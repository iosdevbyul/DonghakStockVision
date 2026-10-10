"""PIT feature inputs are checked offline and always rejected for execution."""

import json

from donghak_stock_vision.api.execution_gate import ExecutionGateAssessment
from donghak_stock_vision.api.pit_inference_input import inspect_pit_input
from donghak_stock_vision.data.learning import FEATURES, FEATURE_VERSION


def _candidate() -> dict[str, object]:
    return {
        "schemaVersion": "pit_inference_input_v1",
        "ticker": "005930",
        "mode": "point_in_time",
        "dataOrigin": "real",
        "featureVersion": FEATURE_VERSION,
        "featureNames": list(FEATURES),
        "featureValues": [0.0] * len(FEATURES),
        "lastTradingDate": "2026-10-08",
        "asOf": "2026-10-10T11:00:00+09:00",
        "dataCutoff": "2026-10-09T00:00:00+09:00",
        "sourceCutoff": "2026-10-09T09:00:00+09:00",
        "snapshotId": "real-snapshot",
        "inputHash": "a" * 64,
        "qualityFlags": [],
    }


def _check(
    overrides: dict[str, object] | None = None,
    *,
    fake_authorized: bool = False,
) -> tuple[bool, bool, tuple[str, ...]]:
    candidate = _candidate()
    candidate.update(overrides or {})
    gate = ExecutionGateAssessment(True, fake_authorized, fake_authorized, ())
    result = inspect_pit_input(json.dumps(candidate).encode(), gate)
    return result.structurally_valid, result.inference_authorized, result.blockers


def test_valid_candidate_never_runs_inference() -> None:
    valid, authorized, blockers = _check()
    assert valid
    assert not authorized
    assert "operational_execution_not_authorized" in blockers


def test_forged_authorized_gate_still_cannot_execute() -> None:
    valid, authorized, blockers = _check(fake_authorized=True)
    assert valid
    assert not authorized
    assert "independent_input_provenance_not_attested" in blockers


def test_current_day_or_future_data_fails_closed() -> None:
    valid, authorized, blockers = _check({"lastTradingDate": "2026-10-10"})
    assert not valid and not authorized
    assert "same_day_or_future_bar_forbidden" in blockers


def test_wrong_feature_order_and_quality_flags_are_rejected() -> None:
    invalid_names = list(FEATURES)
    invalid_names.reverse()
    valid, _, blockers = _check({"featureNames": invalid_names})
    assert not valid
    assert blockers == ("invalid_pit_input_contract",)
    valid, _, blockers = _check({"qualityFlags": ["missing_prior_bar"]})
    assert not valid
    assert "unapproved_input_quality_flags" in blockers


def test_cutoff_before_complete_daily_bar_fails() -> None:
    valid, _, blockers = _check({"dataCutoff": "2026-10-08T17:00:00+09:00"})
    assert not valid
    assert "pit_cutoff_chronology_invalid" in blockers
