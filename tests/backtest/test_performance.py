"""Exact synthetic accounting examples, not market recommendations."""

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.performance import calculate_performance, drawdown
from donghak_stock_vision.backtest.report import format_report
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from tests.backtest.test_position_history import round_trip
from tests.backtest.test_runner import configure, rebuild, runner


def valuation_policy(**changes: Any) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": "explicit-synthetic-mark-v1",
            "field": "open",
            "selection": "latest_public_sequence",
            "max_age_seconds": 86400,
            "money_quantum": "0.01",
            "money_rounding": "half_up",
            "ratio_quantum": "0.00000001",
            "ratio_rounding": "half_up",
            "schedule": "start_events_end",
            "external_cash_flow_krw": "0",
            **changes,
        }
    )


@pytest.fixture(scope="module")
def sample() -> tuple[BacktestRunner, FrozenJSON]:
    r: BacktestRunner = round_trip()
    return r, r.run()


def test_runner_performance_end_to_end(sample: tuple[BacktestRunner, FrozenJSON]) -> None:
    r, result = sample
    p = calculate_performance(result, r.tape, valuation_policy()).to_dict()
    assert p["initial_equity"] == "10000"
    assert Decimal(p["final_equity"]) == Decimal("10159.73")
    assert p["total_return"] == "0.015973"
    assert p["realized_pnl"] == "170.55"
    assert p["unrealized_pnl"] == "-10.82"
    assert (p["completed_trades"], p["wins"], p["losses"], p["breakeven"], p["win_rate"]) == (
        1,
        1,
        0,
        0,
        "1",
    )
    assert p["fill_count"] == 3 and p["buy_fill_count"] == 2 and p["sell_fill_count"] == 1
    assert p["costs"] == {
        "fees": "6.91",
        "sell_taxes": "4.36",
        "slippage_impact": "29",
        "total_explicit_trading_costs": "11.27",
    }
    assert Decimal(p["final_equity"]) - Decimal(p["initial_equity"]) == (
        Decimal(p["realized_pnl"]) + Decimal(p["unrealized_pnl"])
    )
    assert p["mdd"]["ratio"] == "0.001082"
    assert p["mdd"]["peak"]["timestamp"].startswith("2026-01-05")
    assert p["mdd"]["trough"]["timestamp"].startswith("2026-01-06")
    assert all(e["complete"] for e in p["equity_curve"])


def test_replay_hash_report_and_decimal_context(sample: tuple[BacktestRunner, FrozenJSON]) -> None:
    r, result = sample
    policy = valuation_policy()
    first = calculate_performance(result, r.tape, policy)
    with localcontext() as ctx:
        ctx.prec = 4
        second = calculate_performance(result, r.tape, policy)
        report = format_report(second)
    assert first.identifier == second.identifier
    assert report == format_report(first)
    for phrase in (
        "Initial equity",
        "Final equity",
        "Total return",
        "Maximum drawdown",
        "Realized P&L",
        "Unrealized P&L",
        "Completed trades",
        "Win rate",
        "SYNTHETIC HISTORICAL",
        "NOT PIT/OOS",
    ):
        assert phrase in report
    assert calculate_performance(r.run(), r.tape, policy) == first


def test_cash_only_no_completed_trades() -> None:
    r = configure(runner(), decision_steps=[])
    p = calculate_performance(r.run(), r.tape, valuation_policy()).to_dict()
    assert p["initial_equity"] == p["final_equity"] == "10000"
    assert p["total_return"] == p["mdd"]["ratio"] == "0"
    assert p["win_rate"] is None
    assert p["fill_count"] == p["completed_trades"] == 0
    assert p["valuation_coverage"]["unvalued_positions"] == 0


def test_buy_open_only() -> None:
    r = round_trip()
    r = configure(r, end_at="2026-01-06T01:00:00Z")
    p = calculate_performance(r.run(), r.tape, valuation_policy()).to_dict()
    assert p["completed_trades"] == 0 and p["win_rate"] is None
    assert p["realized_pnl"] == "0" and p["unrealized_pnl"] == "-10.82"
    assert p["final_positions"][0]["quantity"] == 10


def test_two_round_trips() -> None:
    r = round_trip(twice=True)
    p = calculate_performance(r.run(), r.tape, valuation_policy()).to_dict()
    assert (p["completed_trades"], p["wins"], p["win_rate"]) == (2, 2, "1")
    assert Decimal(p["final_equity"]) == Decimal("10341.1")
    assert p["realized_pnl"] == "341.1" and p["unrealized_pnl"] == "0"


def change_source_price(r: BacktestRunner, seq: int, field: str, price: str) -> BacktestRunner:
    wire = json.loads(r.tape.to_json())
    event = next(e for e in wire["events"] if e["sequence"] == seq)
    source = next(s for s in wire["sources"] if digest(s) == event["source"]["content_hash"])
    source["fields"][field] = price
    event["public_fields"][field] = price
    event["source"].update(content_hash=digest(source), artifact_id=digest(source))
    return rebuild(r, wire, [a.bundle.to_dict() for a in r.analyses])


@pytest.mark.parametrize("breakeven,expected", [(False, "loss"), (True, "breakeven")])
def test_losing_and_breakeven_completed_trade(breakeven: bool, expected: str) -> None:
    r = change_source_price(round_trip(), 4, "open", "90" if breakeven else "80")
    r = configure(r, end_at="2026-01-08T01:00:00Z")
    if breakeven:
        ep = r.execution_policy.to_dict()
        ep.update(slippage_rate="0", buy_fee_rate="0", sell_fee_rate="0", sell_tax_rate="0")
        r = replace(r, execution_policy=FrozenJSON.freeze(ep))
    p = calculate_performance(r.run(), r.tape, valuation_policy()).to_dict()
    assert p["completed_trades"] == 1 and p["win_rate"] == "0"
    assert p["trade_episodes"][0]["outcome"] == expected
    assert p["breakeven"] == int(breakeven)
    assert p["losses"] == int(not breakeven)
    assert p["wins"] == 0


def test_initial_position_is_marked_not_cost_basis() -> None:
    r = configure(runner("SELL"), decision_steps=[])
    wire = json.loads(r.tape.to_json())
    wire["manifest"]["initial_account"]["positions"][0]["cost_basis_krw"] = "350"
    r = rebuild(r, wire, [a.bundle.to_dict() for a in r.analyses])
    p = calculate_performance(r.run(), r.tape, valuation_policy()).to_dict()
    assert p["initial_equity"] == "10000"  # 9500 cash + 5 * 100, not +350 cost.
    assert p["final_equity"] == "10050"
    assert p["unrealized_pnl"] == "200"  # 5*110 -350.
    assert p["completed_trades"] == 0


def test_missing_initial_mark() -> None:
    r = configure(runner("SELL"), decision_steps=[])
    p = calculate_performance(r.run(), r.tape, valuation_policy(field="close")).to_dict()
    assert p["initial_equity"] is None
    assert p["final_equity"] is not None
    assert p["total_return"] is None
    assert p["mdd"]["ratio"] is None and not p["mdd"]["complete"]
    assert "initial_valuation_incomplete" in p["limitations"]


def test_stale_final_mark_does_not_fabricate_return(
    sample: tuple[BacktestRunner, FrozenJSON],
) -> None:
    r, result = sample
    p = calculate_performance(result, r.tape, valuation_policy(max_age_seconds=0)).to_dict()
    assert p["final_equity"] is None and p["total_return"] is None
    assert p["unrealized_pnl"] is None
    assert p["final_cash"] == "9259.73"
    assert p["final_valuation"]["known_position_value"] == "0"
    assert p["valuation_coverage"]["unvalued_positions"] == 1
    assert p["mdd"]["ratio"] is None
    assert p["mdd"]["observed_lower_bound"] is not None


def test_future_field_and_after_end_prices_are_not_read() -> None:
    r = round_trip()
    # At the BUY fill point its close remains undisclosed for another hour.
    r = configure(r, end_at="2026-01-06T00:00:00Z")
    a = calculate_performance(r.run(), r.tape, valuation_policy(field="close", max_age_seconds=0))
    r2 = change_source_price(r, 2, "close", "999")
    r2 = change_source_price(r2, 6, "open", "500")
    b = calculate_performance(r2.run(), r2.tape, valuation_policy(field="close", max_age_seconds=0))
    assert a.to_dict()["equity_curve"] == b.to_dict()["equity_curve"]
    assert a.to_dict()["final_equity"] is None


def test_partial_exit_not_completed_episode() -> None:
    r = round_trip()
    policy = r.decision_policy.to_dict()
    policy["sizing"].update(sell_mode="partial", partial_kind="quantity", partial_value=5)
    r = replace(r, decision_policy=DecisionPolicy.from_dict(policy))
    p = calculate_performance(r.run(), r.tape, valuation_policy()).to_dict()
    assert p["completed_trades"] == 0 and p["win_rate"] is None
    assert p["realized_pnl"] == "0"
    assert p["ledger_realized_pnl"] == p["open_episode_realized_pnl"] == "85.28"
    assert p["unrealized_pnl"] == "-5.41"


def test_nonpositive_initial_equity_rejected() -> None:
    r = configure(runner(), decision_steps=[])
    wire = json.loads(r.tape.to_json())
    wire["manifest"]["initial_account"]["available_cash_krw"] = "0"
    r = rebuild(r, wire, [a.bundle.to_dict() for a in r.analyses])
    with pytest.raises(ValueError, match="nonpositive_initial_equity"):
        calculate_performance(r.run(), r.tape, valuation_policy())


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"field": "latest"}, "unsupported_valuation_field"),
        ({"external_cash_flow_krw": "10"}, "external_cash_flows_unsupported"),
        ({"money_quantum": "NaN"}, "nonfinite_decimal"),
        ({"selection": "future_close"}, "unsupported_mark_selection"),
    ],
)
def test_explicit_valuation_policy_required(
    sample: tuple[BacktestRunner, FrozenJSON],
    change: dict[str, Any],
    reason: str,
) -> None:
    r, result = sample
    with pytest.raises(ValueError, match=reason):
        calculate_performance(result, r.tape, valuation_policy(**change))


def test_rounding_and_mdd_incomplete_contract() -> None:
    from donghak_stock_vision.backtest.performance import policy_values, rounded

    p = policy_values(valuation_policy(money_quantum="1", money_rounding="half_up"))
    assert rounded(Decimal("10.5"), p, "money") == "11"
    p = policy_values(valuation_policy(money_quantum="1", money_rounding="down"))
    assert rounded(Decimal("10.5"), p, "money") == "10"
    curve = [
        {
            "timestamp": str(i),
            "sequence": i,
            "stage": "event",
            "complete": v is not None,
            "total_equity": v,
        }
        for i, v in enumerate(["100", "120", "90", None, "110"])
    ]
    m = drawdown(curve, p)
    assert m["ratio"] is None and m["observed_lower_bound"] == "0.25"
    assert m["peak"]["timestamp"] == "1" and m["trough"]["timestamp"] == "2"


def test_unverified_history_and_tampered_final_account_rejected(
    sample: tuple[BacktestRunner, FrozenJSON],
) -> None:
    r, result = sample
    v = deepcopy(result.to_dict())
    del v["position_history"]
    with pytest.raises(ValueError, match="verified_position_history_required"):
        calculate_performance(FrozenJSON.freeze(v), r.tape, valuation_policy())
    v = result.to_dict()
    v["final_account"]["total_cash_krw"] = "999999"
    with pytest.raises(ValueError, match="final_account_mismatch"):
        calculate_performance(FrozenJSON.freeze(v), r.tape, valuation_policy())
