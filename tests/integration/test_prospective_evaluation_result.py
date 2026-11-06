"""Bounded synthetic transport fixtures, never authentic prospective evidence."""

from typing import Any
from unittest.mock import patch

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.prospective_evaluation_result import (
    MAX_BYTES,
    MAX_CASES,
    METRICS,
    validate_structure,
    verify_integrity,
)


def result(states: tuple[str, ...] = ("evaluated",)) -> FrozenJSON:
    rows = []
    for i, state in enumerate(states):
        rows.append(
            {
                "prediction_id": f"{i + 1:064x}",
                "prediction_evidence_pin": f"{i + 1000:064x}",
                "outcome_id": f"{i + 2000:064x}" if state == "evaluated" else None,
                "outcome_evidence_pin": f"{i + 3000:064x}" if state == "evaluated" else None,
                "source_id": "a" * 64,
                "prediction_recorded_at": "2025-07-10T00:00:00+00:00",
                "outcome_observed_at": "2025-07-12T00:00:00+00:00"
                if state == "evaluated"
                else None,
                "state": state,
                "reason": "synthetic_declared_case",
            }
        )
    counts = {
        state + "_samples": states.count(state)
        for state in ("evaluated", "pending", "unavailable", "invalid")
    }
    overall = next((s for s in ("invalid", "pending", "unavailable") if s in states), "evaluated")
    return FrozenJSON.freeze(
        {
            "version": "prospective_evaluation_result_v1",
            "state": overall,
            "evaluated_at": "2025-07-13T00:00:00+00:00",
            "policy_id": "b" * 64,
            "policy_pin": "c" * 64,
            "cohort_id": "d" * 64,
            "cohort_pin": "e" * 64,
            "provenance": {"evidence_class": "synthetic_offline", "verification": "not_performed"},
            "counts": {"total_declared_predictions": len(states), **counts},
            "cases": rows,
            "metrics": dict.fromkeys(METRICS),
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "performance_approved": False,
            "execution_authorized": False,
        }
    )


def references(owner: FrozenJSON) -> FrozenJSON:
    d = owner.to_dict()
    # Fixture's separately retained transport reference, not a provider trust root.
    return FrozenJSON.freeze(
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
            "cases": [
                {
                    key: case[key]
                    for key in (
                        "prediction_id",
                        "prediction_evidence_pin",
                        "outcome_id",
                        "outcome_evidence_pin",
                        "source_id",
                        "prediction_recorded_at",
                        "outcome_observed_at",
                    )
                }
                for case in d["cases"]
            ],
        }
    )


@pytest.mark.parametrize(
    "states",
    [
        ("pending",),
        ("unavailable",),
        ("invalid",),
        ("evaluated",),
        ("evaluated", "pending", "unavailable", "invalid"),
        ("evaluated", "pending"),
        ("evaluated", "unavailable"),
    ],
)
def test_all_states_and_count_precedence(states: tuple[str, ...]) -> None:
    owner = result(states)
    refs = references(owner)
    with patch("httpx.Client.send", side_effect=AssertionError("no network")):
        validate_structure(owner)
        verify_integrity(owner, owner.identifier, refs, refs.identifier)
    d = owner.to_dict()
    assert all(v is None for v in d["metrics"].values())
    assert d["provenance"]["verification"] == "not_performed"
    assert all(
        d[key] is False
        for key in (
            "genuine_forward_evidence",
            "pit_qualified",
            "performance_approved",
            "execution_authorized",
        )
    )


def test_serialization_and_detached_owner_are_deterministic() -> None:
    owner = result()
    changed = owner.to_dict()
    changed["policy_id"] = "0" * 64
    assert owner.to_dict()["policy_id"] == "b" * 64
    rebuilt = FrozenJSON(owner.payload_json)
    assert rebuilt == owner and rebuilt.identifier == owner.identifier
    assert result() == owner


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("value", [0, False, {"verified": True, "value": 1}])
def test_no_flag_or_value_can_authorize_metrics(metric: str, value: Any) -> None:
    d = result().to_dict()
    d["metrics"][metric] = value
    with pytest.raises(ValueError, match="metrics_require_independent"):
        validate_structure(FrozenJSON.freeze(d))


@pytest.mark.parametrize(
    "field",
    ["genuine_forward_evidence", "pit_qualified", "performance_approved", "execution_authorized"],
)
def test_approval_claims_rejected(field: str) -> None:
    d = result().to_dict()
    d[field] = True
    with pytest.raises(ValueError, match="approval_forbidden"):
        validate_structure(FrozenJSON.freeze(d))


@pytest.mark.parametrize(
    "kind",
    [
        "version",
        "extra",
        "case_extra",
        "metric_extra",
        "count_extra",
        "missing",
        "wrong_state",
        "state_counts",
        "negative",
        "boolean",
        "fraction",
        "count_sum",
        "zero_cases",
        "too_many",
        "duplicates",
        "duplicate_outcome",
        "order",
        "partial_outcome",
        "evaluated_no_outcome",
        "pending_outcome",
        "bad_pin",
        "future_prediction",
        "future_outcome",
        "outcome_before_prediction",
        "naive_time",
        "non_utc",
        "genuine_origin",
        "verified_provenance",
        "bad_reason",
    ],
)
def test_malformed_and_contradictory_records_rejected(kind: str) -> None:
    d = result(("evaluated", "evaluated")).to_dict()
    if kind == "version":
        d["version"] = "prospective_evaluation_result_v999"
    elif kind == "extra":
        d["verified"] = True
    elif kind == "case_extra":
        d["cases"][0]["verified"] = True
    elif kind == "metric_extra":
        d["metrics"]["approved"] = True
    elif kind == "count_extra":
        d["counts"]["approved"] = True
    elif kind == "missing":
        del d["cohort_pin"]
    elif kind == "wrong_state":
        d["state"] = "approved"
    elif kind == "state_counts":
        d["state"] = "pending"
    elif kind == "negative":
        d["counts"]["invalid_samples"] = -1
    elif kind == "boolean":
        d["counts"]["evaluated_samples"] = True
    elif kind == "fraction":
        d["counts"]["evaluated_samples"] = 2.0
    elif kind == "count_sum":
        d["counts"]["evaluated_samples"] = 1
    elif kind == "zero_cases":
        d["cases"] = []
    elif kind == "too_many":
        d = result(("evaluated",) * (MAX_CASES + 1)).to_dict()
    elif kind == "duplicates":
        d["cases"][1]["prediction_id"] = d["cases"][0]["prediction_id"]
    elif kind == "duplicate_outcome":
        d["cases"][1]["outcome_id"] = d["cases"][0]["outcome_id"]
    elif kind == "order":
        d["cases"].reverse()
    elif kind == "partial_outcome":
        d["cases"][0]["outcome_evidence_pin"] = None
    elif kind == "evaluated_no_outcome":
        for key in ("outcome_id", "outcome_evidence_pin", "outcome_observed_at"):
            d["cases"][0][key] = None
    elif kind == "pending_outcome":
        d["cases"][0]["state"] = "pending"
    elif kind == "bad_pin":
        d["policy_pin"] = "unknown"
    elif kind == "future_prediction":
        d["cases"][0]["prediction_recorded_at"] = "2025-07-14T00:00:00+00:00"
    elif kind == "future_outcome":
        d["cases"][0]["outcome_observed_at"] = "2025-07-14T00:00:00+00:00"
    elif kind == "outcome_before_prediction":
        d["cases"][0]["outcome_observed_at"] = "2025-07-09T00:00:00+00:00"
    elif kind == "naive_time":
        d["evaluated_at"] = "2025-07-13T00:00:00"
    elif kind == "non_utc":
        d["evaluated_at"] = "2025-07-13T09:00:00+09:00"
    elif kind == "genuine_origin":
        d["provenance"]["evidence_class"] = "genuine_forward"
    elif kind == "verified_provenance":
        d["provenance"]["verification"] = "independently_verified"
    elif kind == "bad_reason":
        d["cases"][0]["reason"] = "This is not a reason code"
    with pytest.raises(ValueError):
        validate_structure(FrozenJSON.freeze(d))


@pytest.mark.parametrize("key", ["policy_id", "policy_pin", "cohort_id", "cohort_pin"])
def test_rehashed_altered_top_level_references_fail_original_reference_pin(key: str) -> None:
    owner = result()
    refs = references(owner)
    d = owner.to_dict()
    d[key] = "f" * 64
    forged = FrozenJSON.freeze(d)
    validate_structure(forged)  # Hash shape alone cannot reject a plausible substitution.
    with pytest.raises(ValueError, match="references_mismatch"):
        verify_integrity(forged, forged.identifier, refs, refs.identifier)


@pytest.mark.parametrize(
    "key",
    ["prediction_id", "prediction_evidence_pin", "outcome_id", "outcome_evidence_pin", "source_id"],
)
def test_rehashed_altered_case_references_fail_original_reference_pin(key: str) -> None:
    owner = result()
    refs = references(owner)
    d = owner.to_dict()
    d["cases"][0][key] = "f" * 64
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="references_mismatch"):
        verify_integrity(forged, forged.identifier, refs, refs.identifier)


def test_wrong_or_replaced_trusted_pins_rejected() -> None:
    owner = result()
    refs = references(owner)
    with pytest.raises(ValueError, match="result_pin_mismatch"):
        verify_integrity(owner, "0" * 64, refs, refs.identifier)
    with pytest.raises(ValueError, match="reference_pin_mismatch"):
        verify_integrity(owner, owner.identifier, refs, "0" * 64)
    changed = refs.to_dict()
    changed["policy_pin"] = "f" * 64
    forged_refs = FrozenJSON.freeze(changed)
    with pytest.raises(ValueError, match="reference_pin_mismatch"):
        verify_integrity(owner, owner.identifier, forged_refs, refs.identifier)


def test_bounded_contract_and_duplicate_json_keys() -> None:
    d = result().to_dict()
    d["extra"] = "x" * MAX_BYTES
    with pytest.raises(ValueError, match="byte_budget"):
        validate_structure(FrozenJSON.freeze(d))
    with pytest.raises(ValueError, match="duplicate_evidence_key"):
        FrozenJSON('{"version":"one","version":"two"}')


def test_unverified_prospective_is_still_not_authenticated() -> None:
    d = result().to_dict()
    d["provenance"]["evidence_class"] = "unverified_prospective"
    owner = FrozenJSON.freeze(d)
    validate_structure(owner)
    assert not owner.to_dict()["genuine_forward_evidence"]
    assert all(v is None for v in owner.to_dict()["metrics"].values())


def test_maximum_bounded_case_set_is_valid() -> None:
    owner = result(("evaluated",) * MAX_CASES)
    refs = references(owner)
    verify_integrity(owner, owner.identifier, refs, refs.identifier)
    assert owner.to_dict()["counts"]["total_declared_predictions"] == MAX_CASES


def test_validation_has_no_file_or_database_io() -> None:
    owner = result()
    refs = references(owner)
    with (
        patch("builtins.open", side_effect=AssertionError("no file reads or writes")),
        patch("pathlib.Path.open", side_effect=AssertionError("no file reads or writes")),
        patch("sqlite3.connect", side_effect=AssertionError("no database access")),
    ):
        verify_integrity(owner, owner.identifier, refs, refs.identifier)
