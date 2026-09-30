"""Prepare explicit historical inputs; does not claim real execution is supported."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from donghak_stock_vision.backtest.contracts import ExecutionPolicy, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical import build_historical_input
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.research_calendar import calendar_policy
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "backtest-input", help="freeze real historical input; execution remains blocked"
    )
    p.add_argument("--market-db", type=Path, required=True)
    p.add_argument("--analysis-db", type=Path)
    p.add_argument("--model-id")
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
    config = FrozenJSON(args.config.read_text(encoding="utf-8")).to_dict()
    market = SQLiteStore(args.market_db)
    analysis = AnalysisStore(args.analysis_db) if args.analysis_db is not None else None
    try:
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
        )
        with args.output.open("x", encoding="utf-8") as output:
            output.write(result.document.payload_json)
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
