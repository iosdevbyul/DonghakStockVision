"""Explicit offline post-collection research; no credentials or collection controls."""

import argparse
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.storage.daily_research import daily_status, run_daily
from donghak_stock_vision.storage.krx_scheduler import load_config


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("daily-research", help="offline verified KRX research and resume")
    cmd.add_argument("action", choices=("run", "status"))
    cmd.add_argument("--output-db", type=Path, required=True)
    cmd.add_argument("--day", type=date.fromisoformat, required=True)
    cmd.add_argument("--config", type=Path)
    cmd.add_argument("--config-pin")
    cmd.add_argument("--model-db", type=Path)
    cmd.add_argument("--model-version")
    cmd.add_argument("--tickers", nargs="+")
    cmd.add_argument("--lookback-days", type=int, default=60)
    cmd.add_argument("--allow-synthetic-fixture", action="store_true")


def run(args: argparse.Namespace) -> int:
    at = datetime.now(UTC)
    if args.action == "status":
        result = daily_status(args.output_db, args.day, at)
    else:
        if not all((args.config, args.config_pin, args.model_db, args.model_version, args.tickers)):
            raise ValueError("daily_config_model_and_universe_required")
        result = run_daily(
            load_config(args.config, args.config_pin),
            args.output_db,
            args.model_db,
            args.model_version,
            args.day,
            tuple(sorted(set(args.tickers))),
            at,
            lookback_days=args.lookback_days,
            allow_synthetic_fixture=args.allow_synthetic_fixture,
        )
    print(result.payload_json)
    return int(result.to_dict()["analysis_status"] != "completed")
