"""Native synthetic economic evidence; no live providers or changes to execution policy."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler
from donghak_stock_vision.backtest.paper_scenario import PaperScenario
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter
from donghak_stock_vision.backtest.paper_valuation import (
    FLOW_SCOPE,
    ValuationEvidence,
    value_paper_simulation,
    verify_valuation_report,
)
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Instrument, Money, OrderSide, Quantity
from donghak_stock_vision.trading.paper_types import PaperCommand, Submission
from donghak_stock_vision.trading.risk import MarketQuote
from tests.backtest.test_paper_simulation import experiment, scenario
from tests.trading.test_paper import event, fixture, next_frame
from tests.trading.test_risk import KR, US


def book(
    trades: tuple[tuple[str, int, str], ...] = (),
    instrument: Instrument = KR,
    *,
    initial: bool = False,
    costs: bool = False,
) -> PaperScenario:
    """Advance/submit/advance/fill per trade, no assumed liquidity or prices."""
    broker, template, first = fixture(instrument)
    cp = broker.checkpoint()
    commands: list[PaperCommand] = []
    step = 0
    for i, (side, quantity, price) in enumerate(trades):
        frame = next_frame(first, step, price)
        commands.append(PaperCommand("advance", frame.frame_id, frame))
        order = replace(
            template,
            order_id=f"order-{i}",
            side=OrderSide(side),
            quantity=Quantity(quantity),
            created_at=frame.now,
        )
        commands.append(
            PaperCommand("submit", f"submit-{i}", Submission(order, f"submit-{i}", None))
        )
        step += 1
        frame = next_frame(first, step, price)
        commands.append(PaperCommand("advance", frame.frame_id, frame))
        fill = event(order, quantity, step, f"fill-{i}")
        commands.append(PaperCommand("fill", fill.event_id, fill))
        step += 1
    if not commands:
        frame = next_frame(first, 1)
        commands.append(PaperCommand("advance", frame.frame_id, frame))
    policy = replace(cp.policy, max_commands=64)
    if not costs:
        policy = replace(
            policy,
            buy_fee_rate=Decimal(0),
            sell_fee_rate=Decimal(0),
            sell_tax_rate=Decimal(0),
            slippage_rate=Decimal(0),
        )
    return PaperScenario(
        "synthetic-valuation",
        instrument.market,
        instrument.currency,
        replace(cp.initial_account, positions=cp.initial_account.positions if initial else ()),
        policy,
        first,
        tuple(commands),
    )


def inputs(
    source: PaperScenario,
) -> tuple[PaperSimulationAdapter, ExperimentSpec, FrozenJSON, ValuationEvidence]:
    adapter = PaperSimulationAdapter((source,))
    spec = experiment(adapter, 1)
    scheduler = BatchScheduler(spec)
    records = scheduler.run(adapter)
    record = records[0].to_dict()
    assert record["status"] == "succeeded", record
    result = FrozenJSON.freeze(record["output"])
    final = source.initial_frame
    for command in source.commands:
        if command.kind == "advance":
            final = command.payload  # type: ignore[assignment]
    evidence = ValuationEvidence(
        "synthetic-pinned-marks",
        spec.fingerprint,
        spec.run_id(spec.jobs[0]),
        result.to_dict()["execution_fingerprint"],
        source.initial_frame.now,
        final.now,
        source.initial_frame.quotes,
        final.quotes,
        60,
        FLOW_SCOPE,
    )
    return adapter, spec, result, evidence


def project(
    data: tuple[PaperSimulationAdapter, ExperimentSpec, FrozenJSON, ValuationEvidence],
    *,
    evidence: ValuationEvidence | None = None,
) -> FrozenJSON:
    adapter, spec, result, original = data
    evidence = original if evidence is None else evidence
    return value_paper_simulation(
        spec,
        spec.jobs[0],
        result,
        result.to_dict()["result_fingerprint"],
        adapter,
        evidence,
        evidence.document.identifier,
    )


def number(metric: dict[str, Any]) -> Fraction:
    assert metric["available"], metric
    value = metric["exact_ratio"]
    return Fraction(int(value["numerator"]), int(value["denominator"]))


@pytest.mark.parametrize("instrument", [KR, US])
@pytest.mark.parametrize("initial", [False, True])
def test_no_trade_cash_and_flat_portfolios(instrument: Instrument, initial: bool) -> None:
    data = inputs(book(instrument=instrument, initial=initial))
    report = project(data).to_dict()
    assert report["currency"] == instrument.currency.value
    assert report["market"] == instrument.market.value
    assert number(report["opening"]["equity"]) == (1000 if initial else 900)
    assert number(report["closing"]["equity"]) == (1000 if initial else 900)
    assert number(report["performance"]["total_pnl"]) == 0
    assert number(report["performance"]["total_return"]) == 0
    assert number(report["performance"]["realized_pnl"]) == 0
    assert report["performance"]["unrealized_pnl"]["available"] is not initial
    assert report["executed_amounts"] == {"purchases_gross": "0", "sales_gross": "0"}


@pytest.mark.parametrize("instrument", [KR, US])
def test_buy_hold_explicit_mark_and_net_equity_not_cash_delta(instrument: Instrument) -> None:
    data = inputs(book((("BUY", 5, "10"),), instrument))
    evidence = data[3]
    evidence = replace(
        evidence,
        closing_marks=tuple(
            replace(q, price=Money(Decimal(12), instrument.currency))
            for q in evidence.closing_marks
        ),
    )
    v = project(data, evidence=evidence).to_dict()
    assert v["closing"]["cash"]["total"] == "850"
    assert number(v["closing"]["equity"]) == 910
    assert number(v["performance"]["total_pnl"]) == 10
    assert number(v["performance"]["unrealized_pnl"]) == 10
    assert number(v["performance"]["total_return"]) == Fraction(1, 90)
    assert v["performance"]["total_return"]["amount"] is None
    assert v["performance"]["total_return"]["decimal_unavailable_reason"]
    position = v["closing"]["positions"][0]
    assert position["instrument"]["exchange"] == instrument.exchange.value
    assert number(position["cost_basis"]) == 50
    assert position["mark"]["source"].startswith("valuation_evidence.closing_marks[")


@pytest.mark.parametrize("price,pnl", [("12", 10), ("8", -10)])
def test_profitable_and_losing_full_sales(price: str, pnl: int) -> None:
    v = project(inputs(book((("BUY", 5, "10"), ("SELL", 5, price))))).to_dict()
    assert number(v["performance"]["total_pnl"]) == pnl
    assert number(v["performance"]["realized_pnl"]) == pnl
    assert number(v["performance"]["unrealized_pnl"]) == 0
    assert number(v["performance"]["realized_return"]) == Fraction(pnl, 50)
    assert v["closing"]["positions"] == []
    assert all(f["receipt_references"] for f in v["fills"])
    assert v["fills"][1]["source"] == "execution.fills[1]"


def test_average_cost_multiple_fills_partial_sale_and_exact_reconciliation() -> None:
    data = inputs(book((("BUY", 2, "10"), ("BUY", 2, "12"), ("SELL", 1, "15"))))
    v = project(data).to_dict()
    assert number(v["closing"]["positions"][0]["cost_basis"]) == 33
    assert number(v["fills"][2]["allocated_cost_basis"]) == 11
    assert number(v["performance"]["realized_pnl"]) == 4
    assert number(v["performance"]["unrealized_pnl"]) == 12
    assert number(v["performance"]["total_pnl"]) == 16
    assert number(v["closing"]["equity"]) == 916


def test_repeating_average_allocation_stays_exact_without_rounding() -> None:
    v = project(inputs(book((("BUY", 1, "10"), ("BUY", 2, "11"), ("SELL", 1, "12"))))).to_dict()
    assert number(v["fills"][2]["allocated_cost_basis"]) == Fraction(32, 3)
    assert v["fills"][2]["allocated_cost_basis"]["amount"] is None
    assert number(v["closing"]["positions"][0]["cost_basis"]) == Fraction(64, 3)
    assert number(v["performance"]["realized_pnl"]) == Fraction(4, 3)
    assert number(v["performance"]["unrealized_pnl"]) == Fraction(8, 3)
    assert number(v["performance"]["total_pnl"]) == 4


def test_fees_taxes_and_embedded_slippage_not_double_counted() -> None:
    v = project(inputs(book((("BUY", 5, "10"), ("SELL", 5, "12")), costs=True))).to_dict()
    assert v["execution_costs"]["fees"]["amount"] == "0.12"
    assert v["execution_costs"]["taxes"]["amount"] == "0.12"
    assert v["execution_costs"]["slippage"]["amount"] == "1.1"
    assert number(v["closing"]["equity"]) == Decimal("908.66")
    assert number(v["performance"]["realized_pnl"]) == Decimal("8.66")
    assert number(v["performance"]["total_pnl"]) == Decimal("8.66")


def test_initial_basis_is_not_supplied_by_opening_mark() -> None:
    v = project(inputs(book((("SELL", 2, "12"),), initial=True))).to_dict()
    assert number(v["performance"]["total_pnl"]) == 20
    assert not v["performance"]["realized_pnl"]["available"]
    assert not v["performance"]["unrealized_pnl"]["available"]
    assert v["closing"]["positions"][0]["cost_basis"]["reason"] == "initial_cost_basis_unavailable"


def test_reentry_after_unknown_initial_basis_recovers_only_terminal_basis() -> None:
    v = project(
        inputs(book((("SELL", 5, "12"), ("SELL", 5, "12"), ("BUY", 2, "8")), initial=True))
    ).to_dict()
    assert not v["performance"]["realized_pnl"]["available"]
    assert number(v["closing"]["positions"][0]["cost_basis"]) == 16
    assert number(v["performance"]["unrealized_pnl"]) == 0


def test_partial_fill_cancel_and_pending_reservations_count_equity_once() -> None:
    v = project(inputs(scenario())).to_dict()
    assert number(v["closing"]["equity"]) == Decimal("999.51")
    assert number(v["performance"]["total_pnl"]) == Decimal("-0.49")
    assert len(v["fills"]) == 2
    source = book((("BUY", 5, "10"),))
    source = replace(source, commands=source.commands[:-1])
    v = project(inputs(source)).to_dict()
    assert Decimal(v["closing"]["cash"]["reserved"]["amount"]) > 0
    assert number(v["closing"]["equity"]) == 900
    assert number(v["performance"]["total_pnl"]) == 0


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing", "missing_mark"),
        ("stale", "stale_mark"),
        ("future", "future_mark"),
        ("unavailable", "future_mark"),
    ],
)
def test_bad_closing_marks_never_produce_total_return(change: str, reason: str) -> None:
    data = inputs(book((("BUY", 5, "10"),)))
    evidence = data[3]
    q = evidence.closing_marks[0]
    marks: tuple[MarketQuote, ...]
    if change == "missing":
        marks = ()
    elif change == "stale":
        marks = (
            replace(
                q,
                observed_at=q.observed_at - timedelta(seconds=61),
                available_at=q.available_at - timedelta(seconds=61),
            ),
        )
    elif change == "future":
        marks = (
            replace(
                q,
                observed_at=q.observed_at + timedelta(seconds=1),
                available_at=q.available_at + timedelta(seconds=1),
            ),
        )
    else:
        marks = (replace(q, available_at=q.available_at + timedelta(seconds=1)),)
    v = project(data, evidence=replace(evidence, closing_marks=marks)).to_dict()
    assert not v["closing"]["equity"]["available"]
    assert v["closing"]["positions"][0]["market_value"]["reason"] == reason
    assert not v["performance"]["total_return"]["available"]
    assert not v["performance"]["total_pnl"]["available"]


def test_missing_opening_mark_does_not_guess_initial_equity_or_total_profit() -> None:
    data = inputs(book(initial=True))
    v = project(data, evidence=replace(data[3], opening_marks=())).to_dict()
    assert not v["opening"]["equity"]["available"]
    assert v["closing"]["equity"]["available"]
    assert not v["performance"]["total_pnl"]["available"]


def test_unverified_flows_preserve_value_change_but_no_investment_return() -> None:
    data = inputs(book((("BUY", 5, "10"), ("SELL", 5, "12"))))
    v = project(data, evidence=replace(data[3], cash_flow_scope=None)).to_dict()
    assert number(v["performance"]["net_account_value_change"]) == 10
    assert not v["performance"]["total_pnl"]["available"]
    assert v["performance"]["total_return"]["reason"] == "external_cash_flows_unverified"
    assert v["cash_flows"]["net_external_flow"] is None
    for key in ("total_return", "realized_return", "unrealized_return"):
        assert v["performance"][key]["reason"] == "external_cash_flows_unverified"


@pytest.mark.parametrize(
    "change",
    [
        "cross_currency",
        "exchange",
        "duplicate",
        "zero",
        "negative",
        "boundary",
        "binding",
        "cash_flow",
        "naive",
    ],
)
def test_invalid_or_ambiguous_valuation_evidence_is_rejected(change: str) -> None:
    data = inputs(book((("BUY", 5, "10"),)))
    evidence = data[3]
    q = evidence.closing_marks[0]
    with pytest.raises(ValueError):
        if change == "cross_currency":
            q = replace(q, instrument=US, price=Money(Decimal(10), US.currency))
            evidence = replace(evidence, closing_marks=(q,))
        elif change == "exchange":
            from donghak_stock_vision.trading.domain import Exchange

            q = replace(q, instrument=replace(KR, exchange=Exchange.KOSDAQ))
            evidence = replace(evidence, closing_marks=(q,))
        elif change == "duplicate":
            evidence = replace(evidence, closing_marks=(q, q))
        elif change in {"zero", "negative"}:
            replace(q, price=Money(Decimal(0 if change == "zero" else -1), KR.currency))
        elif change == "boundary":
            evidence = replace(evidence, closing_at=evidence.closing_at + timedelta(seconds=1))
        elif change == "binding":
            evidence = replace(evidence, execution_fingerprint="0" * 64)
        elif change == "cash_flow":
            evidence = replace(evidence, cash_flow_scope="caller_claims_deposit")
        else:
            evidence = replace(evidence, closing_at=evidence.closing_at.replace(tzinfo=None))
        project(data, evidence=evidence)


@pytest.mark.parametrize("change", ["price", "cost", "balance", "version", "fill_time"])
def test_execution_tampering_even_with_recomputed_hashes_needs_semantic_replay(change: str) -> None:
    adapter, spec, result, evidence = inputs(book((("BUY", 5, "10"),)))
    value = result.to_dict()
    execution = value["execution"]
    if change == "price":
        execution["fills"][0]["price"]["amount"] = "11"
    elif change == "cost":
        execution["costs"]["fees"]["amount"] = "0.99"
    elif change == "balance":
        execution["ending_account"]["balances"][0]["available"]["amount"] = "999"
    elif change == "version":
        value["version"] = "unknown"
    else:
        execution["fills"][0]["available_at"] = "2099-01-01T00:00:00+00:00"
    value["execution_fingerprint"] = digest(execution)
    value["result_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "result_fingerprint"}
    )
    evidence = replace(evidence, execution_fingerprint=value["execution_fingerprint"])
    with pytest.raises(ValueError):
        value_paper_simulation(
            spec,
            spec.jobs[0],
            FrozenJSON.freeze(value),
            value["result_fingerprint"],
            adapter,
            evidence,
            evidence.document.identifier,
        )


def test_pins_report_integrity_determinism_and_source_immutability() -> None:
    data = inputs(book((("BUY", 5, "10"), ("SELL", 2, "12"))))
    adapter, spec, result, evidence = data
    before = result.payload_json, evidence.document.payload_json
    a = project(data)
    assert a == project(data)
    with localcontext() as context:
        context.prec = 2
        assert a == project(data)
    v = a.to_dict()
    pin = v["content_fingerprint"]
    verify_valuation_report(
        a,
        pin,
        spec,
        spec.jobs[0],
        result,
        result.to_dict()["result_fingerprint"],
        adapter,
        evidence,
        evidence.document.identifier,
    )
    assert (result.payload_json, evidence.document.payload_json) == before
    altered = replace(
        evidence,
        closing_marks=tuple(
            replace(q, price=Money(Decimal(99), KR.currency)) for q in evidence.closing_marks
        ),
    )
    with pytest.raises(ValueError, match="evidence_pin_mismatch"):
        value_paper_simulation(
            spec,
            spec.jobs[0],
            result,
            result.to_dict()["result_fingerprint"],
            adapter,
            altered,
            evidence.document.identifier,
        )
    with pytest.raises(ValueError, match="pin_mismatch"):
        value_paper_simulation(
            spec, spec.jobs[0], result, "0" * 64, adapter, evidence, evidence.document.identifier
        )
    v["performance"]["total_pnl"]["amount"] = "999"
    v["content_fingerprint"] = digest({k: v for k, v in v.items() if k != "content_fingerprint"})
    with pytest.raises(ValueError, match="replay_mismatch"):
        verify_valuation_report(
            FrozenJSON.freeze(v),
            v["content_fingerprint"],
            spec,
            spec.jobs[0],
            result,
            result.to_dict()["result_fingerprint"],
            adapter,
            evidence,
            evidence.document.identifier,
        )


def test_zero_equity_has_zero_pnl_but_no_division_by_zero_return() -> None:
    source = book()
    balances = tuple(
        replace(b, available=Money(Decimal(0), KR.currency))
        for b in source.initial_account.balances
    )
    source = replace(source, initial_account=replace(source.initial_account, balances=balances))
    v = project(inputs(source)).to_dict()
    assert number(v["opening"]["equity"]) == number(v["closing"]["equity"]) == 0
    assert number(v["performance"]["total_pnl"]) == 0
    assert v["performance"]["total_return"]["reason"] == "nonpositive_return_denominator"


def test_mark_age_boundary_is_inclusive() -> None:
    data = inputs(book((("BUY", 5, "10"),)))
    q = data[3].closing_marks[0]
    evidence = replace(
        data[3],
        closing_marks=(
            replace(
                q,
                observed_at=q.observed_at - timedelta(seconds=60),
                available_at=q.available_at - timedelta(seconds=60),
            ),
        ),
    )
    assert project(data, evidence=evidence).to_dict()["performance"]["total_return"]["available"]


def test_valuation_requires_actual_replay_and_preserves_diagnostic_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from donghak_stock_vision.backtest.simulation_diagnostics import analyze_simulations

    data = inputs(book((("BUY", 5, "10"),)))
    adapter, spec, result, evidence = data
    scheduler = BatchScheduler(spec)
    scheduler.run(adapter)
    cp = scheduler.checkpoint()
    a = analyze_simulations(spec, cp, cp.identifier, adapter, max_jobs=1)
    project(data)
    b = analyze_simulations(spec, cp, cp.identifier, adapter, max_jobs=1)
    assert a.to_dict() == b.to_dict()
    assert a.to_dict()["version"] == "simulation_diagnostic_report_v1"
    assert not a.to_dict()["performance"]["available"]

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("explicit_replay_required")

    monkeypatch.setattr(PaperSimulationAdapter, "replay_validate", fail)
    with pytest.raises(ValueError, match="explicit_replay_required"):
        project((adapter, spec, result, evidence))


def test_no_network_and_detached_result_views(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Network access forbidden in synthetic valuation")

    monkeypatch.setattr(socket, "socket", forbidden)
    data = inputs(book((("BUY", 5, "10"),)))
    value = project(data)
    changed = value.to_dict()
    changed["closing"]["positions"].clear()
    assert value.to_dict()["closing"]["positions"]


@pytest.mark.parametrize("change", ["version", "digest", "flow_scope", "mark_amount"])
def test_modified_valuation_report_rejected_even_if_self_rehashed(change: str) -> None:
    data = inputs(book((("BUY", 5, "10"),)))
    adapter, spec, result, evidence = data
    original = project(data)
    value = original.to_dict()
    if change == "version":
        value["version"] = "unknown"
    elif change == "digest":
        value["content_fingerprint"] = "0" * 64
    elif change == "flow_scope":
        value["cash_flows"]["verified"] = False
    else:
        value["sources"]["valuation_evidence"]["closing_marks"][0]["price"]["amount"] = "999"
    if change != "digest":
        value["content_fingerprint"] = digest(
            {k: v for k, v in value.items() if k != "content_fingerprint"}
        )
    with pytest.raises(ValueError):
        verify_valuation_report(
            FrozenJSON.freeze(value),
            value["content_fingerprint"],
            spec,
            spec.jobs[0],
            result,
            result.to_dict()["result_fingerprint"],
            adapter,
            evidence,
            evidence.document.identifier,
        )
