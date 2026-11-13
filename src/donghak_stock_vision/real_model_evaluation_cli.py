"""Evaluate a pinned frozen research model without credentials or fitting."""

from __future__ import annotations

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.real_model_evaluation import export


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = sub.add_parser("real-model-evaluation")
    command.add_argument("action", choices=("run", "verify"))
    command.add_argument("--request", type=Path, required=True)
    command.add_argument("--request-pin-file", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pins-root", type=Path, required=True)


def run(args: argparse.Namespace) -> int:
    d = document(str(args.request))
    require(digest(d) == read_pin(args.request_pin_file), "evaluation_request_pin_mismatch")
    export(d, args.output, args.pins_root, verify=args.action == "verify")
    print(
        '{"evaluation_verified":true,"parameters_unchanged":true,"performance_approved":false,"pit_qualified":false,"network_requests":0}'
    )
    return 0
