"""Bounded, synthetic training/tuning improvement with independent economic replay."""

from dataclasses import replace
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.strategy_improvement import (
    ImprovementPlan,
    TuningMarks,
    run_improvement,
    verify_feedback,
    verify_improvement,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import digest
from tests.backtest.test_simulation_comparison import policy
from tests.backtest.test_strategy_execution import fixture as execution_fixture
from tests.trading.test_risk import KR, US


def fixture(**changes: Any) -> ImprovementPlan:
    source = execution_fixture(entries=("10", "9", "11"))
    case = source.observations.cases[0]
    marks = TuningMarks(
        case.case_id,
        case.steps[0].decision_frame.now,
        case.steps[-1].execution_frame.now,
        case.steps[0].decision_frame.quotes,
        case.steps[-1].execution_frame.quotes,
        60,
        "replayed_closed_paper_commands_v1",
    )
    p = policy(cohort_id="cohort-tuning")
    plan = ImprovementPlan(
        "cycle-1",
        source,
        p,
        p.identifier,
        (marks,),
        "entry",
        (0, 1),
        "mean_return",
        "1",
        2,
        4,
        16 * 1024 * 1024,
    )
    return replace(plan, **changes)


def test_two_generations_actual_economics_and_replay() -> None:
    plan = fixture()
    result = run_improvement(plan, plan.document.identifier)
    value = result.report.to_dict()
    assert value["state"] == "completed"
    assert value["attempted"] == 3
    first, second = value["generations"]
    assert first["next_indices"] == [2]
    assert second["parent_hash"] == first["generation_hash"]
    assert first["assessments"][0]["objective"] == {"numerator": "1", "denominator": "450"}
    assert first["assessments"][1]["objective"] == {"numerator": "0", "denominator": "1"}
    assert (
        len({r["configuration_hash"] for g in value["generations"] for r in g["assessments"]}) == 3
    )
    verify_improvement(result, value["content_fingerprint"], plan, plan.document.identifier)
    assert run_improvement(plan, plan.document.identifier) == result


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"initial_indices": (0, 0)}, "duplicate_initial"),
        ({"initial_indices": (3,)}, "initial_index"),
        ({"max_attempts": 1}, "attempt_budget"),
        ({"max_generations": 17}, "generation_budget"),
        ({"minimum_coverage": "0"}, "coverage_threshold"),
        ({"minimum_coverage": "2"}, "coverage_threshold"),
        ({"metric": "sharpe"}, "unsupported_objective"),
        ({"search_axis": "missing"}, "unknown_search_axis"),
    ],
)
def test_invalid_plans(changes: dict[str, Any], reason: str) -> None:
    with pytest.raises(ContractError, match=reason):
        fixture(**changes)


@pytest.mark.parametrize("metric", ["mean_return", "median_return"])
def test_unavailable_not_zero(metric: str) -> None:
    plan = fixture(marks=(), metric=metric)
    result = run_improvement(plan, plan.document.identifier).report.to_dict()
    assert result["stop_reason"] == "no_eligible_tuning_feedback"
    assert all(
        r["objective"] is None and not r["eligible"]
        for r in result["generations"][0]["assessments"]
    )


@pytest.mark.parametrize("boundary", [0, 1])
def test_cancellation_preserves_evidence(boundary: int) -> None:
    plan = fixture()
    result = run_improvement(plan, plan.document.identifier, cancel_before_generation=boundary)
    assert len(result.feedback) == boundary
    assert result.report.to_dict()["state"] == "stopped"
    verify_improvement(
        result,
        result.report.to_dict()["content_fingerprint"],
        plan,
        plan.document.identifier,
        cancel_before_generation=boundary,
    )


def test_attempt_and_evidence_budgets() -> None:
    plan = fixture(max_attempts=2)
    assert run_improvement(plan, plan.document.identifier).report.to_dict()["stop_reason"] == (
        "attempt_budget_exhausted"
    )
    plan = fixture(max_result_bytes=1000)
    with pytest.raises(ContractError, match="cycle_evidence_budget"):
        run_improvement(plan, plan.document.identifier)


@pytest.mark.parametrize("role", ["oos", "holdout"])
def test_forbidden_partitions(role: str) -> None:
    with pytest.raises(ContractError, match="feedback_role_forbidden"):
        fixture(source=execution_fixture(role=role))


def test_feedback_allowlist_and_cross_cycle() -> None:
    plan = fixture(max_generations=1)
    feedback = run_improvement(plan, plan.document.identifier).feedback[0]
    with pytest.raises(ContractError, match="tuning_feedback_owner_required"):
        verify_feedback(plan, FrozenJSON.freeze({"origin": "oos"}), 0, (0, 1), None)  # type: ignore[arg-type]
    other = replace(plan, cycle_id="other-cycle")
    with pytest.raises(ContractError, match="feedback_lineage_mismatch"):
        verify_feedback(other, feedback, 0, (0, 1), None)
    with pytest.raises(ContractError, match="feedback_lineage_mismatch"):
        verify_feedback(plan, feedback, 1, (0, 1), None)


def test_forged_feedback_rehashed_is_not_evidence() -> None:
    plan = fixture(max_generations=1)
    feedback = run_improvement(plan, plan.document.identifier).feedback[0]
    value = feedback.analysis.report.to_dict()
    value["valuations"][0]["valuation"]["performance"]["total_return"]["amount"] = "999"
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    forged = replace(feedback, analysis=replace(feedback.analysis, report=FrozenJSON.freeze(value)))
    with pytest.raises(ContractError, match="strategy_analysis_replay_mismatch"):
        verify_feedback(plan, forged, 0, (0, 1), None)


@pytest.mark.parametrize("field", ["state", "generations", "objective", "cycle_hash"])
def test_report_tampering(field: str) -> None:
    plan = fixture(max_generations=1)
    result = run_improvement(plan, plan.document.identifier)
    value = result.report.to_dict()
    value[field] = [] if field == "generations" else "forged"
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="cycle_semantic_replay_mismatch"):
        verify_improvement(
            replace(result, report=FrozenJSON.freeze(value)),
            value["content_fingerprint"],
            plan,
            plan.document.identifier,
        )


@pytest.mark.parametrize("instrument", [KR, US])
def test_native_currency_and_no_network(instrument: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network forbidden")

    monkeypatch.setattr("socket.create_connection", forbidden)
    source = execution_fixture(instrument, entries=("10", "9", "11"))
    old = fixture()
    case = source.observations.cases[0]
    mark = replace(
        old.marks[0],
        opening_marks=case.steps[0].decision_frame.quotes,
        closing_marks=case.steps[-1].execution_frame.quotes,
    )
    plan = replace(old, source=source, marks=(mark,), max_generations=1)
    result = run_improvement(plan, plan.document.identifier)
    assert result.report.to_dict()["attempted"] == 2


def test_failed_configuration_is_preserved() -> None:
    source = execution_fixture(entries=("13",))  # Entry >= exit: original adapter rejects.
    plan = fixture(source=source, initial_indices=(0,))
    result = run_improvement(plan, plan.document.identifier).report.to_dict()
    assert result["state"] == "failed"
    assert (
        result["generations"][0]["assessments"][0]["execution_reason"]
        == "unsupported_reference_parameters"
    )


def test_incomplete_cash_flow_evidence() -> None:
    from tests.backtest.test_strategy_experiment import ref

    old = fixture()
    p = policy(cohort_id="cohort-tuning", cash_flow_scope=None)
    source_plan = replace(old.source.plan, valuation_policy=ref("valuation", p.identifier))
    source = replace(
        old.source, plan=source_plan, trusted_plan_hash=source_plan.document.identifier
    )
    plan = replace(
        old,
        source=source,
        valuation_policy=p,
        trusted_policy_hash=p.identifier,
        marks=(replace(old.marks[0], cash_flow_scope=None),),
    )
    value = run_improvement(plan, plan.document.identifier).report.to_dict()
    assert value["stop_reason"] == "no_eligible_tuning_feedback"
    assert all(r["objective"] is None for r in value["generations"][0]["assessments"])


def test_wrong_pins_and_strategy_identity() -> None:
    from tests.backtest.test_strategy_experiment import ref

    plan = fixture()
    with pytest.raises(ContractError, match="cycle_plan_pin"):
        run_improvement(plan, "0" * 64)
    source_plan = replace(plan.source.plan, strategy=ref("different-code"))
    source = replace(
        plan.source, plan=source_plan, trusted_plan_hash=source_plan.document.identifier
    )
    with pytest.raises(ContractError):
        replace(plan, source=source)


def test_frozen_oos_owner_rejected_before_loading() -> None:
    class ForbiddenOwner:
        @property
        def report(self) -> Any:
            raise AssertionError("forbidden evidence must never be loaded")

    plan = fixture()
    with pytest.raises(ContractError, match="tuning_source_required"):
        replace(plan, source=ForbiddenOwner())  # type: ignore[arg-type]
    with pytest.raises(ContractError, match="tuning_feedback_owner_required"):
        verify_feedback(plan, ForbiddenOwner(), 0, (0, 1), None)  # type: ignore[arg-type]


def test_tampered_marks_and_parent() -> None:
    plan = fixture(max_generations=1)
    feedback = run_improvement(plan, plan.document.identifier).feedback[0]
    with pytest.raises(ContractError, match="feedback_marks_mismatch"):
        verify_feedback(plan, replace(feedback, marks=()), 0, (0, 1), None)
    with pytest.raises(ContractError, match="feedback_lineage_mismatch"):
        verify_feedback(plan, replace(feedback, parent_hash="0" * 64), 0, (0, 1), None)


@pytest.mark.parametrize("threshold,eligible", [("1", False), ("1/2", True)])
def test_missing_cases_stay_in_coverage_denominator(threshold: str, eligible: bool) -> None:
    from donghak_stock_vision.backtest.strategy_observations import SyntheticObservations
    from tests.backtest.test_strategy_execution import rebind

    old = fixture()
    case = old.source.observations.cases[0]
    source = rebind(old.source, SyntheticObservations((case, replace(case, case_id="case-2"))))
    plan = replace(old, source=source, minimum_coverage=threshold, max_generations=1)
    rows = run_improvement(plan, plan.document.identifier).report.to_dict()["generations"][0][
        "assessments"
    ]
    assert all(r["expected"] == 2 and r["evaluated"] == 1 for r in rows)
    assert all(r["coverage"] == {"numerator": "1", "denominator": "2"} for r in rows)
    assert all(r["eligible"] is eligible for r in rows)


def test_prior_cycle_and_parameter_tampering() -> None:
    plan = fixture(max_generations=1)
    feedback = run_improvement(plan, plan.document.identifier).feedback[0]
    value = feedback.batch.report.to_dict()
    value["candidates"][0]["candidate"]["parameters"]["entry"] = "9"
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    changed = replace(feedback, batch=replace(feedback.batch, report=FrozenJSON.freeze(value)))
    with pytest.raises(ContractError):
        verify_feedback(plan, changed, 0, (0, 1), None)


def test_verified_feedback_changes_research_priority() -> None:
    left = fixture(
        source=execution_fixture(entries=("10", "9", "8", "7")),
        initial_indices=(0, 3),
        max_generations=1,
    )
    right = fixture(
        source=execution_fixture(entries=("9", "8", "7", "10")),
        initial_indices=(0, 3),
        max_generations=1,
    )
    left_row = run_improvement(left, left.document.identifier).report.to_dict()["generations"][0]
    right_row = run_improvement(right, right.document.identifier).report.to_dict()["generations"][0]
    assert left_row["next_indices"] == [1]
    assert right_row["next_indices"] == [2]
    assert left_row["assessments"][0]["eligible"]
    assert right_row["assessments"][1]["eligible"]


def test_partial_coverage_remains_reported_but_cannot_steer_proposals() -> None:
    from donghak_stock_vision.backtest.strategy_observations import SyntheticObservations
    from tests.backtest.test_strategy_execution import rebind

    old = fixture()
    case = old.source.observations.cases[0]
    source = rebind(old.source, SyntheticObservations((case, replace(case, case_id="case-2"))))
    plan = replace(old, source=source, minimum_coverage="1/2")
    result = run_improvement(plan, plan.document.identifier).report.to_dict()
    first = result["generations"][0]
    assert all(row["eligible"] for row in first["assessments"])
    assert all(row["evaluated_case_ids"] == [case.case_id] for row in first["assessments"])
    assert first["next_indices"] == []
    assert result["stop_reason"] == "no_eligible_tuning_feedback"
    assert result["attempted"] == 2


def test_preflight_stops_before_next_generation_when_evidence_budget_is_exhausted() -> None:
    baseline = fixture()
    first = run_improvement(
        replace(baseline, max_generations=1), replace(baseline, max_generations=1).document.identifier
    )
    # Give the first generation enough room, but not enough for an equally
    # sized second generation. Preflight must retain the first generation.
    first_feedback = first.feedback[0]
    observed = (
        len(first_feedback.batch.report.payload_json.encode())
        + len(first_feedback.analysis.report.payload_json.encode())
        + sum(len(i.checkpoint.payload_json.encode()) for i in first_feedback.batch.inputs)
    )
    plan = replace(baseline, max_result_bytes=observed + 8192)
    result = run_improvement(plan, plan.document.identifier).report.to_dict()
    assert result["attempted"] == 2
    assert result["state"] == "stopped"
    assert result["stop_reason"] == "evidence_budget_preflight"
    assert len(result["generations"]) == 1
