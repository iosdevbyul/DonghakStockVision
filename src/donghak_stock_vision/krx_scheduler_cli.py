"""Explicit local scheduler operations; installation and enablement default to preview."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage import krx_launchd
from donghak_stock_vision.storage.krx_scheduler import inspect, load_config, run_once


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("krx-scheduler", help="offline status and opt-in daily KRX collection")
    cmd.add_argument(
        "action", choices=("install", "status", "pause", "resume", "uninstall", "run-once")
    )
    cmd.add_argument("--config", type=Path, required=True)
    cmd.add_argument("--config-pin", required=True)
    cmd.add_argument("--apply", action="store_true", help="explicitly apply LaunchAgent management")
    cmd.add_argument("--execute", action="store_true", help="run only if persistently enabled")
    cmd.add_argument("--scheduled", action="store_true")
    cmd.add_argument("--allow-network", action="store_true")
    cmd.add_argument("--confirm-operator-authorization", action="store_true")


def run(args: argparse.Namespace) -> int:
    if args.db is not None:
        raise ValueError("scheduler_uses_explicit_config_not_research_db")
    config = load_config(args.config, args.config_pin)
    if args.action == "status":
        result = FrozenJSON.freeze(
            inspect(config, datetime.now(UTC)).to_dict()
            | {"launchagent": krx_launchd.status(config, args.config)}
        )
    elif args.action == "run-once":
        if args.apply or args.allow_network or args.confirm_operator_authorization:
            raise ValueError("enable_explicitly_with_resume_before_execution")
        result = run_once(config, execute=args.execute, scheduled=args.scheduled)
    else:
        if args.execute or args.scheduled:
            raise ValueError("management_does_not_collect")
        result = krx_launchd.manage(
            config,
            args.config,
            args.action,
            apply=args.apply,
            allow_network=args.allow_network,
            operator_confirmed=args.confirm_operator_authorization,
        )
    print(result.payload_json)
    return int(args.action == "run-once" and result.to_dict().get("outcome") == "failed")
