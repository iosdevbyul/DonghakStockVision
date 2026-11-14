"""Manual research-shadow continuation after verified collection; never scheduling."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.shadow_prediction import (
    correct,
    record,
    recover,
    resolve,
    status,
    verify,
)


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("shadow-prediction")
    p.add_argument(
        "action",
        choices=("record", "resolve", "status", "correct", "recover", "verify", "alignment"),
    )
    p.add_argument("--request", type=Path)
    p.add_argument("--request-pin-file", type=Path)
    p.add_argument("--journal", type=Path)
    p.add_argument("--head-pin-file", type=Path)
    p.add_argument("--intents", type=Path)
    p.add_argument("--pins-root", type=Path)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--prediction-id")
    p.add_argument("--reason")
    p.add_argument("--intent", type=Path)
    p.add_argument("--intent-pin-file", type=Path)
    p.add_argument("--scheduler-config", type=Path)
    p.add_argument("--scheduler-config-pin-file", type=Path)


def run(args: argparse.Namespace) -> int:
    require(not args.report.exists(), "shadow_new_report_required")
    try:
        if args.action != "alignment":
            require(
                args.journal is not None and args.head_pin_file is not None,
                "shadow_journal_required",
            )
        head = read_pin(args.head_pin_file) if args.head_pin_file is not None else ""
        d = {}
        if args.action not in ("status", "correct"):
            require(
                args.request is not None and args.request_pin_file is not None,
                "shadow_request_required",
            )
            d = document(str(args.request))
            require(digest(d) == read_pin(args.request_pin_file), "shadow_request_pin_mismatch")
        if args.action == "alignment":
            from donghak_stock_vision.storage.krx_scheduler import load_config
            from donghak_stock_vision.storage.shadow_handoff import (
                alignment,
                schedule_alignment,
            )

            result = alignment(d)
            require(
                bool(args.scheduler_config) == bool(args.scheduler_config_pin_file),
                "shadow_complete_scheduler_reference_required",
            )
            if args.scheduler_config is not None:
                config = load_config(
                    args.scheduler_config, read_pin(args.scheduler_config_pin_file)
                )
                result = FrozenJSON.freeze(
                    result.to_dict()
                    | {
                        "collection_schedule": schedule_alignment(
                            config, d, datetime.now(UTC)
                        ).to_dict()
                    }
                )
        elif args.action == "status":
            result = status(args.journal, head)
        elif args.action == "verify":
            result = verify(d, args.journal, head)
        elif args.action == "recover":
            require(
                args.intent is not None and args.intent_pin_file is not None,
                "shadow_recovery_intent_required",
            )
            result = recover(d, args.journal, head, args.intent, args.intent_pin_file)
        else:
            require(
                args.intents is not None and args.pins_root is not None,
                "shadow_private_intents_required",
            )
            if args.action == "record":
                result = record(d, args.journal, head, args.intents, args.pins_root)
            elif args.action == "resolve":
                result = resolve(d, args.journal, head, args.intents, args.pins_root)
            else:
                require(
                    args.prediction_id is not None and args.reason is not None,
                    "shadow_correction_required",
                )
                result = correct(
                    args.journal,
                    head,
                    args.prediction_id,
                    args.reason,
                    args.intents,
                    args.pins_root,
                )
        args.report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_new(args.report, result.payload_json)
        value = result.to_dict()
        print(
            json.dumps(
                dict(
                    action=args.action,
                    recorded=value.get("recorded"),
                    blocked=value.get("blocked"),
                    prediction_count=value.get("prediction_count"),
                    performance_approved=False,
                    pit_qualified=False,
                    network_requests=0,
                    **(
                        {"input_timing_ready": value.get("input_timing_ready")}
                        if args.action == "alignment"
                        else {}
                    ),
                )
            )
        )
        return int(bool(value.get("blocked")) or value.get("input_timing_ready") is False)
    except (ValueError, OSError, sqlite3.Error) as error:
        args.report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = dict(
            status="blocked",
            reason=str(error)
            if re.fullmatch(r"[a-z][a-z0-9_]{0,159}", str(error))
            else "shadow_validation_failed",
            prediction_created=None,
            journal_write_outcome="inspect_retained_checkpoints_and_intents",
            network_requests=0,
        )
        write_new(args.report, json.dumps(value))
        print(json.dumps(value))
        return 2
