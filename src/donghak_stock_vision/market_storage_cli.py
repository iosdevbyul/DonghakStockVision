"""Offline operational storage administration and verified capture imports."""

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
        "action",
        choices=("init", "status", "validate", "duplicates", "usage", "latest", "ingest-capture"),
    )
    command.add_argument("--root", type=Path)
    command.add_argument("--capture-root", type=Path)
    command.add_argument("--plan", type=Path)
    command.add_argument("--plan-pin")
    command.add_argument("--capture-pin")
    command.add_argument("--attempt")
    command.add_argument("--sequence", type=int, default=0)
    command.add_argument("--authorization-root", type=Path)
    command.add_argument("--authorization-pin")
    command.add_argument(
        "--authorization-policy",
        choices=(
            "independent_review_v1",
            "operator_read_only_pilot_v1",
            "operator_full_market_recurring_v1",
        ),
        default="independent_review_v1",
    )
    command.add_argument("--allow-synthetic-fixture", action="store_true")
    from donghak_stock_vision.storage.krx_authorization import add_trust_arguments

    add_trust_arguments(command)
    command.add_argument("--max-database-bytes", type=int)
    command.add_argument("--min-free-bytes", type=int)
    command.add_argument("--max-rows-per-batch", type=int)


def run(args: argparse.Namespace) -> int:
    if args.db is not None:
        raise ValueError("market_storage_uses_root_not_research_db")
    if args.root is None:
        if args.action == "ingest-capture":
            raise ValueError("explicit_operational_storage_root_required")
        args.root = Path.home() / "DonghakStockVision-Data"
    options = {
        key: getattr(args, key)
        for key in ("max_database_bytes", "min_free_bytes", "max_rows_per_batch")
        if getattr(args, key) is not None
    }
    if args.action == "ingest-capture":
        if options:
            raise ValueError("storage_limits_only_at_explicit_initialization")
        result = _ingest_capture(args)
    elif args.action == "init":
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


def _ingest_capture(args: argparse.Namespace) -> FrozenJSON:
    from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
    from donghak_stock_vision.storage.krx_authorization import cli_trust
    from donghak_stock_vision.storage.operational_capture import ingest_capture

    if any(
        value is None
        for value in (args.capture_root, args.plan, args.plan_pin, args.capture_pin, args.attempt)
    ):
        raise ValueError("original_capture_plan_and_independent_pins_required")
    with args.plan.open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("plan_byte_limit")
    owner = FrozenJSON(raw.decode("utf-8"))
    if owner.identifier != args.plan_pin:
        raise ValueError("plan_pin_mismatch")
    if (args.authorization_root is None) != (args.authorization_pin is None):
        raise ValueError("original_authorization_root_and_pin_required")
    authorization = (
        FrozenJSON.freeze(
            {"root": str(args.authorization_root), "grant_pin": args.authorization_pin}
        )
        if args.authorization_root is not None
        else None
    )
    return ingest_capture(
        args.root,
        args.capture_root,
        ForwardCapturePlan.from_dict(owner.to_dict()),
        args.plan_pin,
        args.attempt,
        args.capture_pin,
        sequence=args.sequence,
        authorization=authorization,
        trust=cli_trust(args),
        authorization_policy=args.authorization_policy,
        allow_synthetic_fixture=args.allow_synthetic_fixture,
    )
