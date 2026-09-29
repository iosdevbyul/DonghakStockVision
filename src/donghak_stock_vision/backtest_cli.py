"""Explicit frozen synthetic or stored historical research CLI; never live trading."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_service import (
    HistoricalBacktestRequest,
    run_historical_backtest,
)
from donghak_stock_vision.backtest.service import run_backtest
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore


class ReadOnlyMarketStore(SQLiteStore):
    """Reuse Phase 1 read API without schema writes or creating a missing DB."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self.connection.execute("PRAGMA query_only=ON")


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = sub.add_parser("backtest", help="run an explicit historical research simulation")
    source = command.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="frozen synthetic research JSON")
    source.add_argument("--market-db", type=Path, help="historical daily SQLite source (read only)")
    command.add_argument("--analysis-db", type=Path, help="Phase 2 artifact/audit SQLite")
    command.add_argument("--model-id", help="existing Phase 2 research model")
    command.add_argument("--config", type=Path, help="explicit historical policies/config JSON")
    command.add_argument(
        "--start",
        required=True,
        help="Seoul YYYY-MM-DD for DB; aware ISO timestamp for frozen input",
    )
    command.add_argument(
        "--end", required=True, help="Seoul YYYY-MM-DD for DB; aware ISO timestamp for frozen input"
    )
    command.add_argument(
        "--initial-cash", required=True, help="positive KRW Decimal; frozen input must match"
    )
    command.add_argument(
        "--tickers", "--ticker", nargs="+", required=True, help="ordered frozen universe"
    )
    command.add_argument("--format", choices=("text", "json"), default="text")


def run(args: argparse.Namespace) -> int:
    if args.market_db is not None:
        return run_historical(args)
    require(
        args.analysis_db is None and args.model_id is None and args.config is None,
        "historical_options_require_market_db",
    )
    document = FrozenJSON(args.input.read_text(encoding="utf-8"))
    try:
        result, performance, report = run_backtest(
            document,
            start=args.start,
            end=args.end,
            initial_cash=args.initial_cash,
            tickers=args.tickers,
        )
    except (KeyError, TypeError) as error:
        raise ValueError("invalid_frozen_backtest_input") from error
    if args.format == "json":
        print(
            FrozenJSON.freeze(
                {
                    "run": result.to_dict(),
                    "run_hash": result.identifier,
                    "performance": performance.to_dict(),
                    "performance_hash": performance.identifier,
                }
            ).payload_json
        )
    else:
        print(report, end="")
    return 0


def run_historical(args: argparse.Namespace) -> int:
    require(
        args.analysis_db is not None and args.model_id is not None and args.config is not None,
        "historical_db_model_config_required",
    )
    require(args.market_db.is_file(), "market_db_missing")
    require(args.analysis_db.is_file(), "analysis_db_missing")
    try:
        request = HistoricalBacktestRequest(
            date.fromisoformat(args.start),
            date.fromisoformat(args.end),
            args.initial_cash,
            tuple(args.tickers),
            args.model_id,
            FrozenJSON(args.config.read_text(encoding="utf-8")),
        )
        market = ReadOnlyMarketStore(args.market_db)
        try:
            analysis = AnalysisStore(args.analysis_db)
            try:
                result = run_historical_backtest(request, market, analysis)
            finally:
                analysis.close()
        finally:
            market.close()
    except (KeyError, TypeError) as error:
        raise ValueError("invalid_historical_backtest_config") from error
    if args.format == "json":
        print(
            FrozenJSON.freeze(
                {**result.document.to_dict(), "result_hash": result.identifier}
            ).payload_json
        )
    else:
        print(result.report + f"Result hash: {result.identifier}\n", end="")
    return 0
