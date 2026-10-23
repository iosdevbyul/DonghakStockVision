"""Synthetic decision -> original risk/broker -> valuation evidence. No providers."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_spec import SimulationLimits
from donghak_stock_vision.backtest.simulation_validation import TimeWindow
from donghak_stock_vision.backtest.strategy_execution import (
    StrategyExecutionRequest,
    execute_strategy_candidates,
    verify_strategy_execution,
)
from donghak_stock_vision.backtest.strategy_experiment import (
    DatasetPartition,
    ParameterGrid,
    ResearchPlan,
)
from donghak_stock_vision.backtest.strategy_observations import (
    MarketStep,
    ObservationCase,
    PartitionAuthorization,
    SyntheticObservations,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.strategy.paper_reference import code_reference, strategy_reference
from donghak_stock_vision.trading.domain import Instrument, Money
from tests.backtest.test_simulation_comparison import policy
from tests.backtest.test_strategy_experiment import ref
from tests.trading.test_paper import fixture as broker_fixture
from tests.trading.test_paper import next_frame
from tests.trading.test_risk import KR, US, T


def fixture(
    instrument: Instrument = KR,
    *,
    entries: tuple[str, ...] = ("10", "9"),
    role: str = "tuning",
    capacity: int = 1,
    cancel: bool = True,
    **rates: Any,
) -> StrategyExecutionRequest:
    broker, _, initial = broker_fixture(instrument)
    account = replace(broker.account("account1"), positions=())
    initial = replace(
        initial,
        valuations=tuple(
            replace(v, equity=Money(Decimal("900"), instrument.currency))
            for v in initial.valuations
        ),
    )
    policy_owner = replace(
        broker.checkpoint().policy,
        max_commands=64,
        buy_fee_rate=Decimal(0),
        sell_fee_rate=Decimal(0),
        sell_tax_rate=Decimal(0),
        slippage_rate=Decimal(0),
    )
    policy_owner = replace(policy_owner, **rates)
    steps = tuple(
        MarketStep(
            next_frame(initial, i * 2, price),
            next_frame(initial, i * 2 + 1, price),
            capacity,
            f"liquidity-{i}",
            cancel,
        )
        for i, price in enumerate(("10", "12", "11"))
    )
    source = SyntheticObservations(
        (ObservationCase("case-1", instrument, account, policy_owner, steps),)
    )
    roles = ("training", "tuning", "oos", "holdout")
    start = T.replace(hour=0, minute=0) - timedelta(days=roles.index(role))
    windows = tuple(
        TimeWindow(start + timedelta(days=i), start + timedelta(days=i + 1)) for i in range(4)
    )
    cohorts = tuple(ref(f"cohort-{r}") for r in roles)
    auth = PartitionAuthorization(
        "explicit-synthetic-custody",
        role,
        source.document.identifier,
        cohorts[roles.index(role)].identifier,
        windows[roles.index(role)],
    )
    partitions = tuple(
        DatasetPartition(
            r,
            cohorts[i],
            source.reference,
            windows[i],
            ref(f"provenance-{r}", auth.document.identifier)
            if r == role
            else ref(f"provenance-{r}"),
        )
        for i, r in enumerate(roles)
    )
    valuation_policy = policy(cohort_id=f"cohort-{role}")
    plan = ResearchPlan(
        "parameter-sensitive-fixture",
        strategy_reference(),
        code_reference(),
        ref("fixture-execution", source.execution_policy.identifier),
        ref("fixture-risk", source.risk_hash),
        ref("valuation", valuation_policy.identifier),
        ref("original-experiment-custody"),
        instrument.market,
        instrument.currency,
        partitions,
        (
            ParameterGrid("entry", "decimal", entries, "1", "20"),
            ParameterGrid("exit", "decimal", ("12",), "1", "20"),
            ParameterGrid("quantity", "integer", (1,), "1", "100"),
        ),
        17,
        SimulationLimits(32, 1, 1, 65, 60, 256 * 1024, 16 * 1024 * 1024, 512 * 1024),
        64,
        1024,
    )
    return StrategyExecutionRequest(
        plan,
        plan.document.identifier,
        source,
        source.document.identifier,
        auth,
        auth.document.identifier,
    )


def test_parameter_sensitive_full_flow() -> None:
    request = fixture()
    batch = execute_strategy_candidates(request)
    rows = batch.report.to_dict()["candidates"]
    assert [[d["action"] for d in r["decisions"]] for r in rows] == [
        ["BUY", "SELL", "WAIT"],
        ["WAIT", "SELL", "WAIT"],
    ]
    assert [r["status"] for r in rows] == ["completed", "completed"]
    outputs = [s.checkpoint.to_dict()["records"][0]["output"] for s in batch.inputs]
    assert [r["execution"]["counts"]["fills"] for r in outputs] == [2, 0]
    assert outputs[1]["execution"]["counts"]["risk_denied_orders"] == 1
    verify_strategy_execution(batch, batch.report.to_dict()["content_fingerprint"], request)
    assert execute_strategy_candidates(request) == batch


def rebind(
    request: StrategyExecutionRequest, source: SyntheticObservations
) -> StrategyExecutionRequest:
    auth = replace(request.authorization, dataset_hash=source.document.identifier)
    partitions = tuple(
        replace(
            p, dataset=source.reference, provenance=ref("rebound-custody", auth.document.identifier)
        )
        if p.role == auth.role
        else p
        for p in request.plan.partitions
    )
    plan = replace(
        request.plan,
        partitions=partitions,
        execution_policy=ref("execution", source.execution_policy.identifier),
        risk_policy=ref("risk", source.risk_hash),
    )
    return StrategyExecutionRequest(
        plan,
        plan.document.identifier,
        source,
        source.document.identifier,
        auth,
        auth.document.identifier,
    )


@pytest.mark.parametrize("instrument", [KR, US])
@pytest.mark.parametrize("role", ["training", "tuning"])
def test_native_market_authorized_roles(instrument: Instrument, role: str) -> None:
    request = fixture(instrument, role=role)
    batch = execute_strategy_candidates(request)
    assert all(
        i.experiment.market == instrument.market and i.experiment.currency == instrument.currency
        for i in batch.inputs
    )
    assert batch.report.to_dict()["role"] == role
    assert batch.report.to_dict()["historical_pit_approved"] is False


@pytest.mark.parametrize("role", ["oos", "holdout"])
def test_forbidden_partition_even_with_valid_pins(role: str) -> None:
    with pytest.raises(ContractError, match="feedback_forbidden"):
        execute_strategy_candidates(fixture(role=role))


@pytest.mark.parametrize("field", ["strategy", "code"])
def test_artifact_mismatch(field: str) -> None:
    request = fixture()
    changes: dict[str, Any] = {field: ref("substituted")}
    plan = replace(request.plan, **changes)
    request = replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
    with pytest.raises(ContractError, match="unsupported_strategy_artifact"):
        execute_strategy_candidates(request)


@pytest.mark.parametrize(
    "field", ["trusted_plan_hash", "trusted_observations_hash", "trusted_authorization_hash"]
)
def test_independent_pins_required(field: str) -> None:
    changes: dict[str, Any] = {field: "0" * 64}
    with pytest.raises(ContractError, match="pin_mismatch"):
        execute_strategy_candidates(replace(fixture(), **changes))


def test_future_and_missing_availability_rejected() -> None:
    request = fixture()
    case = request.observations.cases[0]
    frame = case.steps[0].decision_frame
    quote = frame.quotes[0]
    with pytest.raises(ContractError):
        changes: dict[str, Any] = {"available_at": None}
        replace(quote, **changes)
    future = replace(quote, available_at=frame.now + timedelta(seconds=1))
    bad_step = replace(case.steps[0], decision_frame=replace(frame, quotes=(future,)))
    with pytest.raises(ContractError, match="lookahead"):
        replace(case, steps=(bad_step, *case.steps[1:]))


def test_exact_window_and_provenance_binding() -> None:
    request = fixture()
    auth = replace(
        request.authorization, window=TimeWindow(T + timedelta(seconds=1), T + timedelta(hours=1))
    )
    partitions = tuple(
        replace(p, window=auth.window, provenance=ref("new", auth.document.identifier))
        if p.role == auth.role
        else p
        for p in request.plan.partitions
    )
    plan = replace(request.plan, partitions=partitions)
    changed = replace(
        request,
        plan=plan,
        trusted_plan_hash=plan.document.identifier,
        authorization=auth,
        trusted_authorization_hash=auth.document.identifier,
    )
    with pytest.raises(ContractError, match="outside_authorized"):
        execute_strategy_candidates(changed)
    auth = replace(request.authorization, cohort_hash="0" * 64)
    with pytest.raises(ContractError, match="partition_binding"):
        execute_strategy_candidates(
            replace(
                request, authorization=auth, trusted_authorization_hash=auth.document.identifier
            )
        )


def test_original_dataset_and_policy_substitution_rejected() -> None:
    request = fixture()
    case = request.observations.cases[0]
    changed = SyntheticObservations((replace(case, case_id="substitution"),))
    with pytest.raises(ContractError, match="dataset_pin"):
        execute_strategy_candidates(replace(request, observations=changed))
    plan = replace(request.plan, risk_policy=ref("other-policy"))
    with pytest.raises(ContractError, match="source_policy"):
        execute_strategy_candidates(
            replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
        )


def test_failures_do_not_contaminate_other_candidate_accounts() -> None:
    batch = execute_strategy_candidates(fixture(entries=("10", "13")))
    rows = batch.report.to_dict()["candidates"]
    assert [r["status"] for r in rows] == ["completed", "failed"]
    assert rows[1]["failure_classification"] == "invalid_configuration"
    output = batch.inputs[0].checkpoint.to_dict()["records"][0]["output"]
    assert output["execution"]["counts"]["fills"] == 2
    assert len(batch.inputs) == 1


def test_unsupported_parameters_cannot_supply_fill_or_risk_controls() -> None:
    request = fixture()
    plan = replace(
        request.plan, grid=(*request.plan.grid, ParameterGrid("risk_approval", "boolean", (True,)))
    )
    request = replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
    batch = execute_strategy_candidates(request)
    assert not batch.inputs
    assert all(r["status"] == "failed" for r in batch.report.to_dict()["candidates"])


@pytest.mark.parametrize("capacity,cancel,expected", [(0, False, 1), (0, True, 0), (1, True, 0)])
def test_pending_and_cleanup_use_fixture_liquidity(
    capacity: int, cancel: bool, expected: int
) -> None:
    request = fixture(entries=("10",), capacity=capacity, cancel=cancel)
    batch = execute_strategy_candidates(request)
    counts = batch.inputs[0].checkpoint.to_dict()["records"][0]["output"]["execution"]["counts"]
    assert counts["pending_orders"] == expected
    assert counts["fills"] == (2 if capacity else 0)
    assert counts["cancellations"] == int(capacity == 0 and cancel)


def test_insufficient_cash_has_no_fabricated_fill() -> None:
    request = fixture(entries=("10",))
    case = request.observations.cases[0]
    balance = replace(
        case.initial_account.balances[0], available=Money(Decimal(0), case.instrument.currency)
    )
    source = SyntheticObservations(
        (replace(case, initial_account=replace(case.initial_account, balances=(balance,))),)
    )
    batch = execute_strategy_candidates(rebind(request, source))
    counts = batch.inputs[0].checkpoint.to_dict()["records"][0]["output"]["execution"]["counts"]
    assert counts["fills"] == 0 and counts["risk_denied_orders"] == 2


def test_missing_quote_is_explicit_no_action() -> None:
    request = fixture(entries=("10",))
    case = request.observations.cases[0]
    step = replace(case.steps[0], decision_frame=replace(case.steps[0].decision_frame, quotes=()))
    source = SyntheticObservations((replace(case, steps=(step, *case.steps[1:])),))
    rows = execute_strategy_candidates(rebind(request, source)).report.to_dict()["candidates"]
    assert rows[0]["decisions"][0]["action"] == "WAIT"
    assert rows[0]["decisions"][0]["rule"] == "missing_quote"


def test_job_order_duplicate_execution_and_account_isolation() -> None:
    from time import monotonic

    from donghak_stock_vision.backtest.mass_simulation import Cancellation, RunBudget

    request = fixture()
    case = request.observations.cases[0]
    request = rebind(request, SyntheticObservations((case, replace(case, case_id="case-2"))))
    batch = execute_strategy_candidates(request)
    for owner in reversed(batch.inputs):
        for job in reversed(owner.experiment.jobs):
            first = owner.adapter.run(
                owner.experiment,
                job,
                owner.experiment.run_seed(job),
                RunBudget(owner.experiment, Cancellation(), monotonic),
            )
            again = owner.adapter.run(
                owner.experiment,
                job,
                owner.experiment.run_seed(job),
                RunBudget(owner.experiment, Cancellation(), monotonic),
            )
            assert first == again
            stored = next(
                r for r in owner.checkpoint.to_dict()["records"] if r["job_key"] == job.key
            )
            assert first.to_dict() == stored["output"]


@pytest.mark.parametrize("target", ["decision", "order", "instance", "outcome"])
def test_rehashed_execution_tampering(target: str) -> None:
    request = fixture()
    batch = execute_strategy_candidates(request)
    value = batch.report.to_dict()
    row = value["candidates"][0]
    if target == "decision":
        row["decisions"][0]["action"] = "WAIT"
    elif target == "order":
        row["decisions"][0]["order"]["quantity"]["shares"] = 99
    elif target == "instance":
        row["strategy_instance"]["candidate"]["parameters"]["entry"] = "9"
    else:
        row["runs"][0]["result_fingerprint"] = "0" * 64
    from donghak_stock_vision.data.learning import digest

    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    forged = replace(batch, report=FrozenJSON.freeze(value))
    with pytest.raises(ContractError, match="decision_replay"):
        verify_strategy_execution(forged, value["content_fingerprint"], request)


def test_modified_paper_owner_rejected_even_if_report_unchanged() -> None:
    request = fixture()
    batch = execute_strategy_candidates(request)
    owner = batch.inputs[0]
    value = owner.checkpoint.to_dict()
    value["records"][0]["output"]["execution"]["fills"] = []
    changed = FrozenJSON.freeze(value)
    owner = replace(owner, checkpoint=changed, trusted_checkpoint_hash=changed.identifier)
    with pytest.raises(ContractError, match="paper_owner_mismatch"):
        verify_strategy_execution(
            replace(batch, inputs=(owner, *batch.inputs[1:])),
            batch.report.to_dict()["content_fingerprint"],
            request,
        )


def test_seed_and_parameter_identity_are_bound() -> None:
    request = fixture()
    original = execute_strategy_candidates(request)
    plan = replace(request.plan, seed=request.plan.seed + 1)
    changed = replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
    assert original.report != execute_strategy_candidates(changed).report
    with pytest.raises(ContractError, match="decision_replay"):
        verify_strategy_execution(
            original, original.report.to_dict()["content_fingerprint"], changed
        )


def test_resource_caps_before_compilation(monkeypatch: pytest.MonkeyPatch) -> None:
    request = fixture()
    plan = replace(request.plan, limits=replace(request.plan.limits, max_runs=1))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No compile beyond budget")

    monkeypatch.setattr("donghak_stock_vision.backtest.strategy_execution._compile", forbidden)
    with pytest.raises(ContractError, match="run_budget"):
        execute_strategy_candidates(
            replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
        )


def economic_inputs(
    request: StrategyExecutionRequest,
    batch: Any,
    *,
    missing: bool = False,
    flow: str | None = "replayed_closed_paper_commands_v1",
) -> tuple[Any, ...]:
    from donghak_stock_vision.backtest.paper_valuation import ValuationEvidence
    from donghak_stock_vision.backtest.strategy_execution_analysis import PinnedValuation

    marks = []
    for owner in batch.inputs:
        for row in owner.checkpoint.to_dict()["records"]:
            if row["status"] != "succeeded":
                continue
            case = next(c for c in request.observations.cases if c.case_id == row["job_key"])
            evidence = ValuationEvidence(
                "independently-pinned-synthetic-marks",
                owner.experiment.fingerprint,
                row["run_id"],
                row["output"]["execution_fingerprint"],
                case.steps[0].decision_frame.now,
                case.steps[-1].execution_frame.now,
                case.steps[0].decision_frame.quotes,
                () if missing else case.steps[-1].execution_frame.quotes,
                60,
                flow,
            )
            marks.append(PinnedValuation(evidence, evidence.document.identifier))
    return tuple(marks)


@pytest.mark.parametrize("instrument", [KR, US])
def test_original_valuation_and_comparison_exact_economics(instrument: Instrument) -> None:
    from donghak_stock_vision.backtest.strategy_execution_analysis import (
        analyze_strategy_execution,
        verify_strategy_analysis,
    )

    request = fixture(instrument)
    batch = execute_strategy_candidates(request)
    p = policy(cohort_id="cohort-tuning")
    marks = economic_inputs(request, batch)
    analysis = analyze_strategy_execution(
        batch, batch.report.to_dict()["content_fingerprint"], request, p, p.identifier, marks
    )
    value = analysis.report.to_dict()
    assert value["candidate_count"] == value["executed_candidate_count"] == 2
    assert [e["return_statistics"]["mean"] for e in value["comparison"]["experiments"]] == [
        {"numerator": "1", "denominator": "450"},
        {"numerator": "0", "denominator": "1"},
    ]
    assert value["comparison"]["paired_comparisons"][0]["statistics"]["mean"] == {
        "numerator": "-1",
        "denominator": "450",
    }
    assert value["ranking"] is None
    verify_strategy_analysis(
        analysis,
        value["content_fingerprint"],
        batch,
        batch.report.to_dict()["content_fingerprint"],
        request,
        p,
        p.identifier,
        marks,
    )


def test_missing_economic_evidence_stays_unavailable() -> None:
    from donghak_stock_vision.backtest.strategy_execution_analysis import analyze_strategy_execution

    request = fixture()
    batch = execute_strategy_candidates(request)
    p = policy(cohort_id="cohort-tuning")
    report = analyze_strategy_execution(
        batch, batch.report.to_dict()["content_fingerprint"], request, p, p.identifier, ()
    ).report.to_dict()
    for row in report["comparison"]["experiments"]:
        assert row["return_statistics"]["evaluated_count"] == 0
        assert row["return_statistics"]["mean"] is None
        assert row["runs"][0]["unavailable_reason"] == "valuation_not_supplied"


def test_analysis_keeps_failed_candidates_in_coverage() -> None:
    from donghak_stock_vision.backtest.strategy_execution_analysis import analyze_strategy_execution

    request = fixture(entries=("10", "13"))
    batch = execute_strategy_candidates(request)
    p = policy(cohort_id="cohort-tuning")
    value = analyze_strategy_execution(
        batch,
        batch.report.to_dict()["content_fingerprint"],
        request,
        p,
        p.identifier,
        economic_inputs(request, batch),
    ).report.to_dict()
    assert value["failed_configuration_count"] == 1 and value["candidate_count"] == 2
    assert value["coverage_warning"] == "executed_candidate_subset_only"
    assert value["comparison"] is None


def test_legacy_experiment_summary_retains_honest_namespace_limitation() -> None:
    from donghak_stock_vision.backtest.strategy_evaluation import summarize_experiment
    from donghak_stock_vision.backtest.strategy_execution_analysis import (
        legacy_evaluation_assessments,
    )

    request = fixture()
    batch = execute_strategy_candidates(request)
    assessments = legacy_evaluation_assessments(
        batch, batch.report.to_dict()["content_fingerprint"], request
    )
    report = summarize_experiment(
        request.plan,
        request.trusted_plan_hash,
        assessments,
        tuple(a.document.identifier for a in assessments),
    ).to_dict()
    assert report["unavailable_count"] == 2
    assert all(r["reason"] == "evaluation_not_supported" for r in report["candidates"])


def test_rehashed_analysis_and_forged_marks_rejected() -> None:
    from donghak_stock_vision.backtest.strategy_execution_analysis import (
        analyze_strategy_execution,
        verify_strategy_analysis,
    )
    from donghak_stock_vision.data.learning import digest

    request = fixture()
    batch = execute_strategy_candidates(request)
    pin = batch.report.to_dict()["content_fingerprint"]
    p = policy(cohort_id="cohort-tuning")
    marks = economic_inputs(request, batch)
    analysis = analyze_strategy_execution(batch, pin, request, p, p.identifier, marks)
    value = analysis.report.to_dict()
    value["comparison"]["experiments"][0]["return_statistics"]["mean"]["numerator"] = "999"
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="analysis_replay"):
        verify_strategy_analysis(
            replace(analysis, report=FrozenJSON.freeze(value)),
            value["content_fingerprint"],
            batch,
            pin,
            request,
            p,
            p.identifier,
            marks,
        )
    evidence = replace(marks[0].evidence, execution_fingerprint="0" * 64)
    forged = replace(marks[0], evidence=evidence, trusted_hash=evidence.document.identifier)
    with pytest.raises(ContractError):
        analyze_strategy_execution(batch, pin, request, p, p.identifier, (forged, *marks[1:]))


def test_no_lookahead_from_execution_prices() -> None:
    request = fixture(entries=("10",))
    first = execute_strategy_candidates(request)
    case = request.observations.cases[0]
    execution = case.steps[0].execution_frame
    quote = replace(execution.quotes[0], price=Money(Decimal("10.01"), case.instrument.currency))
    step = replace(case.steps[0], execution_frame=replace(execution, quotes=(quote,)))
    changed = rebind(
        request, SyntheticObservations((replace(case, steps=(step, *case.steps[1:])),))
    )
    second = execute_strategy_candidates(changed)
    assert [d["action"] for d in first.report.to_dict()["candidates"][0]["decisions"]] == [
        d["action"] for d in second.report.to_dict()["candidates"][0]["decisions"]
    ]


def test_partial_fill_cancel_and_insufficient_shares_preserve_accounting() -> None:
    request = fixture(entries=("10",))
    plan = replace(
        request.plan,
        grid=(*request.plan.grid[:2], ParameterGrid("quantity", "integer", (2,), "1", "100")),
    )
    request = replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
    batch = execute_strategy_candidates(request)
    execution = batch.inputs[0].checkpoint.to_dict()["records"][0]["output"]["execution"]
    assert [f["quantity"]["shares"] for f in execution["fills"]] == [1]
    assert execution["ending_positions"][0]["quantity"]["shares"] == 1
    assert execution["counts"]["cancellations"] == 1  # buy remainder cancelled; oversell denied
    assert execution["counts"]["risk_denied_orders"] == 1


def test_network_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No external network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    request = fixture(entries=("10",))
    before = request.observations.document
    batch = execute_strategy_candidates(request)
    verify_strategy_execution(batch, batch.report.to_dict()["content_fingerprint"], request)
    assert request.observations.document == before


@pytest.mark.parametrize("condition", ["stale", "closed"])
def test_stale_quote_and_closed_market_keep_risk_gate(condition: str) -> None:
    request = fixture(entries=("10",))
    case = request.observations.cases[0]
    frame = case.steps[0].decision_frame
    if condition == "stale":
        q = replace(
            frame.quotes[0],
            observed_at=T - timedelta(seconds=120),
            available_at=T - timedelta(seconds=120),
        )
        frame = replace(frame, quotes=(q,))
    else:
        frame = replace(frame, sessions=tuple(replace(s, is_open=False) for s in frame.sessions))
    execution_frame = case.steps[0].execution_frame
    if condition == "closed":
        execution_frame = replace(
            execution_frame,
            sessions=tuple(replace(s, is_open=False) for s in execution_frame.sessions),
        )
    step = replace(case.steps[0], decision_frame=frame, execution_frame=execution_frame)
    source = SyntheticObservations((replace(case, steps=(step, *case.steps[1:])),))
    batch = execute_strategy_candidates(rebind(request, source))
    execution = batch.inputs[0].checkpoint.to_dict()["records"][0]["output"]["execution"]
    assert execution["counts"]["fills"] == 0
    assert execution["counts"]["risk_denied_orders"] == (2 if condition == "stale" else 1)
    if condition == "closed":
        assert execution["orders"][0]["risk_allowed"] is True
        assert any("market_closed_or_unknown" in r["reasons"] for r in execution["receipts"])
    else:
        assert all(not order["risk_allowed"] for order in execution["orders"])


def test_recorded_costs_and_slippage_use_existing_valuation() -> None:
    from donghak_stock_vision.backtest.strategy_execution_analysis import analyze_strategy_execution

    request = fixture(
        entries=("10",),
        buy_fee_rate=Decimal("0.001"),
        sell_fee_rate=Decimal("0.001"),
        sell_tax_rate=Decimal("0.002"),
        slippage_rate=Decimal("0.01"),
    )
    batch = execute_strategy_candidates(request)
    p = policy(cohort_id="cohort-tuning")
    analysis = analyze_strategy_execution(
        batch,
        batch.report.to_dict()["content_fingerprint"],
        request,
        p,
        p.identifier,
        economic_inputs(request, batch),
    ).report.to_dict()
    output = batch.inputs[0].checkpoint.to_dict()["records"][0]["output"]["execution"]
    assert output["costs"]["fees"]["amount"] == "0.04"
    assert output["costs"]["taxes"]["amount"] == "0.03"
    assert output["costs"]["slippage"]["amount"] == "0.22"
    assert output["ending_cash"][0]["available"]["amount"] == "901.71"
    valuation = analysis["valuations"][0]["valuation"]
    assert valuation["performance"]["total_pnl"]["amount"] == "1.71"


def test_candidate_cap_is_explicit_and_bounded() -> None:
    request = fixture(entries=("1", "2", "3", "4", "5", "6"))
    with pytest.raises(ContractError, match="candidate_limit"):
        execute_strategy_candidates(request)


def test_partial_execution_failure_stays_in_results() -> None:
    request = fixture()
    plan = replace(request.plan, limits=replace(request.plan.limits, max_steps_per_run=1))
    batch = execute_strategy_candidates(
        replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
    )
    rows = batch.report.to_dict()["candidates"]
    assert all(r["status"] == "incomplete" for r in rows)
    assert all(r["runs"][0]["failure"]["code"] == "budget_exhausted" for r in rows)


def test_missing_provenance_cannot_authorize_observations() -> None:
    request = fixture()
    partitions = tuple(
        replace(p, provenance=ref("unrelated-custody")) if p.role == "tuning" else p
        for p in request.plan.partitions
    )
    plan = replace(request.plan, partitions=partitions)
    with pytest.raises(ContractError, match="partition_provenance"):
        execute_strategy_candidates(
            replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
        )


def test_derived_features_are_not_an_input_language() -> None:
    request = fixture()
    # The accepted source contract has no feature expression, score or executable field.
    with pytest.raises(TypeError):
        changes: dict[str, Any] = {"future_feature": "price_tomorrow"}
        replace(request.observations.cases[0].steps[0], **changes)


@pytest.mark.parametrize(
    "parameters",
    [
        {"entry": "10", "exit": "9", "quantity": 1},
        {"entry": "0", "exit": "12", "quantity": 1},
        {"entry": 10.0, "exit": "12", "quantity": 1},
        {"entry": "10", "exit": "12", "quantity": True},
        {"entry": "10", "exit": "12", "quantity": 101},
        {"entry": "10", "exit": "12", "quantity": 1, "risk": "allow"},
    ],
)
def test_reference_parameter_schema_is_exact(parameters: dict[str, Any]) -> None:
    from donghak_stock_vision.strategy.paper_reference import decide

    with pytest.raises(ContractError):
        decide(FrozenJSON.freeze(parameters), Decimal("10"))


def test_mixed_native_markets_are_rejected_before_execution() -> None:
    request = fixture()
    usd = fixture(US)
    mixed = rebind(request, usd.observations)
    with pytest.raises(ContractError, match="market_currency_mismatch"):
        execute_strategy_candidates(mixed)


def test_missing_terminal_mark_does_not_turn_unavailable_into_zero() -> None:
    from donghak_stock_vision.backtest.strategy_execution_analysis import analyze_strategy_execution

    request = fixture()
    plan = replace(
        request.plan,
        grid=(
            request.plan.grid[0],
            ParameterGrid("exit", "decimal", ("20",), "1", "20"),
            request.plan.grid[2],
        ),
    )
    request = replace(request, plan=plan, trusted_plan_hash=plan.document.identifier)
    batch = execute_strategy_candidates(request)
    p = policy(cohort_id="cohort-tuning")
    analysis = analyze_strategy_execution(
        batch,
        batch.report.to_dict()["content_fingerprint"],
        request,
        p,
        p.identifier,
        economic_inputs(request, batch, missing=True),
    )
    rows = analysis.report.to_dict()["comparison"]["experiments"]
    assert rows[0]["return_statistics"]["evaluated_count"] == 0
    assert rows[0]["return_statistics"]["mean"] is None
    assert rows[1]["return_statistics"]["mean"] == {"numerator": "0", "denominator": "1"}


def test_observation_content_tampering_cannot_be_rehashed_without_custody() -> None:
    request = fixture()
    case = request.observations.cases[0]
    step = case.steps[0]
    quote = replace(
        step.decision_frame.quotes[0], price=Money(Decimal("9"), case.instrument.currency)
    )
    changed = replace(
        case,
        steps=(
            replace(step, decision_frame=replace(step.decision_frame, quotes=(quote,))),
            *case.steps[1:],
        ),
    )
    source = SyntheticObservations((changed,))
    forged = replace(
        request, observations=source, trusted_observations_hash=source.document.identifier
    )
    with pytest.raises(ContractError, match="dataset_pin_mismatch"):
        execute_strategy_candidates(forged)


def test_explicit_single_candidate_boundary_matches_batch_compilation() -> None:
    from donghak_stock_vision.backtest.strategy_execution import prepare_strategy_candidate
    from donghak_stock_vision.backtest.strategy_experiment import candidates

    request = fixture()
    candidate = candidates(request.plan)[0]
    scenarios, provenance = prepare_strategy_candidate(request, candidate)
    batch = execute_strategy_candidates(request)
    row = batch.report.to_dict()["candidates"][0]
    assert provenance.to_dict()["decisions"] == row["decisions"]
    assert provenance.to_dict()["instance"] == row["strategy_instance"]
    assert scenarios[0] in batch.inputs[0].adapter.scenarios
    other = candidates(replace(request.plan, seed=request.plan.seed + 1))[0]
    with pytest.raises(ContractError, match="candidate_not_in_authorized_plan"):
        prepare_strategy_candidate(request, other)


def test_authorized_window_end_is_exclusive() -> None:
    request = fixture()
    auth = replace(
        request.authorization,
        window=TimeWindow(
            request.authorization.window.start,
            request.observations.cases[0].steps[-1].execution_frame.now,
        ),
    )
    partitions = tuple(
        replace(p, window=auth.window, provenance=ref("custody", auth.document.identifier))
        if p.role == auth.role
        else p
        for p in request.plan.partitions
    )
    plan = replace(request.plan, partitions=partitions)
    with pytest.raises(ContractError, match="outside_authorized_partition"):
        execute_strategy_candidates(
            replace(
                request,
                plan=plan,
                trusted_plan_hash=plan.document.identifier,
                authorization=auth,
                trusted_authorization_hash=auth.document.identifier,
            )
        )
