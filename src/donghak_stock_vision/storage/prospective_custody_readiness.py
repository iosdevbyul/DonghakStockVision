"""Local replay and custody gaps; no external timestamp or custody authentication."""

from datetime import datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.prediction_binding import bind_outcome
from donghak_stock_vision.storage.prospective_evaluation_consumer import (
    EvaluationSources,
    prepare_evaluation,
)
from donghak_stock_vision.storage.prospective_evaluation_result import MAX_BYTES

GAPS = (
    "missing_independent_policy_predeclaration_timestamp",
    "missing_independent_prediction_creation_time_evidence",
    "unauthenticated_local_timestamps",
    "missing_independently_retained_custody_references",
    "future_observation_provenance_not_independently_authenticated",
)
CHRONOLOGY_REASONS = frozenset(
    {
        "future_observation_chronology_mismatch",
        "future_evidence_after_as_of",
        "observation_precedes_frozen_gate",
        "observation_not_available_by_deadline",
        "observation_after_frozen_deadline",
    }
)


def audit_custody(source: EvaluationSources, as_of: datetime) -> FrozenJSON:
    """Read original owners; inspect local chronology without certifying custody.

    No supplied receipt/status/hash can remove a gap: there is no independent
    authentication adapter in this contract. Existing consumer restrictions apply.
    """
    require(type(source) is EvaluationSources, "unsupported_evidence_owner_type")
    at = timestamp(as_of)
    preparation = prepare_evaluation(source, at)
    d = preparation.to_dict()
    originals = {row["prediction_id"]: row for row in d["result"]["cases"]}
    rows = []
    reason_counts = dict.fromkeys(GAPS, 0)
    counts = {
        "locally_verified_predictions": len(originals),
        "locally_verified_future_pairs": 0,
        "future_pending": 0,
        "future_unavailable": 0,
        "future_invalid": 0,
        "independently_authenticated_cases": 0,
    }
    horizon = source.horizon.to_dict()
    for case in sorted(source.cases, key=lambda c: c.prediction.prediction_id):
        original = originals[case.prediction.prediction_id]
        binding = bind_outcome(case.prediction, at, case.future)
        b = binding.to_dict()
        mapped = "evaluated" if b["status"] == "verified" else b["status"]
        require(
            mapped == original["state"]
            and b["reason"]
            == (
                "original_prediction_and_future_observation_bound"
                if mapped == "evaluated"
                else original["reason"]
            ),
            "custody_source_changed_during_replay",
        )
        observation = None
        if b["status"] == "verified":
            require(
                b["future_observation_id"] == original["outcome_id"]
                and b["future_resolution_pin"] == original["outcome_evidence_pin"],
                "custody_source_changed_during_replay",
            )
            observation = b["outcome"]["observation"]
            counts["locally_verified_future_pairs"] += 1
        else:
            counts["future_" + b["status"]] += 1
        reasons = list(GAPS)
        if observation is None:
            reasons.append("missing_or_unverifiable_future_observation_provenance")
        if b["reason"] in CHRONOLOGY_REASONS:
            reasons.append("inconsistent_chronology")
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        rows.append(
            {
                "prediction_id": case.prediction.prediction_id,
                "prediction_entry_pin": case.prediction.entry_pin,
                "original_request_pin": case.prediction.request_pin,
                "supplied_future_request_pin": (
                    None if case.future is None else sha(case.future.request_pin)
                ),
                "original_prediction_policy_pin": case.prediction.policy_pin,
                "journal_head": case.prediction.journal_head,
                "recorded_at": original["prediction_recorded_at"],
                "target_trading_date": horizon["target_trading_date"],
                "evaluate_not_before": horizon["evaluate_not_before"],
                "outcome_deadline": horizon["outcome_deadline"],
                "local_future_timestamps": None
                if observation is None
                else {
                    key: observation[key]
                    for key in ("request_started_at", "local_capture_at", "local_ingested_at")
                },
                "future_observation_id": original["outcome_id"],
                "future_verification_pin": original["outcome_evidence_pin"],
                "binding_pin": binding.identifier,
                "local_future_state": b["status"],
                "local_future_reason": b["reason"],
                "prediction_locally_replayable": True,
                "future_locally_replayable": observation is not None,
                "independently_authenticated_chronology": False,
                "custody_state": "unresolved",
                "reasons": sorted(reasons),
            }
        )
    report = FrozenJSON.freeze(
        {
            "version": "prospective_custody_readiness_v1",
            "as_of": at.isoformat(),
            "state": "unresolved",
            "scope": "local_replay_and_independent_custody_gap_inspection",
            "preparation_pin": preparation.identifier,
            "references_pin": d["references_pin"],
            "policy_pin": source.policy_pin,
            "cohort_pin": source.cohort_pin,
            "horizon_pin": source.horizon_pin,
            "policy_declared_at": source.policy.to_dict()["declared_at"],
            "timestamp_assurance": "local_clock_consistency_only_not_independent_authentication",
            "counts": counts,
            "reason_counts": dict(sorted(reason_counts.items())),
            "cases": rows,
            "independently_authenticated_chronology": False,
            "independent_custody_references_verified": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "performance_approved": False,
            "execution_authorized": False,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_BYTES, "custody_report_byte_budget")
    return report


def verify_custody(
    report: FrozenJSON,
    trusted_report_pin: str,
    trusted_references_pin: str,
    source: EvaluationSources,
    as_of: datetime,
) -> None:
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_BYTES,
        "custody_report_byte_budget",
    )
    require(report.identifier == sha(trusted_report_pin), "custody_report_pin_mismatch")
    require(
        report.to_dict().get("version") == "prospective_custody_readiness_v1",
        "custody_report_version",
    )
    replay = audit_custody(source, as_of)
    require(
        replay.to_dict()["references_pin"] == sha(trusted_references_pin),
        "custody_reference_pin_mismatch",
    )
    require(replay == report, "custody_semantic_mismatch")
