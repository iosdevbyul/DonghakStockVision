"""Explicit offline signal analysis and local journaling; never submits orders."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("forward-signal", help="captured-observation SMA signals; no trading")
    cmd.add_argument("action", choices=("dry-run", "evaluate", "history", "verify", "offline-demo"))
    cmd.add_argument("--capture-root", type=Path)
    cmd.add_argument("--request", type=Path)
    cmd.add_argument("--request-pin")
    cmd.add_argument("--journal", type=Path)
    cmd.add_argument("--journal-pin")
    cmd.add_argument("--signal-id")
    cmd.add_argument("--limit", type=int, default=50)
    from donghak_stock_vision.storage.krx_authorization import add_trust_arguments

    add_trust_arguments(cmd)
    cmd.add_argument("--outcome", choices=("BUY", "SELL", "HOLD"), default="BUY")


def _request(args: argparse.Namespace) -> FrozenJSON:
    require(
        args.capture_root is not None and args.request is not None and args.request_pin is not None,
        "explicit_capture_root_request_and_pin_required",
    )
    require(
        args.request.is_file()
        and not args.request.is_symlink()
        and args.request.stat().st_size <= 256 * 1024,
        "bounded_signal_request_required",
    )
    return FrozenJSON(args.request.read_text())


def run(args: argparse.Namespace) -> int:
    from donghak_stock_vision.storage.forward_signal import generate_signal, verify_signal
    from donghak_stock_vision.storage.forward_signal_journal import append_signal, history

    if args.action == "offline-demo":
        from donghak_stock_vision.forward_signal_demo_cli import demonstrate

        require(
            args.capture_root is not None and args.journal is not None,
            "explicit_demo_capture_root_and_journal_required",
        )
        print(demonstrate(args.capture_root, args.journal, args.outcome).payload_json)
        return 0
    from donghak_stock_vision.storage.krx_authorization import cli_trust

    trust = cli_trust(args)
    if args.action == "dry-run":
        decision = generate_signal(
            args.capture_root, _request(args), args.request_pin, authorization_trust=trust
        )
        print(
            FrozenJSON.freeze(
                {
                    "version": "forward_signal_dry_run_v1",
                    "decision": decision.to_dict(),
                    "decision_pin": decision.identifier,
                    "journal_written": False,
                }
            ).payload_json
        )
        return 0
    require(
        args.journal is not None and args.journal_pin is not None,
        "explicit_journal_and_trusted_head_pin_required",
    )
    if args.action == "history":
        print(history(args.journal, args.journal_pin, limit=args.limit).payload_json)
    elif args.action == "evaluate":
        print(
            append_signal(
                args.journal,
                args.capture_root,
                _request(args),
                args.request_pin,
                args.journal_pin,
                datetime.now(UTC),
                authorization_trust=trust,
            ).payload_json
        )
    else:
        require(args.signal_id is not None, "explicit_signal_id_required")
        request = _request(args)
        records = history(args.journal, args.journal_pin, limit=256).to_dict()
        selected = [
            e for e in records["entries"] if e["entry"]["decision"]["signal_id"] == args.signal_id
        ]
        require(len(selected) == 1, "signal_not_found")
        entry = selected[0]["entry"]
        verify_signal(
            args.capture_root,
            request,
            args.request_pin,
            FrozenJSON.freeze(entry["decision"]),
            entry["decision_pin"],
            authorization_trust=trust,
        )
        print(
            FrozenJSON.freeze(
                {
                    "version": "forward_signal_verification_v1",
                    "signal_id": args.signal_id,
                    "semantic_replay_verified": True,
                    "independent_custody_verified": False,
                }
            ).payload_json
        )
    return 0
