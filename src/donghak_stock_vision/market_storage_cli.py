"""Offline operational storage administration; no collection or recovery imports."""

import argparse
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.operational_market import OperationalMarketStore, StorageLimits


def add_commands(sub: Any) -> None:
    command = sub.add_parser(
        "market-storage", help="private operational SQLite; no provider requests"
    )
    command.add_argument(
        "action", choices=("init", "status", "validate", "duplicates", "usage", "latest")
    )
    command.add_argument("--root", type=Path, default=Path.home() / "DonghakStockVision-Data")
    command.add_argument("--max-database-bytes", type=int)
    command.add_argument("--min-free-bytes", type=int)
    command.add_argument("--max-rows-per-batch", type=int)


def run(args: argparse.Namespace) -> int:
    if args.db is not None:
        raise ValueError("market_storage_uses_root_not_research_db")
    options = {
        key: getattr(args, key)
        for key in ("max_database_bytes", "min_free_bytes", "max_rows_per_batch")
        if getattr(args, key) is not None
    }
    if args.action == "init":
        result = OperationalMarketStore.initialize(
            args.root, StorageLimits(**options) if options else None
        )
    else:
        if options:
            raise ValueError("storage_limits_only_at_explicit_initialization")
        with closing(OperationalMarketStore(args.root)) as store:
            if args.action == "validate":
                result = store.validate()
            else:
                status = store.status()
                doc = status.to_dict()
                fields = {
                    "duplicates": ("duplicate_records", "additional_source_links"),
                    "usage": ("disk_usage",),
                    "latest": ("latest_trading_date", "latest_response_date", "markets"),
                }
                result = (
                    status
                    if args.action == "status"
                    else FrozenJSON.freeze(
                        {
                            "version": f"operational_market_storage_{args.action}_v1",
                            **{key: doc[key] for key in fields[args.action]},
                            "eligibility": doc["eligibility"],
                        }
                    )
                )
    print(result.payload_json)
    return int(args.action == "validate" and not result.to_dict()["valid"])
