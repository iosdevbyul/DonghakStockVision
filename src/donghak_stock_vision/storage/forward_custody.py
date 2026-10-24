"""Offline operational assessment and receipt staging over existing custody contracts.

No transport factory, SDK client, submission, or new custody database is provided.
Operator reference strings and consistent local bytes never authenticate authority.
"""

import hashlib
import json
import sqlite3
import stat
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.capture_custody_ack import inspect_custody_acknowledgement
from donghak_stock_vision.storage.capture_custody_idempotency import (
    prepare_custody_submission_identity,
)
from donghak_stock_vision.storage.capture_custody_outbox import enqueue_capture_receipt
from donghak_stock_vision.storage.capture_custody_readback import verify_custody_readback
from donghak_stock_vision.storage.capture_evidence_sink import (
    export_capture_receipt,
    verify_exported_capture_receipt,
)
from donghak_stock_vision.storage.forward_capture import DB, _connection, _writer, verify_capture

EXPORTS = "custody-exports"


@dataclass(frozen=True)
class ForwardOperationsConfig:
    permitted_interval_seconds: int | None = None
    frequency_reference: str | None = None
    custody_endpoint_reference: str | None = None
    custodian_id: str | None = None
    retention_reference: str | None = None
    backup_reference: str | None = None

    def __post_init__(self) -> None:
        interval = self.permitted_interval_seconds
        require(
            interval is None or (type(interval) is int and 1 <= interval <= 86400),
            "invalid_permitted_interval",
        )
        for name, ref in vars(self).items():
            if name != "permitted_interval_seconds":
                require(
                    ref is None or (type(ref) is str and 0 < len(ref) <= 256),
                    "invalid_operations_reference",
                )

    @property
    def document(self) -> FrozenJSON:
        self.__post_init__()
        return FrozenJSON.freeze(
            {"version": "forward_capture_operations_config_v1", **asdict(self)}
        )

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "ForwardOperationsConfig":
        require(
            set(document) == {"version"} | set(cls.__dataclass_fields__), "operations_config_fields"
        )
        require(
            document["version"] == "forward_capture_operations_config_v1",
            "operations_config_version",
        )
        return cls(**{name: document[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ReturnedCustodyEvidence:
    acknowledgement: bytes | None = None
    acknowledgement_pin: str | None = None
    readback: bytes | None = None
    readback_pin: str | None = None

    def __post_init__(self) -> None:
        for data, pin in (
            (self.acknowledgement, self.acknowledgement_pin),
            (self.readback, self.readback_pin),
        ):
            require((data is None) == (pin is None), "returned_evidence_pin_required")
            if data is not None:
                require(type(data) is bytes and len(data) <= 65536, "returned_evidence_byte_budget")
                sha(pin)


_DEFAULT_CONFIG = ForwardOperationsConfig()
_NO_RETURNED_EVIDENCE = ReturnedCustodyEvidence()


@dataclass(frozen=True)
class _SuppliedReadback:
    raw_id: int
    key: str
    payload: bytes | None

    def retrieve(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        require(raw_id == self.raw_id and idempotency_key == self.key, "readback_source_binding")
        return self.payload


def _capture(
    root: Path,
    plan: ForwardCapturePlan,
    plan_pin: str,
    attempt: str,
    capture_pin: str,
    sequence: int,
) -> tuple[FrozenJSON, dict[str, Any]]:
    plan.__post_init__()  # Fail closed on final-test/holdout before disk access.
    require(plan.document.identifier == sha(plan_pin), "independent_plan_pin_mismatch")
    owner = verify_capture(root, plan, attempt, capture_pin)
    rows = owner.to_dict()["responses"]
    require(type(sequence) is int and 0 <= sequence < len(rows), "unknown_response")
    return owner, rows[sequence]


def _private(path: Path, *, directory: bool) -> bool:
    if path.is_symlink() or not path.exists():
        return False
    value = path.stat()
    return (
        stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    ) and stat.S_IMODE(value.st_mode) & 0o077 == 0


def _outbox(connection: sqlite3.Connection, raw_id: int) -> dict[str, Any] | None:
    if (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name='capture_custody_outbox'"
        ).fetchone()
        is None
    ):
        return None
    row = connection.execute(
        "SELECT state,attempt_count,last_blocker,last_attempt_at,acknowledgement_sha256 "
        "FROM capture_custody_outbox WHERE raw_id=?",
        (raw_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(
        zip(
            ("state", "attempt_count", "last_blocker", "last_attempt_at", "acknowledgement_sha256"),
            row,
            strict=True,
        )
    )


def _binding(
    connection: sqlite3.Connection, root: Path, owner: FrozenJSON, response: dict[str, Any]
) -> FrozenJSON:
    raw_id = response["raw_id"]
    require(
        _private(root / EXPORTS, directory=True)
        and _private(root / EXPORTS / f"raw-{raw_id}.json", directory=False)
        and (root / EXPORTS / f"raw-{raw_id}.json").stat().st_size <= 65536,
        "private_export_storage_required",
    )
    identity = prepare_custody_submission_identity(connection, root / EXPORTS, raw_id)
    require(_outbox(connection, raw_id) is not None, "outbox_entry_missing")
    exported = (root / EXPORTS / f"raw-{raw_id}.json").read_bytes()
    require(
        hashlib.sha256(exported).hexdigest() == identity.receipt_sha256,
        "export_changed_during_binding",
    )
    document = owner.to_dict()
    return FrozenJSON.freeze(
        {
            "version": "forward_capture_custody_binding_v1",
            "capture_id": document["capture_id"],
            "capture_manifest_pin": owner.identifier,
            "source_endpoint": response["metadata"]["endpoint"],
            "request_parameters": response["metadata"]["parameters"],
            "plan_pin": digest(document["plan"]),
            "scope": {
                k: document["plan"][k]
                for k in (
                    "provider",
                    "market",
                    "currency",
                    "ticker",
                    "day",
                    "partition",
                    "split_pin",
                    "mode",
                )
            },
            "response_sequence": response["metadata"]["request_index"],
            "raw_id": raw_id,
            "raw_sha256": response["receipt"]["rawSha256"],
            "source_reference": f"raw_pages/{raw_id}/sha256/{response['receipt']['rawSha256']}",
            "response_fingerprint": digest(response),
            "ingestion_fingerprint": digest(document["ingestion"]),
            "export_payload": json.loads(exported),
            "export_sha256": identity.receipt_sha256,
            "idempotency_key": identity.idempotency_key,
            "outbox_raw_id": raw_id,
            "external_submission_id": None,
            "independent_custody_verified": False,
            "publication_authenticated": False,
        }
    )


def stage_forward_custody(
    root: Path,
    plan: ForwardCapturePlan,
    plan_pin: str,
    attempt: str,
    capture_pin: str,
    sequence: int = 0,
) -> FrozenJSON:
    """Only export original receipt bytes and enqueue locally; never process the outbox."""
    _capture(root, plan, plan_pin, attempt, capture_pin, sequence)
    require(
        _private(root, directory=True) and _private(root / DB, directory=False),
        "private_pilot_storage_required",
    )
    with _writer(root) as store:
        owner, response = _capture(root, plan, plan_pin, attempt, capture_pin, sequence)
        destination = root / EXPORTS
        if not destination.exists():
            destination.mkdir(mode=0o700)
        require(_private(destination, directory=True), "private_export_directory_required")
        raw_id = response["raw_id"]
        path = destination / f"raw-{raw_id}.json"
        if path.exists() or path.is_symlink():
            require(
                _private(path, directory=False)
                and path.stat().st_size <= 65536
                and verify_exported_capture_receipt(store.connection, raw_id, destination),
                "existing_export_incomplete_or_mismatched",
            )
        else:
            export_capture_receipt(store.connection, raw_id, destination)
        require(
            verify_exported_capture_receipt(store.connection, raw_id, destination),
            "export_not_verified",
        )
        enqueue_capture_receipt(store.connection, raw_id)
        return _binding(store.connection, root, owner, response)


def verify_forward_custody(
    binding: FrozenJSON,
    binding_pin: str,
    root: Path,
    plan: ForwardCapturePlan,
    plan_pin: str,
    attempt: str,
    capture_pin: str,
    sequence: int = 0,
) -> None:
    owner, response = _capture(root, plan, plan_pin, attempt, capture_pin, sequence)
    require(
        type(binding) is FrozenJSON and binding.identifier == sha(binding_pin),
        "binding_pin_mismatch",
    )
    with closing(_connection(root)) as connection:
        fresh = _binding(connection, root, owner, response)
    require(binding == fresh, "custody_binding_semantic_mismatch")


def assess_forward_operations(
    root: Path,
    plan: ForwardCapturePlan,
    plan_pin: str,
    attempt: str,
    capture_pin: str,
    *,
    sequence: int = 0,
    credential_present: bool = False,
    config: ForwardOperationsConfig = _DEFAULT_CONFIG,
    returned: ReturnedCustodyEvidence = _NO_RETURNED_EVIDENCE,
) -> FrozenJSON:
    """Read-only deterministic assessment; configured strings are never permission proofs."""
    require(
        type(config) is ForwardOperationsConfig and type(returned) is ReturnedCustodyEvidence,
        "original_operations_evidence_owners_required",
    )
    config.__post_init__()
    returned.__post_init__()
    require(type(credential_present) is bool, "credential_presence_boolean_required")
    owner, response = _capture(root, plan, plan_pin, attempt, capture_pin, sequence)
    raw_id = response["raw_id"]
    private_archive = _private(root, directory=True) and _private(root / DB, directory=False)
    destination = root / EXPORTS
    export_path = destination / f"raw-{raw_id}.json"
    binding = None
    local_readback = None
    external_readback = None
    ack = None
    receipt_identity = None
    blockers = list(plan.blockers(credential_present))
    if plan.authorization_reference is None:
        blockers.append("provider_service_authorization_reference_missing")
    if plan.terms_reference is None:
        blockers.append("provider_terms_reference_missing")
    blockers.extend(
        (
            "provider_service_authorization_not_independently_verified",
            "provider_terms_and_frequency_not_independently_verified",
        )
    )
    if plan.mode == "offline_fixture":
        blockers.append("synthetic_capture_not_provider_authorization")
    if not private_archive:
        blockers.append("private_pilot_storage_required")
    frequency_configured = (
        config.frequency_reference is not None and config.permitted_interval_seconds is not None
    )
    frequency_compatible = (
        frequency_configured
        and config.permitted_interval_seconds is not None
        and plan.interval_seconds >= config.permitted_interval_seconds
    )
    if not frequency_configured:
        blockers.append("permitted_frequency_evidence_missing")
    elif not frequency_compatible:
        blockers.append("configured_collection_frequency_exceeds_limit")
    if config.custody_endpoint_reference is None or config.custodian_id is None:
        blockers.append("external_custody_configuration_missing")
    blockers.append("external_custody_availability_unverified")
    if config.retention_reference is None:
        blockers.append("retention_configuration_missing")
    if config.backup_reference is None:
        blockers.append("backup_configuration_missing")
    blockers.extend(("retention_not_independently_verified", "backup_restore_not_verified"))
    with closing(_connection(root)) as connection:
        outbox = _outbox(connection, raw_id)
        private_export = (
            _private(destination, directory=True)
            and _private(export_path, directory=False)
            and export_path.stat().st_size <= 65536
        )
        export_verified = private_export and verify_exported_capture_receipt(
            connection, raw_id, destination
        )
        if not private_export and (destination.exists() or destination.is_symlink()):
            blockers.append("private_export_storage_required")
        if not export_verified:
            blockers.append("custody_export_missing_or_invalid")
        else:
            receipt_identity = prepare_custody_submission_identity(connection, destination, raw_id)
            exported = export_path.read_bytes()
            local_readback = asdict(
                verify_custody_readback(
                    connection,
                    destination,
                    raw_id,
                    transport=_SuppliedReadback(raw_id, receipt_identity.idempotency_key, exported),
                )
            )
            if returned.acknowledgement is not None:
                require(config.custodian_id is not None, "expected_custodian_required")
                review = inspect_custody_acknowledgement(
                    returned.acknowledgement,
                    exported,
                    expected_raw_id=raw_id,
                    expected_custodian_id=config.custodian_id or "",
                    independently_pinned_ack_sha256=returned.acknowledgement_pin or "",
                )
                ack = {
                    **asdict(review),
                    "source_sha256": hashlib.sha256(returned.acknowledgement).hexdigest(),
                    "external_submission_id": None,
                    "custodian_receipt_id": None,
                }
                if review.content_consistent:
                    # Keep issuer-provided IDs only after original content checks.
                    original = json.loads(returned.acknowledgement)
                    ack["custodian_receipt_id"] = original["receiptId"]
                blockers.extend(review.blockers)
            if returned.readback is not None:
                if hashlib.sha256(returned.readback).hexdigest() != returned.readback_pin:
                    blockers.append("returned_readback_independent_pin_mismatch")
                else:
                    external_readback = asdict(
                        verify_custody_readback(
                            connection,
                            destination,
                            raw_id,
                            transport=_SuppliedReadback(
                                raw_id, receipt_identity.idempotency_key, returned.readback
                            ),
                        )
                    )
                    if external_readback["blocker"]:
                        blockers.append(external_readback["blocker"])
            if outbox is not None:
                binding = _binding(connection, root, owner, response)
        if outbox is None:
            blockers.append("custody_outbox_not_staged")
        elif outbox["state"] != "pending":
            blockers.append(f"custody_outbox_{outbox['state']}")
    if returned.acknowledgement is None:
        blockers.append("external_custody_receipt_missing")
    if returned.readback is None:
        blockers.append("external_custody_readback_missing")
    if returned.acknowledgement is not None and ack is None:
        blockers.append("external_receipt_not_inspected_without_verified_export")
    blockers.append("independent_custody_not_authenticated")
    destination_usable = not destination.exists() and not destination.is_symlink()
    if _private(destination, directory=True):
        destination_usable = (not export_path.exists() and not export_path.is_symlink()) or (
            export_verified and private_export
        )
    return FrozenJSON.freeze(
        {
            "version": "forward_capture_operational_readiness_v1",
            "capture_id": owner.to_dict()["capture_id"],
            "capture_manifest_pin": owner.identifier,
            "plan_pin": plan.document.identifier,
            "config": config.document.to_dict(),
            "config_pin": config.document.identifier,
            "scope": {
                k: plan.document.to_dict()[k]
                for k in (
                    "provider",
                    "market",
                    "ticker",
                    "day",
                    "currency",
                    "partition",
                    "split_pin",
                    "mode",
                )
            },
            "status": "blocked_for_provider_collection_and_independent_custody",
            "offline_staging_ready": private_archive and destination_usable,
            "credential_present": credential_present,
            "provider_authorization": {
                "service_endpoint": response["metadata"]["endpoint"],
                "configured_reference": plan.authorization_reference,
                "terms_reference": plan.terms_reference,
                "independently_authenticated": False,
                "frequency_configured": frequency_configured,
                "frequency_locally_compatible": bool(frequency_compatible),
            },
            "budgets": {
                k: plan.document.to_dict()[k]
                for k in (
                    "max_requests",
                    "max_response_bytes",
                    "max_seconds",
                    "interval_seconds",
                    "daily_limit",
                    "concurrency",
                )
            },
            "storage": {
                "archive_private": private_archive,
                "export_private": private_export,
                "archive_bytes": (root / DB).stat().st_size,
                "archive_limit_bytes": 256 * 1024 * 1024,
            },
            "capture_locally_verified": True,
            "original_export_verified": export_verified,
            "binding": binding.to_dict() if binding else None,
            "binding_pin": binding.identifier if binding else None,
            "outbox": outbox,
            "local_export_readback": local_readback,
            "supplied_external_receipt": ack,
            "supplied_external_readback": external_readback,
            "external_submission_id": None,
            "external_custody_available": None,
            "external_custody_configured": config.custody_endpoint_reference is not None
            and config.custodian_id is not None,
            "retention_independently_verified": False,
            "backup_restore_verified": False,
            "independent_custody_verified": False,
            "publication_authenticated": False,
            "pit_qualified": False,
            "inference_authorized": False,
            "network_requests": 0,
            "blockers": list(dict.fromkeys(blockers)),
        }
    )


def verify_operations_report(
    report: FrozenJSON,
    trusted_report_pin: str,
    root: Path,
    plan: ForwardCapturePlan,
    plan_pin: str,
    attempt: str,
    capture_pin: str,
    *,
    sequence: int = 0,
    credential_present: bool = False,
    config: ForwardOperationsConfig = _DEFAULT_CONFIG,
    returned: ReturnedCustodyEvidence = _NO_RETURNED_EVIDENCE,
) -> None:
    require(
        type(report) is FrozenJSON and report.identifier == sha(trusted_report_pin),
        "readiness_report_pin_mismatch",
    )
    fresh = assess_forward_operations(
        root,
        plan,
        plan_pin,
        attempt,
        capture_pin,
        sequence=sequence,
        credential_present=credential_present,
        config=config,
        returned=returned,
    )
    require(report == fresh, "readiness_report_semantic_mismatch")
