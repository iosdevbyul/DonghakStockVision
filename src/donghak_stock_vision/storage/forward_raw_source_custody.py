"""Read-only forward lineage consumer of the original raw-source contract.

No archive repair, staging, SDK, credentials or transport. Separate local reads
are consistency checks, not an atomic snapshot or an independent trust anchor.
"""

from contextlib import closing
from dataclasses import asdict, dataclass, field
from hashlib import sha256

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.capture_custody_s3 import BUCKET
from donghak_stock_vision.storage.capture_raw_source_contract import (
    MAX_RAW_SOURCE_BYTES,
    RawSourceCustodyContract,
    prepare_raw_source_custody,
    verify_raw_source_readback,
)
from donghak_stock_vision.storage.capture_raw_source_s3 import RawSourceS3Observation
from donghak_stock_vision.storage.forward_capture import _connection, identity
from donghak_stock_vision.storage.forward_custody import _capture
from donghak_stock_vision.storage.prospective_operational_readiness import CaptureSource

VERSION = "forward_raw_source_custody_assessment_v1"
MAX_REPORT_BYTES = 64 * 1024


@dataclass(frozen=True)
class ForwardRawSourceLink:
    binding: FrozenJSON
    source: RawSourceCustodyContract = field(repr=False)


def link_forward_raw_source(
    capture: CaptureSource, *, archive_namespace: str
) -> ForwardRawSourceLink:
    """Resolve raw_id only through the verified original manifest and journal.

    Namespace is an operator label retained in the binding, not a namespace
    authenticated by SQLite. Retain the binding pin and original root separately.
    """
    require(type(capture) is CaptureSource, "original_capture_source_required")
    capture.plan.__post_init__()
    require(capture.plan.document.identifier == sha(capture.plan_pin), "original_plan_pin")
    capture_id = identity(capture.plan, capture.attempt)
    # Bound BLOB reads before the existing capture replay, which reads receipts.
    with closing(_connection(capture.root)) as connection:
        rows = connection.execute(
            "SELECT typeof(r.body),length(r.body) FROM forward_responses f "
            "LEFT JOIN raw_pages r ON r.id=f.raw_id WHERE f.capture_id=? LIMIT ?",
            (capture_id, capture.plan.max_requests + 1),
        ).fetchall()
        require(len(rows) <= capture.plan.max_requests, "response_count_budget")
        require(
            all(
                kind == "blob"
                and type(size) is int
                and 0 <= size <= min(MAX_RAW_SOURCE_BYTES, capture.plan.max_response_bytes)
                for kind, size in rows
            ),
            "raw_source_byte_budget_or_type",
        )
    owner, response = _capture(
        capture.root,
        capture.plan,
        capture.plan_pin,
        capture.attempt,
        capture.capture_pin,
        capture.sequence,
    )
    # The original contract performs its receipt/BLOB join in this read transaction.
    with closing(_connection(capture.root)) as connection:
        require(
            connection.execute(
                "SELECT document,pin FROM forward_manifests WHERE capture_id=?", (capture_id,)
            ).fetchone()
            == (owner.payload_json, capture.capture_pin),
            "original_manifest_changed",
        )
        require(
            connection.execute(
                "SELECT plan,attempt FROM forward_intents WHERE capture_id=?", (capture_id,)
            ).fetchone()
            == (capture.plan.document.payload_json, capture.attempt),
            "original_intent_changed",
        )
        require(
            connection.execute(
                "SELECT raw_id,metadata FROM forward_responses WHERE capture_id=? AND sequence=?",
                (capture_id, capture.sequence),
            ).fetchone()
            == (response["raw_id"], FrozenJSON.freeze(response["metadata"]).payload_json),
            "original_response_changed",
        )
        source = prepare_raw_source_custody(
            connection, response["raw_id"], archive_namespace=archive_namespace
        )
        require(
            source.receipt_payload() == response["receipt"]
            and source.run_id == f"{capture_id}:{capture.sequence}"
            and source.byte_length == response["body_bytes"],
            "original_raw_source_binding_mismatch",
        )
    binding = FrozenJSON.freeze(
        {
            "version": "forward_raw_source_link_v1",
            "archive_root": str(capture.root.resolve()),
            "archive_namespace": source.archive_namespace,
            "capture_id": capture_id,
            "capture_manifest_pin": capture.capture_pin,
            "plan_pin": capture.plan_pin,
            "capture_plan_version": capture.plan.document.to_dict()["version"],
            "attempt": capture.attempt,
            "response_sequence": capture.sequence,
            "response_fingerprint": digest(response),
            "raw_id": source.raw_id,
            "raw_sha256": source.raw_sha256,
            "byte_length": source.byte_length,
            "receipt": source.receipt_payload(),
            "receipt_sha256": source.receipt_sha256,
            "receipt_idempotency_key": source.receipt_idempotency_key,
            "source_identifier": source.identifier,
            "source_object_key": source.source_object_key,
            "receipt_object_key": source.receipt_object_key,
        }
    )
    return ForwardRawSourceLink(binding, source)


def observation_document(observation: RawSourceS3Observation) -> FrozenJSON:
    """Canonical, bounded reference to supplied observation; no authority implied."""
    require(type(observation) is RawSourceS3Observation, "original_s3_observation_required")
    require(observation.retain_until.utcoffset() is not None, "observation_retention_timezone")
    doc = FrozenJSON.freeze(
        {**asdict(observation), "retain_until": observation.retain_until.isoformat()}
    )
    require(len(doc.payload_json.encode()) <= MAX_REPORT_BYTES, "observation_byte_budget")
    return doc


@dataclass(frozen=True)
class SuppliedRawSourceEvidence:
    """Untrusted observation plus separately retained exact object reference and pins.

    reference fields: version, source_identifier, bucket, key, version_id. It is
    retained outside this report; a self-consistent reference is not remote proof.
    """

    observation: RawSourceS3Observation | None = None
    observation_pin: str | None = None
    reference: FrozenJSON | None = None
    reference_pin: str | None = None
    readback: bytes | None = field(default=None, repr=False)
    readback_pin: str | None = None

    def __post_init__(self) -> None:
        require(
            (self.observation is None)
            == (self.observation_pin is None)
            == (self.reference is None)
            == (self.reference_pin is None),
            "supplied_object_observation_and_reference_required",
        )
        require((self.readback is None) == (self.readback_pin is None), "raw_readback_pin_required")
        if self.observation is not None:
            doc = observation_document(self.observation)
            require(doc.identifier == sha(self.observation_pin), "observation_pin_mismatch")
            require(type(self.reference) is FrozenJSON, "original_object_reference_required")
            assert self.reference is not None
            require(
                len(self.reference.payload_json.encode()) <= MAX_REPORT_BYTES
                and self.reference.identifier == sha(self.reference_pin),
                "object_reference_pin_mismatch",
            )
        if self.readback is not None:
            require(self.observation is not None, "readback_object_identity_required")
            require(
                type(self.readback) is bytes and len(self.readback) <= MAX_RAW_SOURCE_BYTES,
                "supplied_raw_source_byte_budget",
            )
            require(sha256(self.readback).hexdigest() == sha(self.readback_pin), "raw_readback_pin")


_NO_EXTERNAL = SuppliedRawSourceEvidence()


def assess_forward_raw_source(
    capture: CaptureSource,
    *,
    archive_namespace: str,
    supplied: SuppliedRawSourceEvidence = _NO_EXTERNAL,
    submission_reference: str | None = None,
) -> FrozenJSON:
    """Offline content consistency only; never upload success or historical authority."""
    require(type(supplied) is SuppliedRawSourceEvidence, "original_raw_evidence_required")
    supplied.__post_init__()
    require(
        submission_reference is None
        or (type(submission_reference) is str and 0 < len(submission_reference) <= 256),
        "invalid_submission_reference",
    )
    link = link_forward_raw_source(capture, archive_namespace=archive_namespace)
    source = link.source
    observation = supplied.observation
    readback = None
    if observation is not None:
        # Original observation identity and original production key, no synthesized metadata.
        require(
            type(observation.raw_id) is int
            and type(observation.byte_length) is int
            and observation.source_identifier == source.identifier
            and observation.archive_namespace == source.archive_namespace
            and observation.raw_id == source.raw_id
            and observation.bucket == BUCKET
            and observation.key == source.source_object_key
            and observation.raw_sha256 == source.raw_sha256
            and observation.byte_length == source.byte_length
            and observation.retention_mode == "GOVERNANCE"
            and observation.independent_custody_verified is False
            and observation.inference_authorized is False,
            "supplied_object_source_mismatch",
        )
        # Reuse the original offline VersionId validator, without a transport call.
        verify_raw_source_readback(source, source.body, version_id=observation.version_id)
        assert supplied.reference is not None
        require(
            supplied.reference.to_dict()
            == {
                "version": "forward_raw_source_object_reference_v1",
                "source_identifier": source.identifier,
                "bucket": observation.bucket,
                "key": source.source_object_key,
                "version_id": observation.version_id,
            },
            "supplied_object_reference_mismatch",
        )
        if supplied.readback is not None:
            readback = asdict(
                verify_raw_source_readback(
                    source, supplied.readback, version_id=observation.version_id
                )
            )
    reasons = [
        "remote_object_existence_not_independently_established",
        "external_custodian_independence_not_authenticated",
        "original_publication_time_not_authenticated",
        "prediction_existence_time_not_authenticated",
        "external_upload_success_not_authenticated",
    ]
    if observation is None:
        reasons.append("external_raw_source_metadata_missing")
    if readback is None:
        reasons.append("external_raw_source_readback_missing")
    if capture.plan.mode == "offline_fixture":
        reasons.append("synthetic_capture_not_genuine_forward_evidence")
    report = FrozenJSON.freeze(
        {
            "version": VERSION,
            "binding": link.binding.to_dict(),
            "binding_pin": link.binding.identifier,
            "original_raw_bytes_locally_verified": True,
            "original_capture_manifest_verified": True,
            "original_receipt_identity_verified": True,
            "raw_source_contract_compatible": True,
            "custody_payload_kind": "full_original_http_response",
            "json_receipt_external_custody_assessed": False,
            "submission_reference": submission_reference,
            "external_submission_configured_or_claimed": submission_reference is not None
            or observation is not None,
            "supplied_object_metadata_consistent": observation is not None,
            "supplied_external_readback": readback,
            "observation": None
            if observation is None
            else observation_document(observation).to_dict(),
            "observation_pin": supplied.observation_pin,
            "object_reference": None
            if supplied.reference is None
            else supplied.reference.to_dict(),
            "object_reference_pin": supplied.reference_pin,
            "readback_pin": supplied.readback_pin,
            "remote_object_existence_verified": False,
            "external_upload_verified": False,
            "independent_custody_verified": False,
            "publication_authenticated": False,
            "prediction_existence_time_authenticated": False,
            "issuer_independence_verified": False,
            "independently_authenticated_chronology": False,
            "collection_authorized": False,
            "inference_authorized": False,
            "forward_signal_eligible": False,
            "paper_execution_eligible": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "prospective_performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
            "network_requests": 0,
            "reason_codes": reasons,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_REPORT_BYTES, "raw_assessment_byte_budget")
    return report


def verify_forward_raw_source_assessment(
    report: FrozenJSON,
    report_pin: str,
    capture: CaptureSource,
    *,
    archive_namespace: str,
    supplied: SuppliedRawSourceEvidence = _NO_EXTERNAL,
    submission_reference: str | None = None,
) -> None:
    """Reconstruct using separately retained originals, never report-supplied claims."""
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_REPORT_BYTES,
        "raw_assessment_byte_budget",
    )
    require(report.identifier == sha(report_pin), "raw_assessment_pin")
    require(report.to_dict().get("version") == VERSION, "raw_assessment_version")
    fresh = assess_forward_raw_source(
        capture,
        archive_namespace=archive_namespace,
        supplied=supplied,
        submission_reference=submission_reference,
    )
    require(fresh == report, "raw_assessment_semantic_mismatch")
