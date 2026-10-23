"""Real synthetic strategy -> risk -> broker -> valuation -> multi-axis proposals."""

from dataclasses import replace
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.multi_parameter_improvement import (
    MultiParameterPlan,
    neighbors,
    parameter_space,
    run_multi_parameter,
    verify_multi_parameter,
)
from donghak_stock_vision.backtest.strategy_experiment import ParameterGrid
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import digest
from tests.backtest.test_strategy_execution import fixture as execution_fixture
from tests.backtest.test_strategy_improvement import fixture as single_fixture
from tests.trading.test_risk import KR, US


def fixture(*, instrument: Any = KR, dimensions: int = 3, **changes: Any) -> MultiParameterPlan:
    source = execution_fixture(instrument, entries=("10", "11"))
    grid = (
        source.plan.grid[0],
        ParameterGrid("exit", "decimal", ("11", "12"), "1", "20"),
        ParameterGrid("quantity", "integer", (1, 2) if dimensions == 3 else (1,), "1", "100"),
    )
    original = replace(source.plan, grid=grid)
    source = replace(source, plan=original, trusted_plan_hash=original.document.identifier)
    single = single_fixture()
    case = source.observations.cases[0]
    mark = replace(
        single.marks[0],
        opening_marks=case.steps[0].decision_frame.quotes,
        closing_marks=case.steps[-1].execution_frame.quotes,
    )
    plan = MultiParameterPlan(
        "multi-cycle",
        source,
        single.valuation_policy,
        single.trusted_policy_hash,
        (mark,),
        ((0, 1, 0), (0, 1, 1)) if dimensions == 3 else ((0, 1, 0),),
        "mean_return",
        "1",
        64,
        2,
        8,
        32,
        1,
        1024 * 1024,
        16 * 1024 * 1024,
        16 * 1024 * 1024,
    )
    return replace(plan, **changes)


@pytest.mark.parametrize("dimensions,valid,total", [(2, 3, 4), (3, 6, 8)])
def test_finite_valid_enumeration(dimensions: int, valid: int, total: int) -> None:
    points = parameter_space(fixture(dimensions=dimensions))
    assert len(points) == total
    assert sum(p.invalid_reason is None for p in points) == valid
    assert len({p.identity for p in points}) == total
    assert all(
        p.invalid_reason == "entry_must_precede_exit"
        for p in points
        if p.invalid_reason is not None
    )


def test_two_generations_actual_economics_and_replay() -> None:
    plan = fixture()
    result = run_multi_parameter(plan, plan.document.identifier)
    value = result.report.to_dict()
    assert value["attempts"] == 3
    first, second = value["generations"]
    assert first["research_priority_anchor"] == [0, 1, 0]
    assert first["next_coordinates"] == [[0, 0, 0]]
    assert second["parent_hash"] == first["generation_hash"]
    assert first["assessments"][0]["objective"] == {"numerator": "1", "denominator": "450"}
    assert first["assessments"][1]["objective"] == {"numerator": "1", "denominator": "900"}
    assert (
        len({tuple(r["coordinates"]) for g in value["generations"] for r in g["assessments"]}) == 3
    )
    outputs = [
        e.result.feedback[0].batch.inputs[0].checkpoint.to_dict()["records"][0]["output"]
        for e in result.evidence
        if e.result is not None
    ]
    assert outputs[0]["execution"]["counts"]["fills"] == 2
    assert outputs[1]["execution"]["counts"]["fills"] == 1
    verify_multi_parameter(result, value["content_fingerprint"], plan, plan.document.identifier)
    assert run_multi_parameter(plan, plan.document.identifier) == result


def test_neighbor_order_and_invalid_exclusion() -> None:
    space = parameter_space(fixture())
    actual = neighbors(space, (0, 1, 0), {(0, 1, 0)}, 5)
    assert actual[:3] == ((0, 0, 0), (0, 1, 1), (1, 1, 0))
    assert (1, 0, 0) not in actual and (1, 0, 1) not in actual
    assert neighbors(space, (0, 1, 0), {p.coordinates for p in space}, 1) == ()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"initial": ((0, 0, 0), (0, 0, 0))}, "duplicate_initial"),
        ({"initial": ((2, 0, 0),)}, "coordinate_range"),
        ({"initial": ((0, 0),)}, "coordinate_dimensions"),
        ({"max_valid_candidates": 0}, "valid_candidate_budget"),
        ({"max_generations": 17}, "generation_budget"),
        ({"max_simulation_runs": 257}, "simulation_run_budget"),
        ({"proposals_per_generation": 6}, "proposal_budget"),
        ({"max_output_bytes": 0}, "output_budget"),
    ],
)
def test_plan_limits(changes: dict[str, Any], reason: str) -> None:
    with pytest.raises(ContractError, match=reason):
        fixture(**changes)


def test_invalid_and_skipped_configurations_preserved_and_cancelled() -> None:
    plan = fixture(initial=((1, 0, 0),), max_attempts=1)
    result = run_multi_parameter(plan, plan.document.identifier, cancel_before_generation=1)
    value = result.report.to_dict()
    assert value["state"] == "stopped"
    assert value["stop_reason"] == "cancelled_at_generation_boundary"
    assert value["attempts"] == 0
    assert value["generations"][0]["assessments"][0]["status"] == "invalid"
    assert any(p["status"] == "skipped" for p in value["configurations"])
    verify_multi_parameter(
        result,
        value["content_fingerprint"],
        plan,
        plan.document.identifier,
        cancel_before_generation=1,
    )


@pytest.mark.parametrize("boundary", [0, 1])
def test_cancellation_keeps_prior_owners(boundary: int) -> None:
    plan = fixture()
    result = run_multi_parameter(plan, plan.document.identifier, cancel_before_generation=boundary)
    value = result.report.to_dict()
    assert value["state"] == "stopped"
    assert len(result.evidence) == boundary * 2
    assert value["stop_reason"] == "cancelled_at_generation_boundary"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"max_attempts": 2}, "attempt_budget_exhausted"),
        ({"max_simulation_runs": 2}, "simulation_run_budget_exhausted"),
        ({"max_retained_bytes": 1024 * 1024}, "retained_byte_reservation_exhausted"),
    ],
)
def test_pre_generation_budgets(changes: dict[str, Any], reason: str) -> None:
    plan = fixture(**changes)
    value = run_multi_parameter(plan, plan.document.identifier).report.to_dict()
    assert value["state"] == "stopped" and value["stop_reason"] == reason


def test_valid_candidate_cap() -> None:
    with pytest.raises(ContractError, match="valid_candidate_budget_exceeded"):
        parameter_space(fixture(max_valid_candidates=2))


def test_no_economics_no_priority() -> None:
    plan = fixture(marks=())
    value = run_multi_parameter(plan, plan.document.identifier).report.to_dict()
    assert value["stop_reason"] == "no_complete_eligible_tuning_feedback"
    assert all(r["objective"] is None for r in value["generations"][0]["assessments"])


def test_partial_coverage_descriptive_only() -> None:
    from donghak_stock_vision.backtest.strategy_observations import SyntheticObservations
    from tests.backtest.test_strategy_execution import rebind

    old = fixture(minimum_coverage="1/2")
    case = old.source.observations.cases[0]
    source = rebind(old.source, SyntheticObservations((case, replace(case, case_id="case-2"))))
    plan = replace(old, source=source)
    value = run_multi_parameter(plan, plan.document.identifier).report.to_dict()
    assert value["stop_reason"] == "no_complete_eligible_tuning_feedback"
    rows = value["generations"][0]["assessments"]
    assert all(r["evaluated"] == 1 and r["expected"] == 2 for r in rows)
    assert all(r["objective"] is not None and not r["eligible"] for r in rows)


@pytest.mark.parametrize("field", ["generations", "configurations", "stop_reason", "attempts"])
def test_rehashed_report_tampering(field: str) -> None:
    plan = fixture(max_generations=1)
    result = run_multi_parameter(plan, plan.document.identifier)
    value = result.report.to_dict()
    value[field] = "forged"
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="multi_semantic_replay_mismatch"):
        verify_multi_parameter(
            replace(result, report=FrozenJSON.freeze(value)),
            value["content_fingerprint"],
            plan,
            plan.document.identifier,
        )


@pytest.mark.parametrize("role", ["oos", "holdout"])
def test_forbidden_roles(role: str) -> None:
    old = fixture()
    source = replace(old.source, authorization=replace(old.source.authorization, role=role))
    with pytest.raises(ContractError, match="feedback_role_forbidden"):
        replace(old, source=source)


@pytest.mark.parametrize("instrument", [KR, US])
def test_native_market_and_no_network(instrument: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("external network forbidden")

    monkeypatch.setattr("socket.create_connection", forbidden)
    plan = fixture(instrument=instrument, dimensions=2)
    result = run_multi_parameter(plan, plan.document.identifier)
    assert result.report.to_dict()["attempts"] == 2
    assert result.report.to_dict()["operational_eligible"] is False


def test_axis_and_cardinality_bounds_before_enumeration(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Cartesian expansion must not run")

    monkeypatch.setattr(
        "donghak_stock_vision.backtest.multi_parameter_improvement.candidates", forbidden
    )
    old = fixture()
    grid = list(old.source.plan.grid)
    grid[0] = replace(grid[0], values=tuple(str(i) for i in range(1, 10)))
    raw = replace(old.source.plan, grid=tuple(grid))
    source = replace(old.source, plan=raw, trusted_plan_hash=raw.document.identifier)
    with pytest.raises(ContractError, match="axis_value_bound"):
        replace(old, source=source)
    grid[1] = replace(grid[1], values=tuple(str(i) for i in range(11, 19)))
    with pytest.raises(ContractError, match="candidate_count_exceeded"):
        replace(old.source.plan, grid=tuple(grid))


def test_output_budget_is_separate_from_reservation() -> None:
    plan = fixture(max_output_bytes=1000, max_generations=1)
    with pytest.raises(ContractError, match="json_budget_exceeded"):
        run_multi_parameter(plan, plan.document.identifier)


def test_candidate_output_exhaustion_is_not_profit_and_cancellation_wins() -> None:
    plan = fixture(max_candidate_bytes=1000)
    result = run_multi_parameter(plan, plan.document.identifier, cancel_before_generation=1)
    value = result.report.to_dict()
    assert value["state"] == "stopped"
    assert value["stop_reason"] == "cancelled_at_generation_boundary"
    assert all(
        r["status"] == "failed" and r["objective"] is None
        for r in value["generations"][0]["assessments"]
    )
    assert all(e.failure is not None for e in result.evidence)
    verify_multi_parameter(
        result,
        value["content_fingerprint"],
        plan,
        plan.document.identifier,
        cancel_before_generation=1,
    )


def test_invalid_owner_is_rejected_before_loading() -> None:
    class Forbidden:
        @property
        def observations(self) -> Any:
            raise AssertionError("forbidden evidence was loaded")

    with pytest.raises(ContractError, match="tuning_source_required"):
        fixture(source=Forbidden())


def test_forged_feedback_and_foreign_cycle_owner() -> None:
    plan = fixture(max_generations=1)
    result = run_multi_parameter(plan, plan.document.identifier)
    owner = result.evidence[0]
    assert owner.result is not None
    feedback = owner.result.feedback[0]
    report = feedback.analysis.report.to_dict()
    report["valuations"][0]["valuation"]["performance"]["total_return"]["amount"] = "123"
    report["content_fingerprint"] = digest(
        {k: v for k, v in report.items() if k != "content_fingerprint"}
    )
    forged = replace(
        feedback, analysis=replace(feedback.analysis, report=FrozenJSON.freeze(report))
    )
    changed = replace(owner, result=replace(owner.result, feedback=(forged,)))
    with pytest.raises(ContractError, match="multi_semantic_replay_mismatch"):
        verify_multi_parameter(
            replace(result, evidence=(changed, *result.evidence[1:])),
            result.report.to_dict()["content_fingerprint"],
            plan,
            plan.document.identifier,
        )
    with pytest.raises(ContractError, match="multi_semantic_replay_mismatch"):
        verify_multi_parameter(
            replace(result, evidence=(replace(owner, cycle_hash="0" * 64), *result.evidence[1:])),
            result.report.to_dict()["content_fingerprint"],
            plan,
            plan.document.identifier,
        )


def test_incompatible_source_pin_and_cohort_rejected() -> None:
    old = fixture()
    with pytest.raises(ContractError):
        replace(old, source=replace(old.source, trusted_plan_hash="0" * 64))
    with pytest.raises(ContractError):
        replace(old, source=replace(old.source, trusted_authorization_hash="0" * 64))
    with pytest.raises(ContractError, match="multi_plan_pin"):
        run_multi_parameter(old, "0" * 64)


def test_finite_exhaustion_and_generation_bound() -> None:
    plan = fixture(dimensions=2, max_generations=4)
    result = run_multi_parameter(plan, plan.document.identifier).report.to_dict()
    assert result["stop_reason"] == "finite_search_exhausted"
    assert result["attempts"] == 3
    one = fixture(max_generations=1)
    result = run_multi_parameter(one, one.document.identifier).report.to_dict()
    assert result["stop_reason"] == "generation_budget_exhausted"
