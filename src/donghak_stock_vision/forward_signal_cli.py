"""Explicit offline signal analysis and local journaling; never submits orders."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("forward-signal", help="captured-observation SMA signals; no trading")
    cmd.add_argument(
        "action",
        choices=(
            "dry-run",
            "evaluate",
            "history",
            "verify",
            "offline-demo",
            "predict",
            "prediction-status",
            "offline-prediction-demo",
        ),
    )
    cmd.add_argument("--capture-root", type=Path)
    cmd.add_argument("--request", type=Path)
    cmd.add_argument("--request-pin")
    cmd.add_argument("--journal", type=Path)
    cmd.add_argument("--journal-pin")
    cmd.add_argument("--signal-id")
    cmd.add_argument("--limit", type=int, default=50)
    cmd.add_argument("--prediction-policy", type=Path)
    cmd.add_argument("--prediction-policy-pin")
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

    require(
        args.action == "predict"
        or (args.prediction_policy is None and args.prediction_policy_pin is None),
        "prediction_policy_only_for_predict",
    )

    if args.action in {"offline-demo", "offline-prediction-demo"}:
        from donghak_stock_vision.forward_signal_demo_cli import demonstrate, demonstrate_prediction

        require(
            args.capture_root is not None and args.journal is not None,
            "explicit_demo_capture_root_and_journal_required",
        )
        result = (
            demonstrate_prediction(args.capture_root, args.journal)
            if args.action == "offline-prediction-demo"
            else demonstrate(args.capture_root, args.journal, args.outcome)
        )
        print(result.payload_json)
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
    elif args.action in {"evaluate", "predict"}:
        policy = None
        if args.action == "predict":
            require(
                args.prediction_policy is not None and args.prediction_policy_pin is not None,
                "explicit_prediction_policy_and_pin_required",
            )
            require(
                args.prediction_policy.is_file()
                and not args.prediction_policy.is_symlink()
                and args.prediction_policy.stat().st_size <= 4096,
                "bounded_prediction_policy_required",
            )
            policy = FrozenJSON(args.prediction_policy.read_text())
        print(
            append_signal(
                args.journal,
                args.capture_root,
                _request(args),
                args.request_pin,
                args.journal_pin,
                datetime.now(UTC),
                authorization_trust=trust,
                prediction_policy=policy,
                prediction_policy_pin=args.prediction_policy_pin,
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
        if "prediction" in entry:
            from donghak_stock_vision.storage.prospective_prediction import authorize_horizon

            authorize_horizon(
                request,
                args.request_pin,
                FrozenJSON.freeze(entry["prediction"]["policy"]),
                entry["prediction"]["policy_pin"],
            )
        verify_signal(
            args.capture_root,
            request,
            args.request_pin,
            FrozenJSON.freeze(entry["decision"]),
            entry["decision_pin"],
            authorization_trust=trust,
        )
        if args.action == "prediction-status":
            from donghak_stock_vision.storage.prospective_prediction import state

            require("prediction" in entry, "legacy_signal_has_no_predeclared_prediction")
            print(state(FrozenJSON.freeze(entry["prediction"]), datetime.now(UTC)).payload_json)
            return 0
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
