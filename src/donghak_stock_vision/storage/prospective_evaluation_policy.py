"""Bounded evaluation planning, independent of unmerged outcome-binding code.

A retained policy pin fixes intent; neither a local declaration time nor a policy
can authenticate a prediction or confer prospective evidence eligibility.
"""

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp

MAX_CASES = 256
MAX_BYTES = 32 * 1024


def validate_policy(owner: FrozenJSON, trusted_pin: str) -> None:
    """Verify a separately retained policy, never accept performance summary JSON."""
    require(type(owner) is FrozenJSON, "evaluation_policy_owner_required")
    require(len(owner.payload_json.encode()) <= MAX_BYTES, "evaluation_policy_byte_budget")
    require(owner.identifier == sha(trusted_pin), "evaluation_policy_pin_mismatch")
    d = owner.to_dict()
    require(
        set(d)
        == {
            "version",
            "mode",
            "declared_at",
            "cohort_pin",
            "horizon_definition_pin",
            "expected_prediction_ids",
            "minimum_samples",
            "minimum_coverage",
            "metric",
            "baselines",
            "uncertainty",
        },
        "evaluation_policy_fields",
    )
    require(d["version"] == "prospective_evaluation_policy_v1", "evaluation_policy_version")
    require(d["mode"] in {"offline_synthetic", "genuine_prospective"}, "evaluation_evidence_mode")
    require(timestamp(d["declared_at"]).isoformat() == d["declared_at"], "evaluation_policy_utc")
    sha(d["cohort_pin"])
    sha(d["horizon_definition_pin"])
    cases = d["expected_prediction_ids"]
    require(type(cases) is list and 1 <= len(cases) <= MAX_CASES, "evaluation_case_budget")
    for case in cases:
        sha(case)
    require(cases == sorted(set(cases)), "evaluation_cases_canonical_unique")
    require(
        type(d["minimum_samples"]) is int and 1 <= d["minimum_samples"] <= len(cases),
        "evaluation_minimum_samples",
    )
    coverage = d["minimum_coverage"]
    require(
        type(coverage) is dict and set(coverage) == {"numerator", "denominator"},
        "evaluation_coverage_fraction",
    )
    n, den = coverage["numerator"], coverage["denominator"]
    require(
        type(n) is int and type(den) is int and 1 <= n <= den <= 10000,
        "evaluation_coverage_bounds",
    )
    require(d["metric"] == "strict_directional_accuracy_v1", "evaluation_metric")
    require(d["baselines"] == ["always_up_v1", "always_down_v1"], "evaluation_baselines")
    require(
        d["uncertainty"] == "unavailable_until_dependence_review_v1",
        "evaluation_uncertainty_requires_independent_methodology",
    )


def requirements(owner: FrozenJSON, trusted_pin: str) -> FrozenJSON:
    """Expose planning constraints without loading observations or claiming results."""
    validate_policy(owner, trusted_pin)
    d = owner.to_dict()
    return FrozenJSON.freeze(
        {
            "version": "prospective_evaluation_requirements_v1",
            "policy_pin": trusted_pin,
            "policy": d,
            "expected_count": len(d["expected_prediction_ids"]),
            "coverage_denominator": "all_predeclared_prediction_ids",
            "eligible_pair_requirement": "original_owner_semantic_replay_and_mode_eligibility",
            "missing_outcome_treatment": "excluded_from_metrics_retained_in_coverage",
            "independent_declaration_time_verified": False,
            "pair_evidence_verified": False,
            "performance_available": False,
            "metrics": None,
            "execution_authorized": False,
        }
    )
