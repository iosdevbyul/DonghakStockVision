"""Offline, isolated orchestration using the real Paper Broker and Risk Engine."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import (
    BatchScheduler,
    Cancellation,
    FailureCode,
    RunBudget,
    SimulationFault,
)
from donghak_stock_vision.backtest.paper_scenario import PaperScenario
from donghak_stock_vision.backtest.paper_simulation import (
    PaperSimulationAdapter,
    verify_paper_result,
)
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, SimulationJob
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import (
    CashBalance,
    Instrument,
    Money,
    OrderSide,
    OrderType,
    Quantity,
)
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_types import (
    Cancellation as PaperCancellation,
)
from donghak_stock_vision.trading.paper_types import (
    PaperCommand,
    Submission,
)
from tests.backtest.test_mass_simulation import experiment as base_experiment
from tests.trading.test_paper import event, fixture, next_frame
from tests.trading.test_risk import KR, US, T


def scenario(instrument: Instrument = KR, *, limit: bool = False) -> PaperScenario:
    broker, buy, initial = fixture(instrument)
    cp = broker.checkpoint()
    if limit:
        buy = replace(
            buy,
            order_type=OrderType.LIMIT,
            limit_price=Money(Decimal("10.10"), instrument.currency),
        )
    sell = replace(
        buy,
        order_id="sell1",
        side=OrderSide.SELL,
        quantity=Quantity(2),
        created_at=T + timedelta(seconds=1),
        limit_price=Money(Decimal("9.90"), instrument.currency) if limit else None,
    )
    commands = (
        PaperCommand("submit", "buy-key", Submission(buy, "buy-key", None)),
        PaperCommand("advance", "frame-1", next_frame(initial, 1)),
        PaperCommand("fill", "partial-buy", event(buy, 2, 1, "partial-buy")),
        PaperCommand(
            "cancel",
            "cancel-buy",
            PaperCancellation(buy.order_id, "cancel-buy", T + timedelta(seconds=1)),
        ),
        PaperCommand("submit", "sell-key", Submission(sell, "sell-key", None)),
        PaperCommand("advance", "frame-2", next_frame(initial, 2)),
        PaperCommand("fill", "sell-fill", event(sell, 2, 2, "sell-fill")),
    )
    return PaperScenario(
        f"{instrument.market.value}-roundtrip-{'limit' if limit else 'market'}",
        instrument.market,
        instrument.currency,
        cp.initial_account,
        replace(cp.policy, max_commands=64),
        initial,
        commands,
    )


def experiment(
    adapter: PaperSimulationAdapter, count: int = 10, scenario_id: str | None = None
) -> ExperimentSpec:
    selected = next(
        s for s in adapter.scenarios if scenario_id is None or s.scenario_id == scenario_id
    )
    base = base_experiment(count)
    return replace(
        base,
        experiment_id="synthetic-paper-integration",
        dataset=adapter.dataset_reference,
        runner=adapter.reference,
        execution_policy=adapter.execution_policy,
        market=selected.market,
        currency=selected.currency,
        jobs=tuple(adapter.job(f"paper-{i:03}", selected.scenario_id) for i in range(count)),
        limits=replace(
            base.limits,
            max_result_bytes=128 * 1024,
            max_total_result_bytes=16 * 1024 * 1024,
            max_steps_per_run=65,
            max_run_seconds=60,
        ),
    )


def budget(spec: ExperimentSpec, token: Cancellation | None = None) -> RunBudget:
    return RunBudget(spec, token if token is not None else Cancellation(), lambda: 0.0)


def output(scheduler: BatchScheduler, adapter: PaperSimulationAdapter) -> dict[str, Any]:
    result = scheduler.run(adapter)[0].to_dict()
    assert result["status"] == "succeeded", result
    return dict(result["output"])


@pytest.mark.parametrize("instrument", [KR, US])
@pytest.mark.parametrize("limit", [False, True])
def test_real_adapter_buy_sell_partial_cancel_and_exact_costs(
    instrument: Instrument, limit: bool
) -> None:
    source = scenario(instrument, limit=limit)
    before = source.document
    adapter = PaperSimulationAdapter((source,))
    spec = experiment(adapter, 1)
    result = output(BatchScheduler(spec), adapter)
    execution = result["execution"]
    assert execution["counts"] == {
        "orders": 2,
        "fills": 2,
        "rejections": 0,
        "risk_denied_orders": 0,
        "cancellations": 1,
        "pending_orders": 0,
        "instructions": 7,
        "committed_commands": 7,
    }
    assert execution["starting_cash"][0]["available"]["amount"] == "900"
    assert Decimal(execution["ending_cash"][0]["available"]["amount"]) == Decimal("899.51")
    assert execution["ending_cash"][0]["reserved"]["amount"] == "0"
    assert execution["ending_positions"][0]["quantity"]["shares"] == 10
    assert execution["ending_positions"][0]["reserved"]["shares"] == 0
    assert [o["status"] for o in execution["orders"]] == ["cancelled", "filled"]
    assert all(o["risk_allowed"] for o in execution["orders"])
    assert execution["costs"]["fees"]["amount"] == "0.05"
    assert execution["costs"]["taxes"]["amount"] == "0.04"
    assert execution["costs"]["slippage"]["amount"] == "0.4"
    assert execution["costs"]["slippage_already_in_fill_prices"] is True
    assert execution["valuation"]["available"] is False
    assert execution["valuation"]["portfolio_value"] is None
    assert execution["valuation"]["realized_pnl"] is None
    assert (
        result["market"] if "market" in result else execution["market"] == instrument.market.value
    )
    assert result["run_id"] == spec.run_id(spec.jobs[0])
    assert result["dataset"] == adapter.dataset_reference.to_dict()
    assert result["execution_policy_hash"] == spec.execution_policy.identifier
    assert source.document == before
    verify_paper_result(FrozenJSON.freeze(result), result["result_fingerprint"])
    adapter.replay_validate(spec, spec.jobs[0], FrozenJSON.freeze(result), budget(spec))


@pytest.mark.parametrize("count", [10, 100])
def test_bounded_batches_have_independent_accounts_and_stable_outputs(count: int) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, count)
    first, second = BatchScheduler(spec), BatchScheduler(spec)
    a = first.run(adapter)
    assert second.run(adapter) == a
    assert first.checkpoint() == second.checkpoint()
    assert len(a) == count
    outputs = [r.to_dict()["output"] for r in a]
    assert all(o is not None for o in outputs)
    assert len({o["execution_fingerprint"] for o in outputs}) == 1
    assert len({o["run_id"] for o in outputs}) == count
    assert all(o["execution"]["counts"]["fills"] == 2 for o in outputs)
    assert all(o["execution"]["ending_positions"][0]["quantity"]["shares"] == 10 for o in outputs)


def test_job_call_order_cannot_change_broker_results() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 3)
    forward = {j.key: adapter.run(spec, j, spec.run_seed(j), budget(spec)) for j in spec.jobs}
    backwards = {
        j.key: adapter.run(spec, j, spec.run_seed(j), budget(spec)) for j in reversed(spec.jobs)
    }
    assert forward == backwards
    reordered_spec = replace(spec, jobs=tuple(reversed(spec.jobs)))
    original = forward[spec.jobs[0].key].to_dict()
    reordered = adapter.run(
        reordered_spec, spec.jobs[0], spec.run_seed(spec.jobs[0]), budget(reordered_spec)
    ).to_dict()
    assert original["run_id"] != reordered["run_id"]  # Existing experiment-order identity contract.
    assert original["execution_fingerprint"] == reordered["execution_fingerprint"]


def test_mixed_registry_is_native_currency_isolated_and_registry_order_independent() -> None:
    kr, us = scenario(KR), scenario(US)
    adapter = PaperSimulationAdapter((kr, us))
    reverse = PaperSimulationAdapter((us, kr))
    assert adapter.dataset_reference == reverse.dataset_reference
    assert adapter.reference == reverse.reference
    for source in (kr, us):
        spec = experiment(adapter, 1, source.scenario_id)
        result = output(BatchScheduler(spec), adapter)
        execution = result["execution"]
        assert execution["currency"] == source.currency.value
        assert {b["available"]["currency"] for b in execution["ending_cash"]} == {
            source.currency.value
        }
        assert {f["price"]["currency"] for f in execution["fills"]} == {source.currency.value}
    invalid = replace(
        experiment(adapter, 1, kr.scenario_id), jobs=(adapter.job("cross", us.scenario_id),)
    )
    r = BatchScheduler(invalid).run(adapter)[0].to_dict()
    assert r["status"] == "failed" and r["failure"]["code"] == "incompatible_input"


@pytest.mark.parametrize(
    "case,expected",
    [
        ("cash", "insufficient_available_cash"),
        ("shares", "insufficient_sellable_position"),
        ("disabled", "trading_disabled_or_unknown"),
        ("missing", "reference_price_missing"),
        ("stale", "market_data_stale"),
        ("notional", "maximum_order_notional"),
        ("concentration", "maximum_position_concentration"),
        ("exposure", "maximum_portfolio_exposure"),
        ("loss", "daily_loss_limit"),
        ("valuation", "valuation_missing"),
    ],
)
def test_risk_denials_are_real_evidence_not_infrastructure_failures(
    case: str, expected: str
) -> None:
    source = scenario()
    order = source.commands[0].payload
    assert isinstance(order, Submission)
    account, policy, frame = source.initial_account, source.policy, source.initial_frame
    if case == "cash":
        account = replace(
            account,
            balances=(CashBalance(Money(Decimal(1), KR.currency), Money(Decimal(0), KR.currency)),),
        )
    elif case == "shares":
        order = replace(
            order, order=replace(order.order, side=OrderSide.SELL, quantity=Quantity(11))
        )
    elif case == "disabled":
        frame = replace(frame, trading_enabled=False)
    elif case == "missing":
        frame = replace(frame, quotes=())
    elif case == "stale":
        frame = replace(
            frame,
            quotes=tuple(
                replace(
                    q, observed_at=T - timedelta(seconds=61), available_at=T - timedelta(seconds=61)
                )
                for q in frame.quotes
            ),
        )
    elif case in {"notional", "concentration", "exposure"}:
        options: dict[str, dict[str, Any]] = {
            "notional": {"max_order_notional": Money(Decimal(1), KR.currency)},
            "concentration": {"max_position_fraction": Decimal("0.01")},
            "exposure": {"max_exposure_fraction": Decimal("0.01")},
        }
        policy = replace(policy, risk_policies=(replace(policy.risk_policies[0], **options[case]),))
    elif case == "loss":
        frame = replace(
            frame,
            valuations=tuple(
                replace(v, daily_pnl=Money(Decimal(-51), KR.currency)) for v in frame.valuations
            ),
        )
    elif case == "valuation":
        frame = replace(frame, valuations=())
    source = replace(
        source,
        initial_account=account,
        policy=policy,
        initial_frame=frame,
        commands=(replace(source.commands[0], payload=order),),
    )
    adapter = PaperSimulationAdapter((source,))
    result = output(BatchScheduler(experiment(adapter, 1)), adapter)
    execution = result["execution"]
    assert execution["counts"]["risk_denied_orders"] == 1
    assert execution["counts"]["rejections"] == 1
    assert execution["counts"]["fills"] == 0
    assert expected in [r["code"] for r in execution["receipts"][0]["risk"]["rejections"]]
    assert execution["ending_cash"] == execution["starting_cash"]
    assert execution["error_classification"] is None


@pytest.mark.parametrize(
    "case,reason",
    [
        ("closed", "market_closed_or_unknown"),
        ("missing", "reference_price_missing"),
        ("stale", "market_data_stale"),
        ("limit", "limit_not_marketable"),
    ],
)
def test_execution_eligibility_is_distinct_from_risk_approval(case: str, reason: str) -> None:
    source = scenario()
    frame_command = source.commands[1]
    frame = next_frame(source.initial_frame, 1)
    submit = source.commands[0]
    if case == "closed":
        frame = replace(frame, sessions=tuple(replace(s, is_open=False) for s in frame.sessions))
    elif case == "missing":
        frame = replace(frame, quotes=())
    elif case == "stale":
        frame = replace(
            frame,
            quotes=tuple(
                replace(
                    q, observed_at=T - timedelta(seconds=61), available_at=T - timedelta(seconds=61)
                )
                for q in frame.quotes
            ),
        )
    elif case == "limit":
        assert isinstance(submit.payload, Submission)
        submit = replace(
            submit,
            payload=replace(
                submit.payload,
                order=replace(
                    submit.payload.order,
                    order_type=OrderType.LIMIT,
                    limit_price=Money(Decimal("9"), KR.currency),
                ),
            ),
        )
    source = replace(
        source, commands=(submit, replace(frame_command, payload=frame), source.commands[2])
    )
    adapter = PaperSimulationAdapter((source,))
    result = output(BatchScheduler(experiment(adapter, 1)), adapter)["execution"]
    assert result["orders"][0]["risk_allowed"] is True
    assert result["counts"]["fills"] == 0 and result["counts"]["pending_orders"] == 1
    assert reason in result["receipts"][-1]["reasons"]
    assert Decimal(result["ending_cash"][0]["reserved"]["amount"]) > 0


def test_duplicate_instructions_do_not_duplicate_fills_or_reservations() -> None:
    source = scenario()
    source = replace(
        source,
        commands=(
            *source.commands[:3],
            source.commands[2],
            source.commands[0],
            *source.commands[3:],
        ),
    )
    adapter = PaperSimulationAdapter((source,))
    execution = output(BatchScheduler(experiment(adapter, 1)), adapter)["execution"]
    assert execution["counts"]["instructions"] == 9
    assert execution["counts"]["committed_commands"] == 7
    assert execution["counts"]["fills"] == 2 and execution["counts"]["orders"] == 2
    assert execution["costs"]["fees"]["amount"] == "0.05"


def test_duplicate_order_identity_with_new_key_is_isolated_failure() -> None:
    source = scenario()
    request = source.commands[0].payload
    assert isinstance(request, Submission)
    conflict = PaperCommand(
        "submit", "different-key", replace(request, idempotency_key="different-key")
    )
    broken = replace(source, scenario_id="broken", commands=(source.commands[0], conflict))
    adapter = PaperSimulationAdapter((source, broken))
    spec = experiment(adapter, 1, source.scenario_id)
    spec = replace(
        spec, jobs=(adapter.job("bad", broken.scenario_id), adapter.job("good", source.scenario_id))
    )
    results = BatchScheduler(spec).run(adapter)
    assert results[0].to_dict()["status"] == "failed"
    assert results[0].to_dict()["failure"]["code"] == "incompatible_input"
    assert results[1].to_dict()["output"]["execution"]["counts"]["fills"] == 2
    assert (
        results[1].to_dict()["output"]["execution"]["starting_cash"][0]["available"]["amount"]
        == "900"
    )


def test_resume_makes_zero_broker_calls_for_completed_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 3)
    token = Cancellation()
    calls: list[str] = []
    original = PaperSimulationAdapter.run

    def stopped(
        self: PaperSimulationAdapter,
        exp: ExperimentSpec,
        job: SimulationJob,
        seed: int,
        work: RunBudget,
    ) -> FrozenJSON:
        calls.append(job.key)
        if job.key == spec.jobs[1].key:
            token.cancel()
            work.check()
        return original(self, exp, job, seed, work)

    monkeypatch.setattr(PaperSimulationAdapter, "run", stopped)
    scheduler = BatchScheduler(spec)
    partial = scheduler.run(adapter, token)
    assert partial[0].to_dict()["status"] == "succeeded"
    assert partial[1].to_dict()["status"] == "cancelled"
    cp = scheduler.checkpoint()
    monkeypatch.setattr(PaperSimulationAdapter, "run", original)
    submissions: list[str] = []
    submit = DeterministicPaperBroker.submit

    def tracked(self: DeterministicPaperBroker, *args: Any, **kwargs: Any) -> Any:
        submissions.append("submit")
        return submit(self, *args, **kwargs)

    monkeypatch.setattr(DeterministicPaperBroker, "submit", tracked)
    restored = BatchScheduler(spec, checkpoint=cp, expected_checkpoint_hash=cp.identifier)
    results = restored.run(adapter)
    assert len(submissions) == 4  # Two orders each for only the two unfinished jobs.
    assert results[0] == partial[0]
    before = len(submissions)
    assert restored.run(adapter) == results and len(submissions) == before
    assert all(r.to_dict()["output"]["execution"]["counts"]["fills"] == 2 for r in results)


def test_mid_job_cancellation_discards_local_book_and_restarts_only_uncommitted_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    token = Cancellation()
    execute = DeterministicPaperBroker.execute

    def cancel_after_fill(self: DeterministicPaperBroker, *args: Any, **kwargs: Any) -> Any:
        receipt = execute(self, *args, **kwargs)
        token.cancel()
        return receipt

    monkeypatch.setattr(DeterministicPaperBroker, "execute", cancel_after_fill)
    scheduler = BatchScheduler(spec)
    interrupted = scheduler.run(adapter, token)[0].to_dict()
    assert interrupted["status"] == "cancelled" and interrupted["output"] is None
    monkeypatch.setattr(DeterministicPaperBroker, "execute", execute)
    result = scheduler.run(adapter)[0].to_dict()
    assert result["attempt"] == 2 and result["status"] == "succeeded"
    assert result["output"]["execution"]["counts"]["fills"] == 2
    assert result["output"]["execution"]["starting_cash"][0]["available"]["amount"] == "900"


@pytest.mark.parametrize(
    "change",
    ["dataset", "policy", "scenario_hash", "unknown_scenario", "extra_parameter", "real_origin"],
)
def test_input_binding_tampering_fails_closed(change: str) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    if change == "dataset":
        spec = replace(
            spec,
            dataset=FrozenJSON.freeze(
                {**adapter.dataset_reference.to_dict(), "content_hash": "0" * 64}
            ),
        )
    elif change == "policy":
        spec = replace(spec, execution_policy=FrozenJSON.freeze({"changed": True}))
    elif change == "real_origin":
        spec = replace(spec, data_origin="real")
    else:
        params = spec.jobs[0].parameters.to_dict()
        if change == "scenario_hash":
            params["scenario_hash"] = "0" * 64
        elif change == "unknown_scenario":
            params["scenario_id"] = "unregistered"
        else:
            params["arbitrary_path"] = "/private/forbidden"
        spec = replace(spec, jobs=(replace(spec.jobs[0], parameters=FrozenJSON.freeze(params)),))
    result = BatchScheduler(spec).run(adapter)[0].to_dict()
    assert result["status"] == "failed" and result["output"] is None
    assert result["failure"]["code"] == (
        "missing_input" if change == "unknown_scenario" else "incompatible_input"
    )


def test_result_tampering_needs_trusted_pin_or_explicit_semantic_replay() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    value = output(BatchScheduler(spec), adapter)
    pin = value["result_fingerprint"]
    value["execution"]["counts"]["fills"] += 1
    changed = FrozenJSON.freeze(value)
    with pytest.raises(ValueError, match="hash_mismatch"):
        verify_paper_result(changed, pin)
    value["execution_fingerprint"] = digest(value["execution"])
    value["result_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "result_fingerprint"}
    )
    forged = FrozenJSON.freeze(value)
    with pytest.raises(ValueError, match="pin_mismatch"):
        verify_paper_result(forged, pin)
    with pytest.raises(ValueError, match="replay_mismatch"):
        adapter.replay_validate(spec, spec.jobs[0], forged, budget(spec))


def test_explicit_replay_uses_original_broker_conservation_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    result = FrozenJSON.freeze(output(BatchScheduler(spec), adapter))
    checks: list[str] = []
    original = DeterministicPaperBroker._validate_state

    def guarded(self: DeterministicPaperBroker, state: Any) -> None:
        checks.append("validated")
        original(self, state)

    monkeypatch.setattr(DeterministicPaperBroker, "_validate_state", guarded)
    adapter.replay_validate(spec, spec.jobs[0], result, budget(spec))
    assert len(checks) > 7


def test_limits_are_not_relaxed_for_paper_jobs() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    too_few_steps = replace(spec, limits=replace(spec.limits, max_steps_per_run=1))
    result = BatchScheduler(too_few_steps).run(adapter)[0].to_dict()
    assert result["failure"]["code"] == "budget_exhausted"
    token = Cancellation()
    token.cancel()
    with pytest.raises(SimulationFault) as fault:
        adapter.run(spec, spec.jobs[0], spec.run_seed(spec.jobs[0]), budget(spec, token))
    assert fault.value.code is FailureCode.CANCELLED


def test_scenario_registry_and_commands_are_immutable_bounded_and_cannot_supply_grants() -> None:
    source = scenario()
    with pytest.raises(ValueError, match="duplicate_scenario_id"):
        PaperSimulationAdapter((source, source))
    with pytest.raises(ValueError, match="scenario_registry_limit"):
        PaperSimulationAdapter(())
    with pytest.raises(ValueError, match="scenario_command_limit"):
        replace(source, commands=source.commands * 10)
    with pytest.raises(ValueError, match="immutable_scenario_commands"):
        replace(source, commands=list(source.commands))  # type: ignore[arg-type]
    broker, order, _ = fixture()
    grant = broker.preflight(order).to_decision()
    request = source.commands[0].payload
    assert isinstance(request, Submission)
    with pytest.raises(ValueError, match="cannot_supply_risk_grant"):
        replace(
            source, commands=(replace(source.commands[0], payload=replace(request, risk=grant)),)
        )
    with pytest.raises(ValueError, match="native_settlement"):
        replace(source, initial_account=replace(source.initial_account, balances=()))
    assert source.document == scenario().document


def test_internal_risk_reassessment_cannot_be_replaced_by_preflight_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from donghak_stock_vision.trading.risk import RiskCode, RiskRejection
    from donghak_stock_vision.trading.risk import evaluate_risk as original

    calls: list[str] = []

    def denial(order: Any, context: Any, policy: Any) -> Any:
        calls.append("evaluated")
        assessment = original(order, context, policy)
        return replace(
            assessment, rejections=(RiskRejection(RiskCode.DISABLED, "trading_enabled"),)
        )

    source = scenario()
    source = replace(source, commands=(source.commands[0],))
    adapter = PaperSimulationAdapter((source,))
    monkeypatch.setattr("donghak_stock_vision.trading.paper.evaluate_risk", denial)
    execution = output(BatchScheduler(experiment(adapter, 1)), adapter)["execution"]
    assert len(calls) == 2  # Public preflight and mandatory internal acceptance check.
    assert execution["counts"]["risk_denied_orders"] == 1
    assert execution["counts"]["fills"] == 0
    assert execution["ending_cash"] == execution["starting_cash"]


def test_registered_scenario_changed_after_registration_does_not_match_dataset_pin() -> None:
    source = scenario()
    adapter = PaperSimulationAdapter((source,))
    spec = experiment(adapter, 1)
    object.__setattr__(source.initial_account, "source_id", "tampered-source")
    # Even a newly rehashed job may not replace registered dataset evidence.
    forged_job = adapter.job(spec.jobs[0].key, source.scenario_id)
    spec = replace(spec, jobs=(forged_job,))
    result = BatchScheduler(spec).run(adapter)[0].to_dict()
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "incompatible_input"
    assert result["output"] is None


@pytest.mark.parametrize("case", ["clock", "overfill", "unknown_order", "conflict"])
def test_bad_instructions_fail_closed_in_disposable_book(case: str) -> None:
    source = scenario()
    if case == "clock":
        command = replace(source.commands[1], payload=next_frame(source.initial_frame, -1))
        # Keep key consistent so the existing broker clock guard is the rejecting authority.
        command = replace(command, key=command.payload.frame_id)  # type: ignore[union-attr]
        source = replace(source, commands=(source.commands[0], command))
    elif case in {"overfill", "unknown_order"}:
        from donghak_stock_vision.trading.paper_types import FillEvent

        instruction = source.commands[2].payload
        assert isinstance(instruction, FillEvent)
        change = {"quantity": Quantity(6)} if case == "overfill" else {"order_id": "absent"}
        source = replace(
            source,
            commands=(
                *source.commands[:2],
                replace(source.commands[2], payload=replace(instruction, **change)),
            ),
        )
    else:
        request = source.commands[0].payload
        assert isinstance(request, Submission)
        changed = replace(request, order=replace(request.order, quantity=Quantity(3)))
        source = replace(
            source, commands=(source.commands[0], replace(source.commands[0], payload=changed))
        )
    adapter = PaperSimulationAdapter((source,))
    result = BatchScheduler(experiment(adapter, 1)).run(adapter)[0].to_dict()
    assert result["status"] == "failed" and result["output"] is None
    assert result["failure"]["code"] == "incompatible_input"


def test_output_retention_budget_is_not_enlarged_by_adapter() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    spec = replace(spec, limits=replace(spec.limits, max_result_bytes=100))
    result = BatchScheduler(spec).run(adapter)[0].to_dict()
    assert result["status"] == "failed"
    assert result["failure"] == {"code": "budget_exhausted", "detail": "paper_result_bytes"}
    assert result["output"] is None
