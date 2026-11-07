"""Read-only bridge: original capture facts and unresolved prospective trust gaps."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.storage.capture_custody_ack import CaptureCustodyAcknowledgement
from donghak_stock_vision.storage.forward_capture import verify_capture
from donghak_stock_vision.storage.forward_custody import (
    ForwardOperationsConfig,
    ReturnedCustodyEvidence,
    assess_forward_operations,
)
from donghak_stock_vision.storage.prospective_anchor_governance import AnchorSnapshot, review_anchor
from donghak_stock_vision.storage.prospective_custody_readiness import audit_custody
from donghak_stock_vision.storage.prospective_evaluation_consumer import EvaluationSources

VERSION = "prospective_operational_evidence_readiness_v1"
MAX_REPORT = 256 * 1024
_NO_RETURNED = ReturnedCustodyEvidence()
GAPS = (
    "independent_policy_predeclaration_evidence_unverified",
    "independent_prediction_existence_time_evidence_unverified",
    "authenticated_external_custody_acknowledgement_unverified",
    "independent_issuer_and_key_authority_unverified",
    "historical_revocation_evidence_unverified",
    "authenticated_clock_assurance_unverified",
    "data_source_rights_not_independently_verified_by_this_assessment",
    "operational_database_completion_not_assessed",
)


@dataclass(frozen=True)
class CaptureSource:
    """References to existing owners, not a new evidence/authorization format."""

    root: Path
    plan: ForwardCapturePlan
    plan_pin: str
    attempt: str
    capture_pin: str
    sequence: int = 0


def assess_operational_evidence(
    capture: CaptureSource,
    as_of: datetime,
    config: ForwardOperationsConfig,
    config_pin: str,
    *,
    returned: ReturnedCustodyEvidence = _NO_RETURNED,
    source: EvaluationSources | None = None,
    source_references_pin: str | None = None,
    anchor: AnchorSnapshot | None = None,
    expected_issuer: str | None = None,
    expected_key_id: str | None = None,
) -> FrozenJSON:
    """Reconstruct facts; no supplied approval booleans, converted owners or credentials.

    One capture/response and at most 32 original predictions. Source/custody replay
    is repeated at the end to detect observable changes, not to promise an atomic
    multi-file snapshot. Existing protected partition and future-gate guards apply.
    """
    require(type(capture) is CaptureSource, "operational_original_capture_owner_required")
    require(type(capture.plan) is ForwardCapturePlan, "operational_original_plan_required")
    capture.plan.__post_init__()  # Partition restrictions before archive I/O.
    require(capture.plan.document.identifier == sha(capture.plan_pin), "operational_plan_pin")
    require(type(config) is ForwardOperationsConfig, "operational_config_owner_required")
    config.__post_init__()
    require(config.document.identifier == sha(config_pin), "operational_config_pin")
    require(type(returned) is ReturnedCustodyEvidence, "operational_returned_owner_required")
    returned.__post_init__()
    require(
        type(capture.sequence) is int and 0 <= capture.sequence < capture.plan.max_requests,
        "operational_response_sequence",
    )
    require((source is None) == (source_references_pin is None), "operational_source_pin_required")
    at = utc(as_of)
    custody = None if source is None else audit_custody(source, at)
    if custody is not None:
        require(
            custody.to_dict()["references_pin"] == sha(source_references_pin),
            "operational_source_references_pin",
        )
    require(
        (anchor is None) == (expected_issuer is None and expected_key_id is None),
        "operational_anchor_identity_required",
    )
    governance = None
    if anchor is not None:
        require(type(anchor) is AnchorSnapshot, "operational_anchor_owner_required")
        require(
            type(expected_issuer) is str and type(expected_key_id) is str,
            "operational_anchor_identity_required",
        )
        governance = review_anchor(
            anchor.owner, anchor.pin, str(expected_issuer), str(expected_key_id), at
        )
    plan = capture.plan
    compatible = plan.observation_scope is None and plan.full_market_scope is None
    linked_ids: set[str] = set()
    if source is not None:
        require(compatible, "operational_scope_cannot_map_to_offline_prediction_contract")
        # Reject an unrelated/future archive before reading its payload. Only the
        # original outcome binder may resolve future evidence under its gate.
        for case in source.cases:
            if case.prediction.capture_root.resolve() == capture.root.resolve() and any(
                item["plan_pin"] == capture.plan_pin
                and item["capture_pin"] == capture.capture_pin
                and item["attempt"] == capture.attempt
                for item in case.prediction.request.to_dict()["sources"]
            ):
                linked_ids.add(case.prediction.prediction_id)
        require(bool(linked_ids), "capture_not_original_prediction_input")
    owner = verify_capture(capture.root, plan, capture.attempt, capture.capture_pin)
    doc = owner.to_dict()
    require(capture.sequence < len(doc["responses"]), "operational_response_missing")
    response = doc["responses"][capture.sequence]
    metadata = response["metadata"]
    require(utc(metadata["response_completed_at"]) <= at, "operational_capture_after_as_of")

    # Forward custody v1 indexes research partition/split keys absent in v2/v3.
    # Do not fabricate those keys or reinterpret operational captures as predictions.
    def operations() -> FrozenJSON | None:
        return (
            assess_forward_operations(
                capture.root,
                plan,
                capture.plan_pin,
                capture.attempt,
                capture.capture_pin,
                sequence=capture.sequence,
                config=config,
                returned=returned,
            )
            if compatible
            else None
        )

    local = operations()
    reasons = set(GAPS)
    if local is None:
        reasons.add("capture_custody_v1_requires_research_scope_absent_in_capture_v2_v3")
        reasons.add("returned_custody_evidence_not_inspected_for_unsupported_scope")
    else:
        reasons.update(
            "credential_presence_not_assessed" if code == "missing_krx_api_key" else code
            for code in local.to_dict()["blockers"]
        )
    if anchor is None:
        reasons.add("trust_anchor_owner_not_supplied")
    else:
        assert governance is not None
        reasons.update(governance.to_dict()["reasons"])
    cases = []
    if source is None:
        reasons.update(
            (
                "original_prediction_provenance_not_supplied",
                "future_observation_provenance_unavailable",
            )
        )
    else:
        assert custody is not None
        require(compatible, "operational_scope_cannot_map_to_offline_prediction_contract")
        require(
            response["parsed_bars"] is not None
            and sum(r["parsed_bars"] is not None for r in doc["responses"]) == 1,
            "capture_response_not_original_prediction_input",
        )
        for case, row in zip(
            sorted(source.cases, key=lambda c: c.prediction.prediction_id),
            custody.to_dict()["cases"],
            strict=True,
        ):
            linked = case.prediction.prediction_id in linked_ids
            cases.append({**row, "capture_is_original_prediction_input": linked})
            reasons.update(row["reasons"])
            if not linked:
                reasons.add("capture_not_bound_to_this_prediction")
        require(
            any(row["capture_is_original_prediction_input"] for row in cases),
            "capture_not_original_prediction_input",
        )
    reasons.add("capture_manifest_has_no_ingestion_completion_timestamp")
    reasons.add(
        "synthetic_capture_not_genuine_forward_evidence"
        if plan.mode == "offline_fixture"
        else "provider_retrieval_does_not_establish_publication_time_or_prospective_eligibility"
    )
    refs = FrozenJSON.freeze(
        {
            "capture_pin": capture.capture_pin,
            "plan_pin": capture.plan_pin,
            "attempt": capture.attempt,
            "sequence": capture.sequence,
            "config_pin": config_pin,
            "source_references_pin": source_references_pin,
            "anchor_pin": None if anchor is None else anchor.pin,
            "expected_issuer": expected_issuer,
            "expected_key_id": expected_key_id,
            "returned": {
                name: {
                    "content_digest": None if value is None else sha256(value).hexdigest(),
                    "retained_pin": pin,
                }
                for name, value, pin in (
                    ("acknowledgement", returned.acknowledgement, returned.acknowledgement_pin),
                    ("readback", returned.readback, returned.readback_pin),
                )
            },
        }
    )
    local_doc = {} if local is None else local.to_dict()
    asserted_at = None
    acknowledgement = local_doc.get("supplied_external_receipt")
    if acknowledgement is not None and acknowledgement["content_consistent"]:
        assert returned.acknowledgement is not None
        asserted_at = utc(
            CaptureCustodyAcknowledgement.model_validate_json(returned.acknowledgement).acceptedAt
        )
        if asserted_at > at or asserted_at < utc(metadata["response_completed_at"]):
            reasons.add("issuer_asserted_custody_chronology_inconsistent")
    report = FrozenJSON.freeze(
        {
            "version": VERSION,
            "as_of": at.isoformat(),
            "state": "unresolved",
            "references_pin": refs.identifier,
            "references": refs.to_dict(),
            "capture_id": doc["capture_id"],
            "origin": doc["origin"],
            "origin_assurance": "declared_capture_mode_not_independently_authenticated",
            "market": plan.market,
            "trading_date": plan.day.isoformat(),
            "capture_plan_version": plan.document.to_dict()["version"],
            "capture_replayed": True,
            "response_parse_error": response["parse_error"],
            "parsed_observation_count": len(response["parsed_bars"] or []),
            "local_times": {
                "request_started_at": utc(metadata["request_started_at"]).isoformat(),
                "response_completed_at": utc(metadata["response_completed_at"]).isoformat(),
                "receipt_collection_at": utc(response["receipt"]["collectedAt"]).isoformat(),
                "ingestion_completed_at": None,
            },
            "issuer_asserted_custody_at": None if asserted_at is None else asserted_at.isoformat(),
            "publication_at": None,
            "independently_authenticated_at": None,
            "operational_persistence_verified": False,
            "credential_presence_assessed": False,
            "capture_custody_contract_compatible": compatible,
            "operations_report_pin": None if local is None else local.identifier,
            "custody": {
                k: local_doc.get(k)
                for k in (
                    "original_export_verified",
                    "binding_pin",
                    "local_export_readback",
                    "supplied_external_receipt",
                    "supplied_external_readback",
                )
            },
            "governance_report_pin": None if governance is None else governance.identifier,
            "prospective_custody_report_pin": None if custody is None else custody.identifier,
            "policy_pin": None if source is None else source.policy_pin,
            "cohort_pin": None if source is None else source.cohort_pin,
            "horizon_pin": None if source is None else source.horizon_pin,
            "declared_predictions": len(cases),
            "cases": cases,
            "reason_codes": sorted(reasons),
            "issuer_independence_verified": False,
            "independently_authenticated_chronology": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "prospective_performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
            "network_requests": 0,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_REPORT, "operational_report_byte_budget")
    require(
        verify_capture(capture.root, plan, capture.attempt, capture.capture_pin) == owner
        and operations() == local,
        "operational_capture_changed_during_assessment",
    )
    if source is not None:
        require(
            audit_custody(source, at) == custody, "operational_sources_changed_during_assessment"
        )
    return report


def verify_operational_evidence(
    report: FrozenJSON,
    report_pin: str,
    references_pin: str,
    capture: CaptureSource,
    as_of: datetime,
    config: ForwardOperationsConfig,
    config_pin: str,
    *,
    returned: ReturnedCustodyEvidence = _NO_RETURNED,
    source: EvaluationSources | None = None,
    source_references_pin: str | None = None,
    anchor: AnchorSnapshot | None = None,
    expected_issuer: str | None = None,
    expected_key_id: str | None = None,
) -> None:
    """A rehashed summary is insufficient: reconstruct from the original owners."""
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_REPORT,
        "operational_report_byte_budget",
    )
    require(report.identifier == sha(report_pin), "operational_report_pin")
    require(report.to_dict().get("version") == VERSION, "operational_report_version")
    replay = assess_operational_evidence(
        capture,
        as_of,
        config,
        config_pin,
        returned=returned,
        source=source,
        source_references_pin=source_references_pin,
        anchor=anchor,
        expected_issuer=expected_issuer,
        expected_key_id=expected_key_id,
    )
    require(replay.to_dict()["references_pin"] == sha(references_pin), "operational_reference_pin")
    require(replay == report, "operational_semantic_mismatch")
