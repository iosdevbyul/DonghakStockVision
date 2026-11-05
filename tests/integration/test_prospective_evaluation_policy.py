"""Synthetic intent only: a policy cannot authorize or authenticate a result."""

from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.prospective_evaluation_policy import requirements, validate_policy


def policy(**changes: Any) -> FrozenJSON:
    body = {
        "version": "prospective_evaluation_policy_v1",
        "mode": "offline_synthetic",
        "declared_at": "2025-07-10T00:00:00+00:00",
        "cohort_pin": "1" * 64,
        "horizon_definition_pin": "2" * 64,
        "expected_prediction_ids": ["3" * 64, "4" * 64],
        "minimum_samples": 2,
        "minimum_coverage": {"numerator": 1, "denominator": 1},
        "metric": "strict_directional_accuracy_v1",
        "baselines": ["always_up_v1", "always_down_v1"],
        "uncertainty": "unavailable_until_dependence_review_v1",
    }
    return FrozenJSON.freeze({**body, **changes})


@pytest.mark.parametrize("mode", ["offline_synthetic", "genuine_prospective"])
def test_policy_never_creates_evidence_or_performance(mode: str) -> None:
    owner = policy(mode=mode)
    report = requirements(owner, owner.identifier)
    d = report.to_dict()
    assert d["expected_count"] == 2
    assert d["metrics"] is None and not d["performance_available"]
    assert not d["pair_evidence_verified"] and not d["independent_declaration_time_verified"]
    assert not d["execution_authorized"]
    assert d["coverage_denominator"] == "all_predeclared_prediction_ids"
    assert requirements(FrozenJSON(owner.payload_json), owner.identifier) == report


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "unknown"},
        {"mode": "historical_as_forward"},
        {"declared_at": "2025-07-10T09:00:00+09:00"},
        {"cohort_pin": "not-a-pin"},
        {"horizon_definition_pin": ""},
        {"expected_prediction_ids": []},
        {"expected_prediction_ids": ["3" * 64, "3" * 64]},
        {"expected_prediction_ids": ["4" * 64, "3" * 64]},
        {"expected_prediction_ids": [f"{i:064x}" for i in range(257)]},
        {"minimum_samples": 0},
        {"minimum_samples": 3},
        {"minimum_samples": True},
        {"minimum_coverage": {"numerator": 0, "denominator": 1}},
        {"minimum_coverage": {"numerator": 2, "denominator": 1}},
        {"minimum_coverage": {"numerator": 1.0, "denominator": 2}},
        {"metric": "profitability"},
        {"baselines": []},
        {"uncertainty": "iid_wilson"},
        {"performance": {"accuracy": 1}},
    ],
)
def test_invalid_or_permissive_configuration_is_rejected(changes: dict[str, Any]) -> None:
    owner = policy(**changes)
    with pytest.raises(ValueError):
        validate_policy(owner, owner.identifier)


def test_changed_configuration_requires_original_trusted_pin() -> None:
    original = policy()
    changed = policy(minimum_samples=1)
    with pytest.raises(ValueError, match="pin_mismatch"):
        validate_policy(changed, original.identifier)


def test_configuration_byte_budget_before_parsing() -> None:
    owner = policy(unknown="x" * (33 * 1024))
    with pytest.raises(ValueError, match="byte_budget"):
        validate_policy(owner, owner.identifier)
