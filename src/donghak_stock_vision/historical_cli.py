"""Prepare explicit historical inputs; does not claim real execution is supported."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from donghak_stock_vision.backtest.contracts import ExecutionPolicy, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical import build_historical_input
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from donghak_stock_vision.data.research_calendar import calendar_policy
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "backtest-input", help="freeze real historical input; execution remains blocked"
    )
    p.add_argument("--market-db", type=Path, required=True)
    p.add_argument("--analysis-db", type=Path)
    p.add_argument("--model-id")
    p.add_argument("--run-output-db", type=Path, help="new separate signal/audit SQLite")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--tickers", nargs="+", required=True)
    p.add_argument(
        "--config",
        type=Path,
        required=True,
        help="manifest, policy, quality, capture and availability",
    )
    p.add_argument("--output", type=Path, required=True, help="new bundle file; never overwritten")


def run(args: argparse.Namespace) -> int:
    require(args.market_db.is_file(), "market_db_missing")
    require(not args.output.exists(), "output_already_exists")
    require(
        (args.analysis_db is None) == (args.model_id is None), "analysis_store_and_model_required"
    )
    if args.analysis_db is not None:
        require(args.analysis_db.is_file(), "analysis_db_missing")
        require(args.run_output_db is not None, "run_output_required")
        require(not args.run_output_db.exists(), "run_output_already_exists")
    else:
        require(args.run_output_db is None, "run_output_without_source")
    config = FrozenJSON(args.config.read_text(encoding="utf-8")).to_dict()
    market = ReadOnlyMarketStore(args.market_db)
    analysis = ReadOnlyAnalysisStore(args.analysis_db) if args.analysis_db is not None else None
    output = None
    try:
        if args.run_output_db is not None:
            with args.run_output_db.open("xb"):
                pass
            output = AnalysisStore(args.run_output_db)
        result = build_historical_input(
            market,
            start=args.start,
            end=args.end,
            tickers=args.tickers,
            captured_at=config["captured_at"],
            quality=FrozenJSON.freeze(config["quality"]),
            manifest_template=RunManifest.from_dict(config["manifest"]),
            policy=ExecutionPolicy.from_dict(config["policy"]),
            availability_assumption=config["availability_assumption"],
            analysis_store=analysis,
            model_id=args.model_id,
            research_calendar=calendar_policy(config),
            decision_dates=config.get("decision_dates", "all_available"),
            run_output=output,
        )
        with args.output.open("x", encoding="utf-8") as bundle_file:
            bundle_file.write(result.document.payload_json)
        d = result.document.to_dict()
        print(
            FrozenJSON.freeze(
                {
                    "mode": d["mode"],
                    "input_hash": result.identifier,
                    "limitations": d["limitations"],
                    "execution_status": "blocked",
                    "execution_reason": d["execution_reason"],
                }
            ).payload_json
        )
        return 0
    except (KeyError, TypeError) as error:
        raise ValueError("invalid_historical_input_config") from error
    finally:
        market.close()
        if analysis is not None:
            analysis.close()
        if output is not None:
            output.close()
