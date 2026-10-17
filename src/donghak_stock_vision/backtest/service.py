"""Reusable frozen-input service. No market/model loader, policy defaults, or network."""

from typing import Any

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.ledger import checkpoint, restore
from donghak_stock_vision.backtest.performance import policy_values
from donghak_stock_vision.backtest.report import format_report
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.shared_evidence import EvidenceBuilder, is_shared
from donghak_stock_vision.backtest.trading_report import ReportedBacktest, report_backtest
from donghak_stock_vision.backtest.validation import fields, money, require
from donghak_stock_vision.strategy.contracts import DecisionPolicy


def freeze_request(
    runner: BacktestRunner, valuation_policy: FrozenJSON, *, shared: bool = True
) -> FrozenJSON:
    """Serialize an already assembled, explicit research input. Does not invent data."""
    import json

    builder = EvidenceBuilder()
    if shared:
        for analysis in runner.analyses:
            builder.add(analysis.bundle.to_dict())
    return FrozenJSON.freeze(
        {
            "version": 2 if shared else 1,
            **(
                {"evidence_table": builder.table(runner.tape.manifest.identifier)} if shared else {}
            ),
            "tape": json.loads(runner.tape.to_json()),
            "analyses": builder.records
            if shared
            else [a.bundle.to_dict() for a in runner.analyses],
            "initial_checkpoint": checkpoint(runner.initial).to_dict(),
            "decision_policy": runner.decision_policy.to_dict(),
            "execution_policy": runner.execution_policy.to_dict(),
            "config": runner.config.to_dict(),
            "valuation_policy": valuation_policy.to_dict(),
        }
    )


def run_backtest_with_report(
    document: FrozenJSON, *, start: str, end: str, initial_cash: str, tickers: list[str]
) -> ReportedBacktest:
    require(
        document.to_dict().get("kind") not in {"historical_input_v1", "historical_input_v2"},
        "real_runner_unsupported",
    )
    d = fields(
        document.to_dict(),
        "version tape analyses initial_checkpoint decision_policy "
        "execution_policy config valuation_policy"
        + (" evidence_table" if is_shared(document.to_dict()) else ""),
    )
    require(type(d["version"]) is int and d["version"] in {1, 2}, "unsupported_request_version")
    require(d["version"] == 1 or is_shared(d), "shared_evidence_required")
    policy_values(FrozenJSON.freeze(d["valuation_policy"]))
    tape = FrozenTape.from_json(FrozenJSON.freeze(d["tape"]).payload_json)
    initial = restore(FrozenJSON.freeze(d["initial_checkpoint"]))
    require(money(initial_cash) == initial.to_dict()["total_cash_krw"], "initial_cash_mismatch")
    require(tickers == d["config"]["tickers"], "frozen_universe_mismatch")
    config: dict[str, Any] = {**d["config"], "start_at": start, "end_at": end}
    runner = BacktestRunner(
        tape,
        document.shared_evidence.analyses(tape.manifest, tape.release_plan)
        if is_shared(d)
        else tuple(
            FrozenAnalysis(tape.manifest, tape.release_plan, FrozenJSON.freeze(b))
            for b in d["analyses"]
        ),
        initial,
        DecisionPolicy.from_dict(d["decision_policy"]),
        FrozenJSON.freeze(d["execution_policy"]),
        FrozenJSON.freeze(config),
    )
    return report_backtest(runner, FrozenJSON.freeze(d["valuation_policy"]))


def run_backtest(
    document: FrozenJSON, *, start: str, end: str, initial_cash: str, tickers: list[str]
) -> tuple[FrozenJSON, FrozenJSON, str]:
    result = run_backtest_with_report(
        document, start=start, end=end, initial_cash=initial_cash, tickers=tickers
    )
    return result.run, result.performance, format_report(result.performance, result.details)
