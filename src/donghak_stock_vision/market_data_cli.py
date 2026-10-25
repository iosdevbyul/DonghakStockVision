"""Read-only descriptive market analytics, isolated from collection and strategy execution."""

import argparse
import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import CalendarMonthSource
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.storage.market_analytics import analyze_market_data, validate_access


def add_commands(sub: Any) -> None:
    group = sub.add_parser("market-data", help="bounded, offline, read-only descriptive analytics")
    views = group.add_subparsers(dest="market_view", required=True)
    for name in ("summary", "history", "quality"):
        cmd = views.add_parser(name)
        cmd.add_argument("--db", dest="market_db", type=Path, required=True)
        cmd.add_argument("--db-sha256", required=True)
        cmd.add_argument("--symbol", required=True)
        cmd.add_argument("--start", type=date.fromisoformat, required=True)
        cmd.add_argument("--end", type=date.fromisoformat, required=True)
        cmd.add_argument("--market", choices=("KOSPI", "KOSDAQ", "KONEX"), default="KOSPI")
        cmd.add_argument("--data-origin", choices=("local_krx", "synthetic"), required=True)
        cmd.add_argument("--split", type=Path, required=True)
        cmd.add_argument("--split-pin", required=True)
        cmd.add_argument("--max-rows", type=int, default=1250)
        cmd.add_argument("--calendar-source", type=Path)
        cmd.add_argument("--calendar-raw", type=Path)
        cmd.add_argument("--calendar-pin")


def run(args: argparse.Namespace) -> int:
    require(
        args.split.is_file() and args.split.stat().st_size <= 256 * 1024,
        "bounded_split_file_required",
    )
    metadata = json.loads(args.split.read_text())
    split = ExplicitResearchSplit.from_dict(metadata.get("split", metadata))
    validate_access(args.symbol, args.start, args.end, split, args.split_pin, args.max_rows)
    calendars: tuple[CalendarMonthSource, ...] = ()
    configured = (
        args.calendar_source is not None,
        args.calendar_raw is not None,
        args.calendar_pin is not None,
    )
    require(all(configured) or not any(configured), "complete_calendar_source_raw_pin_required")
    if all(configured):
        require(args.calendar_source.stat().st_size <= 256 * 1024, "calendar_metadata_limit")
        source = json.loads(args.calendar_source.read_text())
        source = source.get("calendar_source", source)
        opener = gzip.open if args.calendar_raw.suffix == ".gz" else open
        with opener(args.calendar_raw, "rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, "calendar_raw_limit")
        calendars = (CalendarMonthSource(raw, FrozenJSON.freeze(source), args.calendar_pin),)
    report = analyze_market_data(
        args.market_db,
        args.symbol,
        args.start,
        args.end,
        expected_sha256=args.db_sha256,
        split=split,
        split_pin=args.split_pin,
        data_origin=args.data_origin,
        market=args.market,
        view=args.market_view,
        max_rows=args.max_rows,
        calendars=calendars,
    )
    print(report.payload_json)
    return 0
