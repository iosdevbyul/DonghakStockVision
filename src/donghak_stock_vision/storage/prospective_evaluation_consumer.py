"""Read-only evaluation preparation from pinned original owners; never performance."""

from dataclasses import dataclass
from datetime import datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.storage.forward_signal import request_contract
from donghak_stock_vision.storage.prediction_binding import bind_outcome, inspect_prediction
from donghak_stock_vision.storage.prospective_evaluation_policy import validate_policy
from donghak_stock_vision.storage.prospective_evaluation_result import (
    MAX_BYTES,
    METRICS,
    validate_structure,
    verify_integrity,
)
from donghak_stock_vision.storage.prospective_evidence_audit import (
    MAX_CASES,
    EvidenceCase,
    audit_evidence,
)
from donghak_stock_vision.storage.prospective_prediction import policy as validate_horizon


@dataclass(frozen=True)
class EvaluationSources:
    policy: FrozenJSON
    policy_pin: str
    cohort: FrozenJSON
    cohort_pin: str
    horizon: FrozenJSON
    horizon_pin: str
    cases: tuple[EvidenceCase, ...]


def prepare_evaluation(source: EvaluationSources, as_of: datetime) -> FrozenJSON:
    """Pin and replay originals; v1 counts remain structural, all metrics null."""
    at = timestamp(as_of)
    validate_policy(source.policy, source.policy_pin)
    p = source.policy.to_dict()
    require(p["mode"] == "offline_synthetic", "independent_prospective_policy_custody_unavailable")
    require(
        type(source.cases) is tuple
        and 1 <= len(source.cases) <= MAX_CASES
        and len(p["expected_prediction_ids"]) <= MAX_CASES,
        "consumer_audit_case_budget",
    )
    require(
        type(source.cohort) is FrozenJSON and len(source.cohort.payload_json.encode()) <= 4096,
        "consumer_cohort_budget",
    )
    require(
        source.cohort.identifier == sha(source.cohort_pin) == p["cohort_pin"],
        "consumer_cohort_pin_mismatch",
    )
    split = ExplicitResearchSplit.from_dict(source.cohort.to_dict())
    require(split.identifier == source.cohort_pin, "consumer_canonical_split_required")
    validate_horizon(source.horizon, source.horizon_pin)
    require(source.horizon_pin == p["horizon_definition_pin"], "consumer_horizon_pin_mismatch")
    require(
        all(type(case) is EvidenceCase for case in source.cases), "consumer_original_cases_required"
    )
    ordered = tuple(sorted(source.cases, key=lambda c: c.prediction.prediction_id))
    ids = [c.prediction.prediction_id for c in ordered]
    require(len(set(ids)) == len(ids), "consumer_duplicate_prediction")
    require(ids == p["expected_prediction_ids"], "consumer_declared_membership_mismatch")
    # Scope guards use existing request validation before any capture replay.
    for case in ordered:
        plans = request_contract(case.prediction.request, case.prediction.request_pin)
        require(
            bool(plans) and plans[0].split_pin == source.cohort_pin,
            "consumer_source_cohort_mismatch",
        )
        require(
            case.prediction.policy_pin == source.horizon_pin,
            "consumer_frozen_horizon_mismatch",
        )
    # The audit owns all aggregate/request byte limits, before any replay below.
    audit = audit_evidence(ordered, at)
    original = []
    context = None
    for case in ordered:
        verified = inspect_prediction(case.prediction).to_dict()
        prediction = verified["prediction"]
        require(prediction["policy_pin"] == source.horizon_pin, "consumer_frozen_horizon_mismatch")
        plans = request_contract(case.prediction.request, case.prediction.request_pin)
        require(
            bool(plans) and plans[0].split_pin == source.cohort_pin,
            "consumer_source_cohort_mismatch",
        )
        current = (plans[0].partition, verified["instrument"])
        require(context is None or current == context, "consumer_incompatible_source_context")
        context = current
        require(
            timestamp(p["declared_at"]) <= timestamp(prediction["recorded_at"]) <= at,
            "consumer_local_predeclaration_chronology",
        )
        original.append(prediction)
    rows = []
    for case, prediction, audit_row in zip(
        ordered, original, audit.to_dict()["cases"], strict=True
    ):
        state = audit_row["status"]
        outcome_id = outcome_pin = outcome_at = None
        if state == "eligible":
            binding = bind_outcome(case.prediction, at, case.future)
            require(
                binding.identifier == audit_row["binding_pin"],
                "consumer_source_changed_during_replay",
            )
            b = binding.to_dict()
            outcome_id, outcome_pin = b["future_observation_id"], b["future_resolution_pin"]
            outcome_at = b["outcome"]["observation"]["local_ingested_at"]
            state = (
                "evaluated"  # Structural declaration only, per the immutable result v1 contract.
            )
        rows.append(
            {
                "prediction_id": case.prediction.prediction_id,
                "prediction_evidence_pin": case.prediction.entry_pin,
                "outcome_id": outcome_id,
                "outcome_evidence_pin": outcome_pin,
                "source_id": case.prediction.request_pin,
                "prediction_recorded_at": prediction["recorded_at"],
                "outcome_observed_at": outcome_at,
                "state": state,
                "reason": audit_row["reason"],
            }
        )
    counts = {
        s + "_samples": sum(row["state"] == s for row in rows)
        for s in ("evaluated", "pending", "unavailable", "invalid")
    }
    state = next(
        (s for s in ("invalid", "pending", "unavailable") if counts[s + "_samples"]), "evaluated"
    )
    result = FrozenJSON.freeze(
        {
            "version": "prospective_evaluation_result_v1",
            "state": state,
            "evaluated_at": at.isoformat(),
            "policy_id": source.policy.identifier,
            "policy_pin": source.policy_pin,
            "cohort_id": split.identifier,
            "cohort_pin": source.cohort_pin,
            "provenance": {"evidence_class": "synthetic_offline", "verification": "not_performed"},
            "counts": {"total_declared_predictions": len(ids), **counts},
            "cases": rows,
            "metrics": dict.fromkeys(METRICS),
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "performance_approved": False,
            "execution_authorized": False,
        }
    )
    validate_structure(result)
    reference_keys = (
        "prediction_id",
        "prediction_evidence_pin",
        "outcome_id",
        "outcome_evidence_pin",
        "source_id",
        "prediction_recorded_at",
        "outcome_observed_at",
    )
    d = result.to_dict()
    references = FrozenJSON.freeze(
        {
            "version": "prospective_evaluation_result_references_v1",
            **{
                key: d[key]
                for key in (
                    "policy_id",
                    "policy_pin",
                    "cohort_id",
                    "cohort_pin",
                    "evaluated_at",
                    "provenance",
                )
            },
            "cases": [{key: row[key] for key in reference_keys} for row in rows],
        }
    )
    verify_integrity(result, result.identifier, references, references.identifier)
    ready, declared = counts["evaluated_samples"], len(p["expected_prediction_ids"])
    coverage = p["minimum_coverage"]
    report = FrozenJSON.freeze(
        {
            "version": "prospective_evaluation_preparation_v1",
            "result": d,
            "result_pin": result.identifier,
            "result_references": references.to_dict(),
            "references_pin": references.identifier,
            "audit_pin": audit.identifier,
            "horizon_pin": source.horizon_pin,
            "diagnostics": {
                "declared_cases": declared,
                "structure_ready_cases": ready,
                "minimum_samples_met": ready >= p["minimum_samples"],
                "minimum_structure_coverage_met": ready * coverage["denominator"]
                >= declared * coverage["numerator"],
                "all_cases_structure_ready": ready == declared,
                "independent_policy_declaration_time_verified": False,
                "performance_available": False,
            },
        }
    )
    require(len(report.payload_json.encode()) <= MAX_BYTES, "consumer_output_budget")
    return report


def verify_preparation(
    report: FrozenJSON,
    trusted_report_pin: str,
    trusted_references_pin: str,
    source: EvaluationSources,
    as_of: datetime,
) -> None:
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_BYTES,
        "consumer_output_budget",
    )
    require(report.identifier == sha(trusted_report_pin), "consumer_report_pin_mismatch")
    require(prepare_evaluation(source, as_of) == report, "consumer_semantic_mismatch")
    d = report.to_dict()
    verify_integrity(
        FrozenJSON.freeze(d["result"]),
        d["result_pin"],
        FrozenJSON.freeze(d["result_references"]),
        trusted_references_pin,
    )
