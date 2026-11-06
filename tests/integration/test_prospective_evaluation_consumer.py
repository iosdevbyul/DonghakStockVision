"""Synthetic original-owner integration; no actual prospective performance."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.integration.test_prediction_binding import AS_OF, future_capture, make_original
from tests.integration.test_prospective_evaluation_policy import policy
from tests.integration.test_prospective_prediction import RECORDED

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.prospective_evaluation_consumer import (
    EvaluationSources,
    prepare_evaluation,
    verify_preparation,
)
from donghak_stock_vision.storage.prospective_evaluation_result import validate_structure
from donghak_stock_vision.storage.prospective_evidence_audit import EvidenceCase


def sources(root: Path, *, future: bool = True) -> EvaluationSources:
    original = make_original(root)
    plan = ForwardCapturePlan.from_dict(original.request.to_dict()["sources"][0]["plan"])
    assert plan.split is not None
    cohort = FrozenJSON.freeze(plan.split.to_dict())
    from tests.integration.test_prospective_prediction import policy as horizon_policy

    horizon = horizon_policy()
    p = policy(
        cohort_pin=cohort.identifier,
        horizon_definition_pin=horizon.identifier,
        expected_prediction_ids=[original.prediction_id],
        minimum_samples=1,
    )
    f = future_capture(original, root / "future") if future else None
    return EvaluationSources(
        p,
        p.identifier,
        cohort,
        cohort.identifier,
        horizon,
        horizon.identifier,
        (EvidenceCase(original, f),),
    )


def test_valid_original_owner_preparation_and_restart(tmp_path: Path) -> None:
    s = sources(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch("httpx.Client.send", side_effect=AssertionError("no network")):
        report = prepare_evaluation(s, AS_OF)
        d = report.to_dict()
        verify_preparation(report, report.identifier, d["references_pin"], replace(s), AS_OF)
    validate_structure(FrozenJSON.freeze(d["result"]))
    assert d["result"]["state"] == "evaluated"
    assert d["diagnostics"]["all_cases_structure_ready"]
    assert not d["diagnostics"]["performance_available"]
    assert all(v is None for v in d["result"]["metrics"].values())
    assert all(
        d["result"][flag] is False
        for flag in (
            "genuine_forward_evidence",
            "pit_qualified",
            "performance_approved",
            "execution_authorized",
        )
    )
    assert prepare_evaluation(replace(s), AS_OF) == report
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("kind", ["pending", "missing", "tampered", "late", "wrong_date"])
def test_noneligible_evidence_accounting(tmp_path: Path, kind: str) -> None:
    s = sources(tmp_path, future=False)
    at = AS_OF
    case = s.cases[0]
    if kind == "pending":
        at = RECORDED
    elif kind == "tampered":
        f = future_capture(case.prediction, tmp_path / "future")
        with (f.capture_root / DB).open("ab") as stream:
            stream.write(b"modified")
        s = replace(s, cases=(EvidenceCase(case.prediction, f),))
    elif kind == "late":
        at = AS_OF + timedelta(days=4)
        f = future_capture(case.prediction, tmp_path / "future", at=at)
        s = replace(s, cases=(EvidenceCase(case.prediction, f),))
        at += timedelta(seconds=4)
    elif kind == "wrong_date":
        from datetime import date

        f = future_capture(
            case.prediction,
            tmp_path / "future",
            day=date(2025, 7, 14),
            at=AS_OF + timedelta(days=4),
        )
        s = replace(s, cases=(EvidenceCase(case.prediction, f),))
    d = prepare_evaluation(s, at).to_dict()
    state = (
        "pending"
        if kind == "pending"
        else "invalid"
        if kind in {"tampered", "wrong_date"}
        else "unavailable"
    )
    assert d["result"]["state"] == state
    assert d["result"]["counts"][state + "_samples"] == 1
    assert d["diagnostics"]["declared_cases"] == 1
    assert not d["diagnostics"]["minimum_structure_coverage_met"]


@pytest.mark.parametrize(
    "kind",
    [
        "policy_pin",
        "cohort_pin",
        "horizon_pin",
        "missing_case",
        "duplicate",
        "over_budget",
        "late_declaration",
        "genuine",
        "version",
    ],
)
def test_invalid_plan_rejected(tmp_path: Path, kind: str) -> None:
    s = sources(tmp_path)
    if kind == "policy_pin":
        s = replace(s, policy_pin="0" * 64)
    elif kind == "cohort_pin":
        s = replace(s, cohort_pin="0" * 64)
    elif kind == "horizon_pin":
        s = replace(s, horizon_pin="0" * 64)
    elif kind == "missing_case":
        s = replace(s, cases=())
    elif kind == "duplicate":
        s = replace(s, cases=s.cases * 2)
    elif kind == "over_budget":
        s = replace(s, cases=s.cases * 33)
    else:
        d = s.policy.to_dict()
        if kind == "late_declaration":
            d["declared_at"] = (RECORDED + timedelta(seconds=1)).isoformat()
        elif kind == "genuine":
            d["mode"] = "genuine_prospective"
        elif kind == "version":
            d["version"] = "unknown"
        p = FrozenJSON.freeze(d)
        s = replace(s, policy=p, policy_pin=p.identifier)
    with pytest.raises(ValueError):
        prepare_evaluation(s, AS_OF)


def test_mismatched_cohort_owner_rejected(tmp_path: Path) -> None:
    s = sources(tmp_path)
    d = s.cohort.to_dict()
    d["training"]["start"] = "2025-06-30"
    cohort = FrozenJSON.freeze(d)
    p = s.policy.to_dict()
    p["cohort_pin"] = cohort.identifier
    owner = FrozenJSON.freeze(p)
    s = replace(
        s, policy=owner, policy_pin=owner.identifier, cohort=cohort, cohort_pin=cohort.identifier
    )
    with pytest.raises(ValueError, match="source_cohort_mismatch"):
        prepare_evaluation(s, AS_OF)


def test_semantic_tampering_and_wrong_retained_pin_rejected(tmp_path: Path) -> None:
    s = sources(tmp_path)
    report = prepare_evaluation(s, AS_OF)
    d = report.to_dict()
    d["diagnostics"]["declared_cases"] = 999
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_preparation(forged, forged.identifier, d["references_pin"], s, AS_OF)
    with pytest.raises(ValueError, match="reference_pin_mismatch"):
        verify_preparation(report, report.identifier, "0" * 64, s, AS_OF)
    d = report.to_dict()
    d["result"]["metrics"]["directional_accuracy"] = 1
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_preparation(forged, forged.identifier, d["references_pin"], s, AS_OF)


def test_partial_accounting_keeps_declared_denominator(tmp_path: Path) -> None:
    s = sources(tmp_path)
    root = tmp_path / "second"
    root.mkdir()
    second = make_original(root, (101, 91, 81, 121))
    cases = (*s.cases, EvidenceCase(second))
    d = s.policy.to_dict()
    d["expected_prediction_ids"] = sorted(c.prediction.prediction_id for c in cases)
    p = FrozenJSON.freeze(d)
    s = replace(s, policy=p, policy_pin=p.identifier, cases=cases)
    report = prepare_evaluation(s, AS_OF).to_dict()
    assert report["result"]["counts"]["total_declared_predictions"] == 2
    assert report["result"]["counts"]["evaluated_samples"] == 1
    assert report["diagnostics"]["declared_cases"] == 2
    assert not report["diagnostics"]["minimum_structure_coverage_met"]
    assert prepare_evaluation(replace(s, cases=tuple(reversed(cases))), AS_OF).to_dict() == report
    with pytest.raises(ValueError, match="membership_mismatch"):
        prepare_evaluation(replace(s, cases=s.cases[:1]), AS_OF)


def test_invalid_original_journal_fails_closed_without_fabricated_recording_time(
    tmp_path: Path,
) -> None:
    s = sources(tmp_path)
    case = s.cases[0]
    bad = EvidenceCase(replace(case.prediction, journal_head="0" * 64), case.future)
    with pytest.raises(ValueError):
        prepare_evaluation(replace(s, cases=(bad,)), AS_OF)


def test_duplicate_outcome_contract_remains_strict(tmp_path: Path) -> None:
    s = sources(tmp_path)
    root = tmp_path / "second"
    root.mkdir()
    second = make_original(root, (101, 91, 81, 121))
    cases = (*s.cases, EvidenceCase(second, s.cases[0].future))
    d = s.policy.to_dict()
    d["expected_prediction_ids"] = sorted(c.prediction.prediction_id for c in cases)
    p = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="duplicate_identifier"):
        prepare_evaluation(replace(s, policy=p, policy_pin=p.identifier, cases=cases), AS_OF)
