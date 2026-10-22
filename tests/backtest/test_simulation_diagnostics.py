"""Evidence-only diagnostics, using the actual isolated Paper Broker fixtures."""

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
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter
from donghak_stock_vision.backtest.simulation_diagnostics import (
    VerifiedDiagnosticReport,
    analyze_simulations,
    compare_diagnostics,
    failure_category,
    observation_category,
)
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, SimulationJob
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Instrument, Money, OrderSide, Quantity
from donghak_stock_vision.trading.paper_types import PaperCommand, Submission
from tests.backtest.test_paper_simulation import experiment, scenario
from tests.trading.test_paper import next_frame
from tests.trading.test_risk import KR, US, T


def analyze(source: PaperScenario, count: int = 1) -> VerifiedDiagnosticReport:
    adapter = PaperSimulationAdapter((source,))
    spec = experiment(adapter, count)
    scheduler = BatchScheduler(spec)
    scheduler.run(adapter)
    checkpoint = scheduler.checkpoint()
    return analyze_simulations(spec, checkpoint, checkpoint.identifier, adapter, max_jobs=10)


@pytest.mark.parametrize("instrument", [KR, US])
def test_factual_report_exact_costs_partial_cancel_and_determinism(instrument: Instrument) -> None:
    source = scenario(instrument)
    before = source.document
    first, second = analyze(source, 2), analyze(source, 2)
    assert first.to_dict() == second.to_dict()
    value = first.to_dict()
    assert first.fingerprint == digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    assert value["scope"]["market"] == instrument.market.value
    assert value["scope"]["currency"] == instrument.currency.value
    assert value["job_counts"]["completed"] == value["verification"]["replayed_jobs"] == 2
    assert value["execution_counts"]["fills"] == 4
    assert value["execution_counts"]["partially_filled_orders"] == 2
    assert value["execution_counts"]["cancelled_orders"] == 2
    assert value["execution_counts"]["unfilled_orders"] == 2
    assert value["rates"]["order_fill"] == {
        "numerator": 4,
        "denominator": 4,
        "unavailable_reason": None,
    }
    assert Decimal(value["execution_costs"]["fees"]) == Decimal("0.10")
    assert Decimal(value["execution_costs"]["taxes"]) == Decimal("0.08")
    assert Decimal(value["execution_costs"]["slippage"]) == Decimal("0.8")
    assert value["performance"]["pnl"] is value["performance"]["return"] is None
    assert not value["performance"]["available"]
    assert "missing_performance_evidence" in value["categories"]
    assert all(f["source"].startswith("records[") for f in value["observations"])
    assert source.document == before
    value["runs"].clear()
    assert len(first.to_dict()["runs"]) == 2
    with pytest.raises(AttributeError):
        first._pin = "tampered"


@pytest.mark.parametrize("case", ["cash", "shares", "disabled", "missing", "stale", "valuation"])
def test_risk_denials_are_observations_not_failed_jobs(case: str) -> None:
    source = scenario()
    submit = source.commands[0]
    assert isinstance(submit.payload, Submission)
    account, frame = source.initial_account, source.initial_frame
    if case == "cash":
        account = replace(
            account,
            balances=tuple(
                replace(b, available=Money(Decimal(1), KR.currency)) for b in account.balances
            ),
        )
    elif case == "shares":
        submit = replace(
            submit,
            payload=replace(
                submit.payload,
                order=replace(submit.payload.order, side=OrderSide.SELL, quantity=Quantity(11)),
            ),
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
    elif case == "valuation":
        frame = replace(frame, valuations=())
    value = analyze(
        replace(source, initial_account=account, initial_frame=frame, commands=(submit,))
    ).to_dict()
    assert value["job_counts"]["completed"] == 1
    assert value["job_counts"]["failed"] == 0
    assert value["execution_counts"]["risk_denied_orders"] == 1
    assert value["execution_counts"]["fills"] == 0
    assert value["risk_rejection_counts"]
    assert value["rates"]["risk_rejection"]["numerator"] == 1
    assert value["execution_counts"]["unfilled_orders"] == 0
    assert "simulation_adapter_failure" not in value["categories"]
    if case in {"missing", "stale", "valuation"}:
        assert value["warning_counts"]


@pytest.mark.parametrize("case", ["closed", "missing", "stale"])
def test_pending_execution_and_market_warnings(case: str) -> None:
    source = scenario()
    command = source.commands[1]
    frame = next_frame(source.initial_frame, 1)
    if case == "closed":
        frame = replace(frame, sessions=tuple(replace(s, is_open=False) for s in frame.sessions))
    elif case == "missing":
        frame = replace(frame, quotes=())
    else:
        frame = replace(
            frame,
            quotes=tuple(
                replace(
                    q, observed_at=T - timedelta(seconds=61), available_at=T - timedelta(seconds=61)
                )
                for q in frame.quotes
            ),
        )
    value = analyze(
        replace(
            source,
            commands=(source.commands[0], replace(command, payload=frame), source.commands[2]),
        )
    ).to_dict()
    assert value["execution_counts"]["fills"] == 0
    assert value["execution_counts"]["unfilled_orders"] == 1
    assert "market_observation_issue" in value["categories"]
    assert value["warning_counts"]


@pytest.mark.parametrize(
    "code,category",
    [
        (FailureCode.INVALID_CONFIGURATION, "experiment_configuration_failure"),
        (FailureCode.MISSING_INPUT, "missing_input"),
        (FailureCode.INCOMPATIBLE_INPUT, "unknown"),
        (FailureCode.EXECUTION_EXCEPTION, "simulation_adapter_failure"),
        (FailureCode.RISK_DENIED, "risk_denial"),
        (FailureCode.TIMEOUT, "orchestration_timeout"),
        (FailureCode.BUDGET_EXHAUSTED, "execution_budget_exhausted"),
        (FailureCode.CANCELLED, "orchestration_cancellation"),
        (FailureCode.INTERRUPTED, "orchestration_interruption"),
        (FailureCode.CHECKPOINT_MISMATCH, "result_integrity_failure"),
        (FailureCode.CHECKPOINT_CORRUPTION, "result_integrity_failure"),
        (FailureCode.RESULT_INTEGRITY, "result_integrity_failure"),
    ],
)
def test_recorded_failures_preserve_codes_without_speculation(
    code: FailureCode, category: str
) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)

    class Failing:
        reference = adapter.reference

        def run(
            self, exp: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            raise SimulationFault(code, "recorded_fixture_failure")

    scheduler = BatchScheduler(spec)
    scheduler.run(Failing())
    checkpoint = scheduler.checkpoint()
    value = analyze_simulations(
        spec, checkpoint, checkpoint.identifier, adapter, max_jobs=1
    ).to_dict()
    assert value["verification"]["replayed_jobs"] == 0
    assert value["verification"]["non_success_causes"] == "pinned_record_only"
    fact = value["observations"][0]
    assert fact["code"] == code.value
    assert fact["category"] == category
    assert value["rates"]["order_fill"]["unavailable_reason"] == "zero_denominator"


@pytest.mark.parametrize(
    "codes,category",
    [
        (
            (
                "reference_price_missing",
                "reference_price_invalid",
                "market_data_stale",
                "later_price_evidence_required",
                "session_evidence_unavailable",
                "market_closed_or_unknown",
            ),
            "market_observation_issue",
        ),
        (
            (
                "limit_not_marketable",
                "accepted_price_cap_exceeded",
                "invalid_execution_economics",
                "later_execution_event_required",
                "limit_price_quantum_mismatch",
                "native_currency_unsupported",
                "risk_evidence_missing",
                "risk_evidence_mismatch",
            ),
            "execution_constraint_violation",
        ),
        (
            ("valuation_missing", "valuation_invalid", "valuation_stale"),
            "risk_valuation_evidence_issue",
        ),
        (
            ("insufficient_available_cash", "insufficient_sellable_position"),
            "insufficient_buying_power_or_quantity",
        ),
        (("future_evidence",), "information_availability_issue"),
        (("new_unknown_code",), "unknown"),
        (
            (
                "invalid_order",
                "invalid_or_nonpositive_quantity",
                "missing_policy",
                "invalid_policy",
                "invalid_context",
                "trading_disabled_or_unknown",
                "instrument_market_not_allowed",
                "currency_mismatch",
                "account_mismatch",
                "fx_missing",
                "fx_invalid",
                "fx_stale",
                "account_stale",
                "cash_balance_missing",
                "buy_cost_buffer_missing",
                "buy_cost_buffer_invalid",
                "maximum_order_notional",
                "maximum_position_concentration",
                "maximum_portfolio_exposure",
                "daily_loss_limit",
                "numeric_bounds_exceeded",
            ),
            "risk_denial",
        ),
    ],
)
def test_explicit_observation_taxonomy(codes: tuple[str, ...], category: str) -> None:
    assert all(observation_category(code) == category for code in codes)


def test_invalid_instruction_has_recorded_specific_category() -> None:
    source = scenario()
    request = source.commands[0].payload
    assert isinstance(request, Submission)
    conflict = PaperCommand(
        "submit", "different-key", replace(request, idempotency_key="different-key")
    )
    value = analyze(replace(source, commands=(source.commands[0], conflict))).to_dict()
    assert value["job_counts"]["failed"] == 1
    assert "invalid_trading_instruction" in value["categories"]
    assert failure_category(FailureCode.INCOMPATIBLE_INPUT, "scenario_binding_invalid") == (
        "invalid_scenario_input"
    )
    assert failure_category(FailureCode.INCOMPATIBLE_INPUT, "unrecognized") == "unknown"


def test_partial_cancelled_batch_empty_and_source_immutability() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 3)
    scheduler = BatchScheduler(spec)
    empty = scheduler.checkpoint()
    report = analyze_simulations(spec, empty, empty.identifier, adapter, max_jobs=3).to_dict()
    assert report["job_counts"]["unexamined"] == 3
    token = Cancellation()

    class StopAfterOne:
        reference = adapter.reference

        def run(
            self, exp: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            result = adapter.run(exp, job, seed, budget)
            token.cancel()
            return result

    scheduler.run(StopAfterOne(), cancellation=token)
    cp = scheduler.checkpoint()
    before = cp.payload_json
    value = analyze_simulations(spec, cp, cp.identifier, adapter, max_jobs=3).to_dict()
    assert value["job_counts"]["completed"] < 3
    assert value["job_counts"]["unexamined"] > 0
    assert cp.payload_json == before


@pytest.mark.parametrize(
    "change",
    [
        "pin",
        "version",
        "result",
        "rehashed",
        "experiment",
        "dataset",
        "strategy",
        "adapter",
        "budget",
    ],
)
def test_corrupt_or_mismatched_inputs_fail_closed(change: str) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    scheduler = BatchScheduler(spec)
    scheduler.run(adapter)
    cp = scheduler.checkpoint()
    value = cp.to_dict()
    pin, limit = cp.identifier, 1
    if change == "pin":
        pin = "0" * 64
    elif change == "version":
        value["version"] = "unknown_version"
        cp = FrozenJSON.freeze(value)
        pin = cp.identifier
    elif change in {"result", "rehashed"}:
        record = value["records"][0]
        output = record["output"]
        output["execution"]["counts"]["fills"] += 1
        if change == "rehashed":
            output["execution_fingerprint"] = digest(output["execution"])
            output["result_fingerprint"] = digest(
                {k: v for k, v in output.items() if k != "result_fingerprint"}
            )
            record["result_hash"] = digest({k: v for k, v in record.items() if k != "result_hash"})
        cp = FrozenJSON.freeze(value)
        pin = cp.identifier
    elif change == "experiment":
        spec = replace(spec, experiment_id="different-experiment")
    elif change == "dataset":
        spec = replace(
            spec, dataset=FrozenJSON.freeze({**spec.dataset.to_dict(), "content_hash": "0" * 64})
        )
    elif change == "strategy":
        spec = replace(
            spec, strategy=FrozenJSON.freeze({**spec.strategy.to_dict(), "content_hash": "0" * 64})
        )
    elif change == "adapter":
        adapter = PaperSimulationAdapter((scenario(US),))
    elif change == "budget":
        limit = 0
    with pytest.raises(ValueError):
        analyze_simulations(spec, cp, pin, adapter, max_jobs=limit)


def test_comparison_is_ordered_and_rejects_unknown_owners_and_scopes() -> None:
    a, b = analyze(scenario()), analyze(scenario(), 2)
    value = compare_diagnostics((b, a)).to_dict()
    assert value["report_hashes"] == [b.fingerprint, a.fingerprint]
    assert [r["job_counts"]["total"] for r in value["items"]] == [2, 1]
    assert value == compare_diagnostics((b, a)).to_dict()
    for reports in [(a,), (a, a), (a, analyze(scenario(US))), (a, analyze(scenario(limit=True)))]:
        with pytest.raises(ValueError):
            compare_diagnostics(reports)
    with pytest.raises(ValueError, match="verified_diagnostic"):
        compare_diagnostics((a, FrozenJSON.freeze(a.to_dict())))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="factory_required"):
        VerifiedDiagnosticReport(FrozenJSON.freeze(a.to_dict()))


def test_successful_analysis_cannot_skip_explicit_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 1)
    scheduler = BatchScheduler(spec)
    scheduler.run(adapter)
    cp = scheduler.checkpoint()
    calls: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> None:
        calls.append(args)
        raise ValueError("semantic_fixture_failure")

    monkeypatch.setattr(PaperSimulationAdapter, "replay_validate", refuse)
    with pytest.raises(SimulationFault, match="diagnostic_semantic_replay_failed"):
        analyze_simulations(spec, cp, cp.identifier, adapter, max_jobs=1)
    assert len(calls) == 1


def test_mixed_success_failure_cancellation_and_resume_history() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, 4)

    class Mixed:
        reference = adapter.reference

        def run(
            self, exp: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            if job == exp.jobs[1]:
                raise SimulationFault(FailureCode.EXECUTION_EXCEPTION, "fixture_failure")
            if job == exp.jobs[2]:
                raise SimulationFault(FailureCode.CANCELLED, "fixture_cancel")
            return adapter.run(exp, job, seed, budget)

    scheduler = BatchScheduler(spec)
    scheduler.run(Mixed())
    cp = scheduler.checkpoint()
    value = analyze_simulations(spec, cp, cp.identifier, adapter, max_jobs=4).to_dict()
    assert value["job_counts"] == {
        "total": 4,
        "observed": 3,
        "completed": 1,
        "failed": 1,
        "cancelled": 1,
        "interrupted": 0,
        "unexamined": 1,
    }
    assert value["evidence_availability"]["semantically_verified_jobs"] == 1
    assert value["execution_costs"]["fees"] == "0.05"
    restored = BatchScheduler(spec, checkpoint=cp, expected_checkpoint_hash=cp.identifier)
    restored.run(adapter)
    cp2 = restored.checkpoint()
    later = analyze_simulations(spec, cp2, cp2.identifier, adapter, max_jobs=4).to_dict()
    assert later["job_counts"]["completed"] == 3
    assert later["job_counts"]["failed"] == 1
    assert later["attempt_counts"]["cancelled"] == 1
    assert "orchestration_cancellation" in later["categories"]
    assert cp.to_dict() == scheduler.checkpoint().to_dict()


def test_comparison_allows_strategy_versions_but_rejects_period_mismatch() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    original = experiment(adapter, 1)

    def project(spec: ExperimentSpec) -> VerifiedDiagnosticReport:
        scheduler = BatchScheduler(spec)
        scheduler.run(adapter)
        cp = scheduler.checkpoint()
        return analyze_simulations(spec, cp, cp.identifier, adapter, max_jobs=1)

    a = project(original)
    changed = replace(
        original,
        strategy=FrozenJSON.freeze(
            {**original.strategy.to_dict(), "version": "candidate-v2", "content_hash": "1" * 64}
        ),
    )
    b = project(changed)
    assert (
        compare_diagnostics((a, b)).to_dict()["items"][1]["strategy"]["version"] == "candidate-v2"
    )
    c = project(replace(original, parameters=FrozenJSON.freeze({"period": "different"})))
    with pytest.raises(ValueError, match="incompatible_diagnostic_scopes"):
        compare_diagnostics((a, c))


def test_analysis_and_comparison_never_access_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("external network is forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    compare_diagnostics((analyze(scenario()), analyze(scenario(), 2)))
