"""Explicit frozen synthetic research CLI; never assembles production market inputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.service import run_backtest


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = sub.add_parser(
        "backtest", help="run a frozen synthetic historical research simulation"
    )
    command.add_argument("--input", type=Path, required=True, help="explicit frozen research JSON")
    command.add_argument("--start", required=True, help="timezone-aware ISO timestamp")
    command.add_argument("--end", required=True, help="timezone-aware ISO timestamp")
    command.add_argument("--initial-cash", required=True, help="must match frozen initial cash")
    command.add_argument("--tickers", nargs="+", required=True, help="ordered frozen universe")
    command.add_argument("--format", choices=("text", "json"), default="text")


def run(args: argparse.Namespace) -> int:
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
