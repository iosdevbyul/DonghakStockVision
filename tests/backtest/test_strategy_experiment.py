"""Controlled offline adapter; no strategy interpreter, training or external acquisition."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import FailureCode, SimulationFault
from donghak_stock_vision.backtest.simulation_validation import TimeWindow
from donghak_stock_vision.backtest.strategy_evaluation import (
    CandidateAssessment,
    CandidateEvaluator,
    ComparisonEvidence,
    summarize_experiment,
    verify_experiment_summary,
)
from donghak_stock_vision.backtest.strategy_experiment import (
    DatasetPartition,
    ParameterGrid,
    ResearchPlan,
    StrategyCandidate,
    candidates,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Currency, Instrument, Market
from donghak_stock_vision.trading.paper_types import wire
from tests.backtest.test_simulation_comparison import campaign, compare, policy, registry
from tests.trading.test_risk import KR, US


def ref(name: str, fingerprint: str | None = None) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "artifact_id": name,
            "version": "v1",
            "content_hash": fingerprint or digest({"name": name}),
        }
    )


def fixture(
    *,
    count: int = 2,
    instrument: Instrument = KR,
    size: int = 1,
    terminal_price: str = "12",
    unavailable: bool = False,
    failed: bool = False,
    **changes: Any,
) -> tuple[ResearchPlan, ComparisonEvidence]:
    adapter = registry(instrument)
    params = FrozenJSON.freeze({"size": size})
    a = campaign(
        adapter,
        "a",
        parameters=params,
        terminal_price=terminal_price,
        unavailable=(0,) if unavailable else (),
        states={0: "failed"} if failed else None,
    )
    b = campaign(adapter, "b", parameters=params, terminal_price=terminal_price)
    p = policy(cohort_id="tuning")
    report = compare(a, b, rules=p)
    start = datetime(2026, 1, 5, tzinfo=UTC)
    partitions = tuple(
        DatasetPartition(
            role,
            ref(role),
            adapter.dataset_reference,
            TimeWindow(start + timedelta(days=i - 1), start + timedelta(days=i)),
            ref(f"{role}-partition-custody"),
        )
        for i, role in enumerate(("training", "tuning", "oos", "holdout"))
    )
    plan = ResearchPlan(
        "controlled-grid",
        a.experiment.strategy,
        a.experiment.code,
        ref("execution", a.experiment.execution_policy.identifier),
        ref("risk", digest(wire(adapter.scenarios[0].policy.risk_policies))),
        ref("valuation", p.identifier),
        ref("original-experiment"),
        a.experiment.market,
        a.experiment.currency,
        partitions,
        (ParameterGrid("size", "integer", tuple(range(1, count + 1)), "1", "64"),),
        a.experiment.seed,
        a.experiment.limits,
        64,
        1024,
    )
    return replace(plan, **changes), ComparisonEvidence(
        report,
        report.to_dict()["content_fingerprint"],
        (a, b),
        p,
        p.identifier,
        a.experiment.fingerprint,
        30,
    )


class OfflineFixtureAdapter:
    """TEST ONLY: returns prepared evidence. It does not execute candidate parameters."""

    def __init__(self, plan: ResearchPlan, source: ComparisonEvidence) -> None:
        self.plan, self.source = plan, source

    def assess(self, candidate: StrategyCandidate) -> CandidateAssessment:
        return CandidateAssessment(candidate, "completed", None, self.plan.provenance, self.source)


def assessment(plan: ResearchPlan, source: ComparisonEvidence) -> CandidateAssessment:
    adapter: CandidateEvaluator = OfflineFixtureAdapter(plan, source)
    return adapter.assess(candidates(plan)[0])


def summary(plan: ResearchPlan, items: tuple[CandidateAssessment, ...]) -> FrozenJSON:
    return summarize_experiment(
        plan, plan.document.identifier, items, tuple(a.document.identifier for a in items)
    )


def test_grid_order_identity_and_exact_values() -> None:
    plan, _ = fixture()
    plan = replace(
        plan,
        grid=(
            ParameterGrid("enabled", "boolean", (True, False)),
            ParameterGrid("rate", "decimal", ("0.10", "0.2"), "0", "1"),
        ),
    )
    rows = candidates(plan)
    assert [c.parameters.to_dict() for c in rows] == [
        {"enabled": True, "rate": "0.1"},
        {"enabled": True, "rate": "0.2"},
        {"enabled": False, "rate": "0.1"},
        {"enabled": False, "rate": "0.2"},
    ]
    assert len({c.candidate_id for c in rows}) == 4
    assert rows == candidates(plan)
    assert replace(plan, seed=plan.seed + 1).document != plan.document
    assert candidates(replace(plan, seed=plan.seed + 1))[0].candidate_id != rows[0].candidate_id


@pytest.mark.parametrize(
    "kind,values,low,high",
    [
        ("integer", (True,), "0", "1"),
        ("integer", (1.0,), "0", "1"),
        ("integer", (2,), "0", "1"),
        ("integer", (1,), None, None),
        ("decimal", (0.1,), "0", "1"),
        ("decimal", ("NaN",), "0", "1"),
        ("decimal", ("0.1", "0.10"), "0", "1"),
        ("boolean", (1,), None, None),
        ("enum", ("",), None, None),
        ("enum", ("a", "a"), None, None),
        ("integer", (1,), "2", "1"),
        ("code", ("exec",), None, None),
    ],
)
def test_invalid_parameters(kind: str, values: Any, low: Any, high: Any) -> None:
    with pytest.raises(ContractError):
        ParameterGrid("size", kind, values, low, high)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"max_candidates": 1}, "candidate_count_exceeded"),
        ({"max_configuration_bytes": 1}, "configuration_bytes_exceeded"),
        ({"max_candidates": 65}, "candidate_budget"),
        ({"market": Market.US}, "market_currency_mismatch"),
        ({"currency": Currency.USD}, "market_currency_mismatch"),
        ({"seed": True}, "invalid_quantity"),
    ],
)
def test_plan_invalid(changes: dict[str, Any], reason: str) -> None:
    with pytest.raises(ContractError, match=reason):
        fixture(**changes)


def test_cardinality_rejected_before_product(monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _ = fixture()

    def forbidden(*args: Any) -> Any:
        raise AssertionError("Cartesian product must not be materialized")

    monkeypatch.setattr("donghak_stock_vision.backtest.strategy_experiment.product", forbidden)
    with pytest.raises(ContractError, match="candidate_count_exceeded"):
        replace(
            plan, grid=tuple(ParameterGrid(f"p{i}", "boolean", (True, False)) for i in range(7))
        )


@pytest.mark.parametrize("role", ["oos", "holdout", "unknown"])
def test_holdout_and_oos_not_accessible(role: str) -> None:
    plan, _ = fixture()
    with pytest.raises(ContractError, match="feedback_forbidden"):
        plan.accessible_partition(role)
    assert plan.accessible_partition("training").role == "training"


def test_missing_split_provenance_and_overlap() -> None:
    plan, _ = fixture()
    with pytest.raises(ContractError):
        replace(plan.partitions[0], provenance=FrozenJSON.freeze({}))
    with pytest.raises(ContractError, match="partition_overlap"):
        replace(
            plan,
            partitions=(
                plan.partitions[0],
                replace(plan.partitions[1], window=plan.partitions[0].window),
                *plan.partitions[2:],
            ),
        )
    with pytest.raises(ContractError, match="four_partitions"):
        replace(plan, partitions=plan.partitions[:3])


def test_verified_statistics_missing_denominator_and_replay() -> None:
    plan, source = fixture()
    item = assessment(plan, source)
    result = summary(plan, (item,))
    value = result.to_dict()
    assert (value["candidate_count"], value["evaluated_count"], value["unavailable_count"]) == (
        2,
        1,
        1,
    )
    assert value["candidates"][0]["metrics"]["return_statistics"]["mean"] == {
        "numerator": "1",
        "denominator": "450",
    }
    assert value["candidates"][1]["metrics"] is None
    assert value["ranking"] is None and value["operational_eligible"] is False
    assert summary(plan, (item,)) == result
    verify_experiment_summary(
        result,
        value["content_fingerprint"],
        plan,
        plan.document.identifier,
        (item,),
        (item.document.identifier,),
    )


@pytest.mark.parametrize("failed", [False, True])
def test_unavailable_and_simulation_failure_retained(failed: bool) -> None:
    plan, source = fixture(unavailable=not failed, failed=failed)
    row = summary(plan, (assessment(plan, source),)).to_dict()["candidates"][0]
    assert row["state"] == "unavailable"
    assert row["metrics"]["return_statistics"]["mean"] is None
    assert row["metrics"]["outcomes"]["failed"] == int(failed)


def test_pinned_infrastructure_failure_distinct_from_negative_return() -> None:
    plan, _ = fixture()
    c = candidates(plan)
    items = (
        CandidateAssessment(c[0], "failed", "infrastructure_failure", plan.provenance, None),
        CandidateAssessment(c[1], "unavailable", "inputs_missing", plan.provenance, None),
    )
    value = summary(plan, items).to_dict()
    assert value["failed_count"] == value["unavailable_count"] == 1
    assert all(r["metrics"] is None for r in value["candidates"])
    assert summary(plan, tuple(reversed(items))) == summary(plan, items)


@pytest.mark.parametrize(
    "field", ["strategy", "code", "execution_policy", "risk_policy", "valuation_policy"]
)
def test_mismatched_candidate_evidence(field: str) -> None:
    plan, source = fixture()
    changes: dict[str, Any] = {field: ref("wrong")}
    bad = replace(plan, **changes)
    with pytest.raises(ContractError, match="mismatch"):
        summary(bad, (assessment(bad, source),))


def test_parameter_mismatch_duplicate_identity_and_stale_pins() -> None:
    plan, source = fixture()
    item = replace(assessment(plan, source), candidate=candidates(plan)[1])
    with pytest.raises(ContractError, match="parameter_mismatch"):
        summary(plan, (item,))
    item = assessment(plan, source)
    with pytest.raises(ContractError, match="duplicate_candidate"):
        summary(plan, (item, item))
    with pytest.raises(ContractError, match="assessment_pin"):
        summarize_experiment(plan, plan.document.identifier, (item,), ("0" * 64,))
    with pytest.raises(ContractError, match="plan_pin"):
        summarize_experiment(plan, "0" * 64, (), ())


def test_no_oos_source_can_enter_tuning() -> None:
    plan, source = fixture()
    # Valid economic evidence is still invalid for a different declared partition.
    shifted = replace(
        plan,
        partitions=tuple(
            replace(
                p,
                window=TimeWindow(
                    p.window.start - timedelta(days=10), p.window.end - timedelta(days=10)
                ),
            )
            for p in plan.partitions
        ),
    )
    with pytest.raises(ContractError, match="temporal_membership"):
        summary(shifted, (assessment(shifted, source),))


def test_rehashed_comparison_and_summary_rejected() -> None:
    plan, source = fixture()
    value = source.report.to_dict()
    value["experiments"][0]["return_statistics"]["mean"]["numerator"] = "999"
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    forged = replace(
        source, report=FrozenJSON.freeze(value), trusted_report_hash=value["content_fingerprint"]
    )
    with pytest.raises(ContractError, match="replay_mismatch"):
        summary(plan, (assessment(plan, forged),))
    item = assessment(plan, source)
    result = summary(plan, (item,)).to_dict()
    result["evaluated_count"] = 2
    result["content_fingerprint"] = digest(
        {k: v for k, v in result.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="summary_replay"):
        verify_experiment_summary(
            FrozenJSON.freeze(result),
            result["content_fingerprint"],
            plan,
            plan.document.identifier,
            (item,),
            (item.document.identifier,),
        )


def test_source_files_readonly_and_no_acquisition(monkeypatch: pytest.MonkeyPatch) -> None:
    plan, source = fixture()
    before = source.report, source.inputs[0].checkpoint, plan.document
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No network allowed")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    summary(plan, (assessment(plan, source),))
    assert before == (source.report, source.inputs[0].checkpoint, plan.document)


@pytest.mark.parametrize("instrument", [KR, US])
def test_multiple_candidate_metrics_and_losses_not_infrastructure(instrument: Instrument) -> None:
    plan, first = fixture(instrument=instrument, terminal_price="9")
    same, second = fixture(instrument=instrument, size=2, terminal_price="9")
    assert same == plan
    items = (
        assessment(plan, first),
        replace(assessment(plan, second), candidate=candidates(plan)[1]),
    )
    value = summary(plan, items).to_dict()
    assert value["evaluated_count"] == 2 and value["failed_count"] == 0
    for row in value["candidates"]:
        assert row["metrics"]["return_statistics"]["losses"] == 1
        assert row["state"] == "completed"
    assert summary(plan, tuple(reversed(items))).to_dict() == value


def test_mixed_market_source_rejected() -> None:
    plan, source = fixture()
    _, us = fixture(instrument=US)
    mixed = replace(source, inputs=(source.inputs[0], us.inputs[1]))
    with pytest.raises(ContractError, match="dataset_mismatch|market_mismatch"):
        summary(plan, (assessment(plan, mixed),))


@pytest.mark.parametrize("kind", ["cohort", "dataset", "provenance"])
def test_partition_and_provenance_mismatch(kind: str) -> None:
    plan, source = fixture()
    if kind == "provenance":
        item = replace(assessment(plan, source), provenance=ref("wrong"))
    else:
        changes: dict[str, Any] = {kind: ref("wrong")}
        plan = replace(
            plan,
            partitions=(
                plan.partitions[0],
                replace(plan.partitions[1], **changes),
                *plan.partitions[2:],
            ),
        )
        item = assessment(plan, source)
    with pytest.raises(ContractError, match="mismatch"):
        summary(plan, (item,))


def test_candidate_tampering_cannot_be_repinned() -> None:
    plan, source = fixture()
    item = assessment(plan, source)
    with pytest.raises(ContractError, match="candidate_identity"):
        replace(item.candidate, candidate_id="0" * 64)
    other = replace(plan, experiment_id="other")
    with pytest.raises(ContractError, match="candidate_not_in_plan"):
        summary(other, (item,))


def test_duplicate_axes_and_configurations() -> None:
    plan, _ = fixture()
    with pytest.raises(ContractError, match="duplicate_parameter_name"):
        replace(plan, grid=(plan.grid[0], plan.grid[0]))
    with pytest.raises(ContractError, match="duplicate_parameter"):
        ParameterGrid("size", "integer", (1, 1), "1", "2")
    with pytest.raises(ContractError, match="grid_name_order"):
        replace(plan, grid=(ParameterGrid("z", "enum", ("a",)), plan.grid[0]))


def test_assessment_state_and_failure_classification() -> None:
    plan, source = fixture()
    item = assessment(plan, source)
    for bad in (
        replace(item, state="promoted"),
        replace(item, state="failed", reason="strategy_loss", source=None),
        replace(item, state="unavailable", reason="inputs_missing"),
    ):
        with pytest.raises(ContractError):
            summary(plan, (bad,))


def test_budget_applies_before_economic_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    plan, source = fixture()
    plan = replace(plan, limits=replace(plan.limits, max_runs=1))
    item = assessment(plan, source)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Replay cannot start beyond the job budget")

    monkeypatch.setattr(
        "donghak_stock_vision.backtest.strategy_evaluation.verify_performance_comparison", forbidden
    )
    with pytest.raises(ContractError, match="evaluation_job_budget"):
        summary(plan, (item,))


def test_different_candidate_scenario_mix_is_not_a_common_cohort() -> None:
    plan, first = fixture()
    _, second = fixture(size=2)
    a = campaign(
        first.inputs[0].adapter,
        "a",
        parameters=candidates(plan)[1].parameters,
        keys=("different-case",),
    )
    b = campaign(
        first.inputs[0].adapter,
        "b",
        parameters=candidates(plan)[1].parameters,
        keys=("different-case",),
    )
    report = compare(a, b, rules=first.policy)
    second = replace(
        second,
        inputs=(a, b),
        report=report,
        trusted_report_hash=report.to_dict()["content_fingerprint"],
        experiment_hash=a.experiment.fingerprint,
    )
    items = (
        assessment(plan, first),
        replace(assessment(plan, second), candidate=candidates(plan)[1]),
    )
    with pytest.raises(ContractError, match="evaluation_membership_mismatch"):
        summary(plan, items)


def test_source_checkpoint_tampering_fails_closed() -> None:
    plan, source = fixture()
    changed = source.inputs[0].checkpoint.to_dict()
    changed["unexpected"] = "untrusted"
    forged = FrozenJSON.freeze(changed)
    owner = replace(source.inputs[0], checkpoint=forged, trusted_checkpoint_hash=forged.identifier)
    source = replace(source, inputs=(owner, source.inputs[1]))
    with pytest.raises(SimulationFault) as caught:
        summary(plan, (assessment(plan, source),))
    assert caught.value.code is FailureCode.CHECKPOINT_CORRUPTION
