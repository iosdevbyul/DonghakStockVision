"""Explicit offline forward-signal PAPER composition and replay commands."""

import argparse
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("paper-trade", help="verified synthetic forward signals to PAPER only")
    cmd.add_argument(
        "action",
        choices=(
            "dry-run",
            "execute",
            "history",
            "portfolio",
            "verify",
            "reconcile",
            "offline-demo",
        ),
    )
    cmd.add_argument("--account", type=Path)
    cmd.add_argument("--account-pin", required=False)
    cmd.add_argument("--journal", type=Path)
    cmd.add_argument("--journal-pin", required=False)
    cmd.add_argument("--action-file", type=Path)
    cmd.add_argument("--workspace", type=Path)


def load(path: Path) -> FrozenJSON:
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= 256 * 1024,
        "bounded_paper_input_file_required",
    )
    return FrozenJSON(path.read_text())


def run(args: argparse.Namespace) -> int:
    from donghak_stock_vision.storage.forward_paper_journal import execute, inspect, reconcile

    if args.action == "offline-demo":
        from donghak_stock_vision.paper_trade_demo_cli import demonstrate

        require(args.workspace is not None, "explicit_new_demo_workspace_required")
        print(demonstrate(args.workspace).payload_json)
        return 0
    require(
        all(
            v is not None for v in (args.account, args.account_pin, args.journal, args.journal_pin)
        ),
        "explicit_account_pin_and_journal_pin_required",
    )
    config = load(args.account)
    if args.action == "reconcile":
        require(args.action_file is not None, "explicit_intended_action_required")
        result = reconcile(
            args.journal, config, args.account_pin, args.journal_pin, load(args.action_file)
        )
    elif args.action in ("dry-run", "execute"):
        require(args.action_file is not None, "explicit_action_file_required")
        result = execute(
            args.journal,
            config,
            args.account_pin,
            args.journal_pin,
            load(args.action_file),
            dry_run=args.action == "dry-run",
        )
    else:
        result = inspect(args.journal, config, args.account_pin, args.journal_pin)
        if args.action == "portfolio":
            result = FrozenJSON.freeze(
                {
                    "version": "forward_paper_portfolio_v1",
                    "mode": "PAPER",
                    "head_pin": args.journal_pin,
                    "portfolio": result.to_dict()["portfolio"],
                }
            )
    print(result.payload_json)
    return 0
