"""Transport integrity only; no evidence replay, evaluator, policy or approval."""

import re
from datetime import datetime
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, utc

MAX_CASES = 256
MAX_BYTES = 256 * 1024
STATES = ("pending", "unavailable", "invalid", "evaluated")
METRICS = (
    "evaluation_coverage",
    "directional_accuracy",
    "always_up_baseline",
    "always_down_baseline",
    "uncertainty",
)


def _bounded(owner: FrozenJSON) -> dict[str, Any]:
    require(type(owner) is FrozenJSON, "result_frozen_owner_required")
    require(len(owner.payload_json.encode()) <= MAX_BYTES, "result_byte_budget")
    return owner.to_dict()


def _instant(value: Any) -> datetime:
    require(type(value) is str and utc(value).isoformat() == value, "result_canonical_utc_required")
    return utc(value)


def validate_structure(owner: FrozenJSON) -> None:
    """Validate declared structure, never independently authenticate its contents.

    Even evaluated records have null metrics and all eligibility/approval flags
    false. Counts are reconstructed from declared case states, not market evidence.
    """
    d = fields(
        _bounded(owner),
        "version state evaluated_at policy_id policy_pin cohort_id cohort_pin "
        "provenance counts cases metrics genuine_forward_evidence pit_qualified "
        "performance_approved execution_authorized",
    )
    require(d["version"] == "prospective_evaluation_result_v1", "result_version")
    require(type(d["state"]) is str and d["state"] in STATES, "result_state")
    evaluated_at = _instant(d["evaluated_at"])
    for name in ("policy_id", "policy_pin", "cohort_id", "cohort_pin"):
        sha(d[name])
    provenance = fields(d["provenance"], "evidence_class verification")
    require(
        type(provenance["evidence_class"]) is str
        and provenance["evidence_class"] in {"synthetic_offline", "unverified_prospective"},
        "result_unverified_evidence_class",
    )
    require(
        provenance["verification"] == "not_performed",
        "result_provenance_verification_not_implemented",
    )
    for name in (
        "genuine_forward_evidence",
        "pit_qualified",
        "performance_approved",
        "execution_authorized",
    ):
        require(d[name] is False, "result_approval_forbidden")
    metrics = fields(d["metrics"], " ".join(METRICS))
    require(
        all(value is None for value in metrics.values()),
        "result_metrics_require_independent_verification",
    )
    counts = fields(
        d["counts"],
        "total_declared_predictions evaluated_samples pending_samples "
        "unavailable_samples invalid_samples",
    )
    require(
        all(type(n) is int and 0 <= n <= MAX_CASES for n in counts.values()),
        "result_invalid_counts",
    )
    cases = d["cases"]
    require(type(cases) is list and 1 <= len(cases) <= MAX_CASES, "result_case_budget")
    require(counts["total_declared_predictions"] == len(cases), "result_declared_count_mismatch")
    seen: dict[str, set[str]] = {
        key: set()
        for key in (
            "prediction_id",
            "prediction_evidence_pin",
            "outcome_id",
            "outcome_evidence_pin",
        )
    }
    actual = dict.fromkeys(STATES, 0)
    ordered = []
    for raw in cases:
        case = fields(
            raw,
            "prediction_id prediction_evidence_pin outcome_id outcome_evidence_pin "
            "source_id prediction_recorded_at outcome_observed_at state reason",
        )
        state = case["state"]
        require(type(state) is str and state in STATES, "result_case_state")
        actual[state] += 1
        require(
            type(case["reason"]) is str
            and re.fullmatch(r"[a-z][a-z0-9_]{0,119}", case["reason"]) is not None,
            "result_reason_code",
        )
        sha(case["source_id"])
        recorded = _instant(case["prediction_recorded_at"])
        require(recorded <= evaluated_at, "result_prediction_after_evaluation")
        has_outcome = case["outcome_id"] is not None
        require(
            has_outcome
            == (case["outcome_evidence_pin"] is not None)
            == (case["outcome_observed_at"] is not None),
            "result_partial_outcome_reference",
        )
        require(state != "evaluated" or has_outcome, "result_evaluated_outcome_required")
        require(state != "pending" or not has_outcome, "result_pending_outcome_contradiction")
        if has_outcome:
            require(
                recorded < _instant(case["outcome_observed_at"]) <= evaluated_at,
                "result_outcome_chronology",
            )
        for name, values in seen.items():
            if name.startswith("outcome") and not has_outcome:
                continue
            value = sha(case[name])
            require(value not in values, "result_duplicate_identifier")
            values.add(value)
        ordered.append(case["prediction_id"])
    require(ordered == sorted(ordered), "result_case_order")
    for state in STATES:
        require(counts[state + "_samples"] == actual[state], "result_case_count_mismatch")
    expected = next((s for s in ("invalid", "pending", "unavailable") if actual[s]), "evaluated")
    require(d["state"] == expected, "result_state_count_contradiction")


def verify_integrity(
    owner: FrozenJSON,
    trusted_result_pin: str,
    references: FrozenJSON,
    trusted_references_pin: str,
) -> None:
    """Check retained result/reference pins, not evidence authenticity or PIT safety.

    Reference owners must have been independently retained by the caller. Deriving
    both trusted pins from a received result establishes no independent trust.
    """
    validate_structure(owner)
    require(owner.identifier == sha(trusted_result_pin), "result_pin_mismatch")
    expected = _bounded(references)
    require(references.identifier == sha(trusted_references_pin), "result_reference_pin_mismatch")
    d = owner.to_dict()
    case_keys = (
        "prediction_id",
        "prediction_evidence_pin",
        "outcome_id",
        "outcome_evidence_pin",
        "source_id",
        "prediction_recorded_at",
        "outcome_observed_at",
    )
    require(
        expected
        == {
            "version": "prospective_evaluation_result_references_v1",
            "policy_id": d["policy_id"],
            "policy_pin": d["policy_pin"],
            "cohort_id": d["cohort_id"],
            "cohort_pin": d["cohort_pin"],
            "evaluated_at": d["evaluated_at"],
            "provenance": d["provenance"],
            "cases": [{key: case[key] for key in case_keys} for case in d["cases"]],
        },
        "result_evidence_references_mismatch",
    )
