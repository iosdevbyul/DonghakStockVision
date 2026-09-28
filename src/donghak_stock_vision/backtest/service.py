"""Reusable frozen-input service. No market/model loader, policy defaults, or network."""

from typing import Any

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.ledger import checkpoint, restore
from donghak_stock_vision.backtest.performance import calculate_performance, policy_values
from donghak_stock_vision.backtest.report import format_report
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.validation import fields, money, require
from donghak_stock_vision.strategy.contracts import DecisionPolicy


def freeze_request(runner: BacktestRunner, valuation_policy: FrozenJSON) -> FrozenJSON:
    """Serialize an already assembled, explicit research input. Does not invent data."""
    import json

    return FrozenJSON.freeze(
        {
            "version": 1,
            "tape": json.loads(runner.tape.to_json()),
            "analyses": [a.bundle.to_dict() for a in runner.analyses],
            "initial_checkpoint": checkpoint(runner.initial).to_dict(),
            "decision_policy": runner.decision_policy.to_dict(),
            "execution_policy": runner.execution_policy.to_dict(),
            "config": runner.config.to_dict(),
            "valuation_policy": valuation_policy.to_dict(),
        }
    )


def run_backtest(
    document: FrozenJSON, *, start: str, end: str, initial_cash: str, tickers: list[str]
) -> tuple[FrozenJSON, FrozenJSON, str]:
    d = fields(
        document.to_dict(),
        "version tape analyses initial_checkpoint decision_policy "
        "execution_policy config valuation_policy",
    )
    require(type(d["version"]) is int and d["version"] == 1, "unsupported_request_version")
    policy_values(FrozenJSON.freeze(d["valuation_policy"]))
    tape = FrozenTape.from_json(FrozenJSON.freeze(d["tape"]).payload_json)
    initial = restore(FrozenJSON.freeze(d["initial_checkpoint"]))
    require(money(initial_cash) == initial.to_dict()["total_cash_krw"], "initial_cash_mismatch")
    require(tickers == d["config"]["tickers"], "frozen_universe_mismatch")
    config: dict[str, Any] = {**d["config"], "start_at": start, "end_at": end}
    runner = BacktestRunner(
        tape,
        tuple(
            FrozenAnalysis(tape.manifest, tape.release_plan, FrozenJSON.freeze(b))
            for b in d["analyses"]
        ),
        initial,
        DecisionPolicy.from_dict(d["decision_policy"]),
        FrozenJSON.freeze(d["execution_policy"]),
        FrozenJSON.freeze(config),
    )
    result = runner.run()
    performance = calculate_performance(result, tape, FrozenJSON.freeze(d["valuation_policy"]))
    return result, performance, format_report(performance)
