"""Manual multi-horizon research actions, never provider or scheduler activation."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.direction_ensemble import source_paths
from donghak_stock_vision.storage.evaluation_evidence import output_paths
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.research_resources import lock


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("direction-shadow")
    p.add_argument(
        "action",
        choices=(
            "register",
            "preflight",
            "record",
            "resolve",
            "report",
            "audit",
            "recover",
            "publish",
        ),
    )
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--request-pin-file", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--report-pin-file", type=Path, required=True)
    p.add_argument("--journal", type=Path)
    p.add_argument("--head-pin-file", type=Path)
    p.add_argument("--intents", type=Path)
    p.add_argument("--pins-root", type=Path)
    p.add_argument("--intent", type=Path)
    p.add_argument("--intent-pin-file", type=Path)


def run(args: argparse.Namespace) -> int:
    from donghak_stock_vision.storage import direction_shadow as owner

    try:
        d = document(str(args.request))
        require(digest(d) == read_pin(args.request_pin_file), "direction_request_pin")
        ensemble = (
            args.action == "register" and d["version"] == "direction_shadow_registration_request_v4"
        )
        if ensemble:
            require(not args.report_pin_file.exists(), "direction_ensemble_already_registered")
        require(
            not args.report.exists() and not args.report_pin_file.exists(),
            "direction_new_reports_required",
        )
        protected = [path for m in d.get("models", []) for path in source_paths(m)]
        if args.journal is not None:
            protected.append(args.journal.parent)
        if "research" in d:
            protected.extend(
                [Path(d["research"]["database"]).parent, Path(d["operational"]["root"])]
            )
        output_paths(args.report, args.report_pin_file, protected)
        if args.action == "publish":
            result = owner.publish_streams(d)
        elif args.action == "register":
            result = owner.register(d).to_dict()
        elif args.action == "preflight":
            result = owner.preflight(d)
        else:
            require(
                args.journal is not None and args.head_pin_file is not None,
                "direction_journal_reference",
            )
            head = read_pin(args.head_pin_file)
            if args.action in ("report", "audit"):
                reader = owner.report if args.action == "report" else owner.audit
                result = reader(d, args.journal, head)
            elif args.action == "recover":
                require(
                    args.intent is not None and args.intent_pin_file is not None,
                    "direction_intent_reference",
                )
                result = owner.recover(d, args.journal, head, args.intent, args.intent_pin_file)
            else:
                require(
                    args.intents is not None and args.pins_root is not None,
                    "direction_separate_intents",
                )
                require(
                    all(
                        not p.is_relative_to(Path(d["operational"]["root"]))
                        and not p.is_relative_to(Path(d["research"]["database"]).parent)
                        for p in (args.journal, args.intents, args.pins_root)
                    ),
                    "direction_protected_output",
                )
                result = getattr(owner, args.action)(
                    d, args.journal, head, args.intents, args.pins_root
                )
        value = FrozenJSON.freeze(result)
        args.report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.report_pin_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if ensemble:
            name = owner.ensemble_pin_name(result["models"])
            require(args.report_pin_file.name == name, "direction_ensemble_pin_name")
            with lock(args.report_pin_file.parent, "." + name + ".register.lock"):
                require(not args.report_pin_file.exists(), "direction_ensemble_already_registered")
                write_new(args.report, value.payload_json)
                write_new(args.report_pin_file, value.identifier)
        else:
            write_new(args.report, value.payload_json)
            write_new(args.report_pin_file, value.identifier)
        print(
            json.dumps(
                dict(
                    action=args.action,
                    status=result.get(
                        "status", "REGISTERED" if args.action == "register" else "COMPLETE"
                    ),
                    new_predictions=result.get("new_predictions", 0),
                    new_evaluations=result.get("evaluated_this_run", 0),
                    network_requests=0,
                    performance_approved=False,
                    pit_qualified=False,
                )
            )
        )
        return 1 if result.get("status") == "BLOCKED" else 0
    except (ValueError, OSError, KeyError) as error:
        reason = (
            str(error)
            if re.fullmatch("[a-z][a-z0-9_]{0,159}", str(error))
            else "direction_evidence_invalid"
        )
        print(
            json.dumps(
                dict(
                    status="INVALID_EVIDENCE",
                    reason=reason,
                    prediction_created=None
                    if args.action in ("record", "resolve", "recover")
                    else False,
                    journal_write_outcome="inspect_retained_intents_and_heads"
                    if args.action in ("record", "resolve", "recover")
                    else "not_attempted",
                    checked_at=datetime.now(UTC).isoformat(),
                )
            )
        )
        return 2
