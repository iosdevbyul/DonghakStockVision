"""Single-case signed receipt reconciliation with replayed original cohort context."""

from datetime import datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.storage.prediction_binding import inspect_prediction
from donghak_stock_vision.storage.prospective_custody_readiness import audit_custody
from donghak_stock_vision.storage.prospective_evaluation_consumer import EvaluationSources
from donghak_stock_vision.storage.prospective_evaluation_result import MAX_BYTES
from donghak_stock_vision.storage.prospective_evidence_audit import MAX_CASES
from donghak_stock_vision.storage.prospective_external_receipt import ReceiptOwner, verify_receipts


def reconcile_receipts(
    source: EvaluationSources,
    selected_prediction_id: str,
    receipts: tuple[ReceiptOwner, ...],
    anchor: FrozenJSON | None,
    anchor_pin: str | None,
    as_of: datetime,
) -> FrozenJSON:
    """Match exactly one policy/prediction receipt pair; no batch authentication."""
    require(type(source) is EvaluationSources, "reconciliation_original_sources_required")
    sha(selected_prediction_id)
    require(
        type(source.cases) is tuple and 1 <= len(source.cases) <= MAX_CASES,
        "reconciliation_cohort_budget",
    )
    require(type(receipts) is tuple and len(receipts) == 2, "reconciliation_single_case_only")
    require(
        all(type(r) is ReceiptOwner for r in receipts), "reconciliation_receipt_owners_required"
    )
    require(
        {r.role for r in receipts} == {"policy", "prediction"},
        "reconciliation_exact_roles_required",
    )
    selected = [c for c in source.cases if c.prediction.prediction_id == selected_prediction_id]
    require(len(selected) == 1, "reconciliation_selected_membership_mismatch")
    # Full declared membership, scope, horizon, pins and original replay remain authoritative.
    custody = audit_custody(source, as_of)
    original = inspect_prediction(selected[0].prediction)
    prediction = FrozenJSON.freeze(original.to_dict()["prediction"])
    return _reconcile_verified_pair(
        source,
        selected_prediction_id,
        receipts,
        anchor,
        anchor_pin,
        as_of,
        custody=custody,
        prediction=prediction,
        entry_pin=selected[0].prediction.entry_pin,
    )


def _reconcile_verified_pair(
    source: EvaluationSources,
    selected_prediction_id: str,
    receipts: tuple[ReceiptOwner, ...],
    anchor: FrozenJSON | None,
    anchor_pin: str | None,
    as_of: datetime,
    *,
    custody: FrozenJSON,
    prediction: FrozenJSON,
    entry_pin: str,
) -> FrozenJSON:
    """Private assembly only; callers must freshly replay originals in this invocation.

    No public API accepts this context. Public semantic verification always
    reconstructs from original owners; these values confer no persistent trust.
    """
    c = custody.to_dict()
    owners = {r.role: r for r in receipts}
    require(
        owners["policy"].artifact == source.policy
        and owners["policy"].artifact_pin == source.policy_pin,
        "reconciliation_original_policy_mismatch",
    )
    require(
        owners["prediction"].artifact == prediction
        and owners["prediction"].artifact_pin == prediction.identifier,
        "reconciliation_original_prediction_mismatch",
    )
    require(
        prediction.to_dict()["prediction_id"] == selected_prediction_id,
        "reconciliation_original_prediction_identity_mismatch",
    )
    selected_row = next(row for row in c["cases"] if row["prediction_id"] == selected_prediction_id)
    observed = selected_row["local_future_timestamps"]
    outcome_at = None if observed is None else utc(observed["local_capture_at"])
    verification = verify_receipts(
        receipts, anchor, anchor_pin, as_of, outcome_observed_at=outcome_at
    )
    v = verification.to_dict()
    signatures = all(row["signature_verified"] for row in v["receipts"])
    local_order = None
    if signatures:
        signed = {row["role"]: utc(row["signed_issuer_time"]) for row in v["receipts"]}
        recorded = utc(prediction.to_dict()["recorded_at"])
        local_order = utc(source.policy.to_dict()["declared_at"]) <= signed[
            "policy"
        ] < recorded <= signed["prediction"] and (
            outcome_at is None or signed["prediction"] < outcome_at
        )
    invalid = (
        v["state"] == "invalid"
        or local_order is False
        or any(
            r["state"] in {"invalid", "invalid_signature", "artifact_mismatch"}
            for r in v["receipts"]
        )
    )
    report = FrozenJSON.freeze(
        {
            "version": "prospective_receipt_custody_reconciliation_v1",
            "as_of": utc(as_of).isoformat(),
            "state": "invalid" if invalid else "unresolved",
            "scope": "one_selected_prediction_receipt_pair_with_complete_cohort_context",
            "selected_prediction_id": selected_prediction_id,
            "original_policy_pin": source.policy_pin,
            "original_prediction_artifact_pin": prediction.identifier,
            "original_prediction_entry_pin": entry_pin,
            "cohort_pin": source.cohort_pin,
            "horizon_pin": source.horizon_pin,
            "original_references_pin": c["references_pin"],
            "custody_report": c,
            "custody_report_pin": custody.identifier,
            "receipt_verification": v,
            "receipt_verification_pin": verification.identifier,
            "cohort": {
                "declared_predictions": len(source.policy.to_dict()["expected_prediction_ids"]),
                "selected_receipt_cases": 1,
                "other_prediction_receipt_cases_not_examined": len(source.cases) - 1,
                "cohort_wide_receipt_verification_performed": False,
            },
            "findings": {
                "original_source_replay_completed": selected_row["future_locally_replayable"],
                "original_prediction_policy_replay_completed": True,
                "supplied_artifact_owners_match_originals": True,
                "selected_artifact_references_match_originals": signatures,
                "selected_receipt_signatures_verified": signatures,
                "selected_signed_and_local_chronology_consistent": local_order,
                "issuer_independence_verified": False,
                "historical_trust_anchor_validity_verified": False,
                "independently_authenticated_chronology": False,
            },
            "reason": "signed_receipt_conflicts_with_original_local_chronology"
            if local_order is False
            else "receipt_verification_failed"
            if invalid
            else "independent_issuer_clock_and_custody_unresolved",
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "prospective_performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_BYTES, "reconciliation_output_budget")
    return report


def verify_reconciliation(
    report: FrozenJSON,
    trusted_report_pin: str,
    trusted_original_references_pin: str,
    source: EvaluationSources,
    selected_prediction_id: str,
    receipts: tuple[ReceiptOwner, ...],
    anchor: FrozenJSON | None,
    anchor_pin: str | None,
    as_of: datetime,
) -> None:
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_BYTES,
        "reconciliation_output_budget",
    )
    require(report.identifier == sha(trusted_report_pin), "reconciliation_report_pin_mismatch")
    require(
        report.to_dict().get("version") == "prospective_receipt_custody_reconciliation_v1",
        "reconciliation_report_version",
    )
    replay = reconcile_receipts(source, selected_prediction_id, receipts, anchor, anchor_pin, as_of)
    require(
        replay.to_dict()["original_references_pin"] == sha(trusted_original_references_pin),
        "reconciliation_original_references_pin_mismatch",
    )
    require(replay == report, "reconciliation_semantic_mismatch")
