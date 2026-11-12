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
        "action",
        choices=(
            "install",
            "status",
            "pause",
            "resume",
            "uninstall",
            "run-once",
            "preflight",
            "pilot-preflight",
            "diagnose",
            "backlog",
            "run-due",
            "register-completion",
        ),
    )
    cmd.add_argument("--config", type=Path, required=True)
    cmd.add_argument("--config-pin", required=True)
    cmd.add_argument("--max-runs", type=int, default=7)
    cmd.add_argument("--max-batch-seconds", type=int, default=240)
    cmd.add_argument("--source-plan", type=Path)
    cmd.add_argument("--source-plan-pin")
    cmd.add_argument("--source-attempt")
    cmd.add_argument("--capture-pin")
    cmd.add_argument("--batch-pin")
    cmd.add_argument("--source-authorization-root", type=Path)
    cmd.add_argument("--source-authorization-pin")
    for name in ("research-output-db", "research-model-db"):
        cmd.add_argument(f"--{name}", type=Path)
    cmd.add_argument("--research-model-version")
    cmd.add_argument("--research-tickers", nargs="+")
    cmd.add_argument("--research-lookback-days", type=int, default=60)
    cmd.add_argument("--research-allow-synthetic-fixture", action="store_true")
    cmd.add_argument("--apply", action="store_true", help="explicitly apply LaunchAgent management")
    cmd.add_argument("--execute", action="store_true", help="run only if persistently enabled")
    cmd.add_argument("--scheduled", action="store_true")
    cmd.add_argument("--allow-network", action="store_true")
    cmd.add_argument("--confirm-operator-authorization", action="store_true")


def run(args: argparse.Namespace) -> int:
    if args.db is not None:
        raise ValueError("scheduler_uses_explicit_config_not_research_db")
    config = load_config(args.config, args.config_pin)
    research_values = (
        getattr(args, "research_output_db", None),
        getattr(args, "research_model_db", None),
        getattr(args, "research_model_version", None),
        getattr(args, "research_tickers", None),
    )
    research_requested = any(value is not None for value in research_values)
    if research_requested and (
        not all(research_values) or args.action not in ("run-once", "run-due") or not args.execute
    ):
        raise ValueError("daily_research_requires_complete_explicit_post_collection_options")
    if getattr(args, "research_allow_synthetic_fixture", False) and not research_requested:
        raise ValueError("daily_research_options_required")
    if args.action in ("backlog", "run-due"):
        from donghak_stock_vision.storage.krx_backlog import run_due

        if args.apply or args.allow_network or args.confirm_operator_authorization:
            raise ValueError("enable_explicitly_with_resume_before_execution")
        if args.action == "backlog" and (args.execute or args.scheduled):
            raise ValueError("backlog_inspection_is_read_only")
        result = run_due(
            config,
            execute=args.execute if args.action == "run-due" else False,
            scheduled=args.scheduled,
            max_runs=args.max_runs,
            max_seconds=args.max_batch_seconds,
            clock=lambda: datetime.now(UTC),
        )
    elif args.action == "register-completion":
        from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
        from donghak_stock_vision.storage.krx_authorization import bounded
        from donghak_stock_vision.storage.krx_backlog import register_completion

        if any(
            (
                args.apply,
                args.execute,
                args.scheduled,
                args.allow_network,
                args.confirm_operator_authorization,
            )
        ):
            raise ValueError("completion_registration_is_offline")
        if not all(
            (
                args.source_plan,
                args.source_plan_pin,
                args.source_attempt,
                args.capture_pin,
                args.batch_pin,
            )
        ):
            raise ValueError("explicit_original_source_and_batch_pins_required")
        if bool(args.source_authorization_root) != bool(args.source_authorization_pin):
            raise ValueError("complete_original_authorization_reference_required")
        reference = (
            FrozenJSON.freeze(
                dict(
                    root=str(args.source_authorization_root),
                    grant_pin=args.source_authorization_pin,
                )
            )
            if args.source_authorization_root
            else config.reference
        )
        plan = ForwardCapturePlan.from_dict(
            FrozenJSON(bounded(args.source_plan, 32768).decode()).to_dict()
        )
        result = register_completion(
            config,
            plan,
            args.source_plan_pin,
            args.source_attempt,
            args.capture_pin,
            args.batch_pin,
            authorization=reference,
        )
    elif args.action == "diagnose":
        from donghak_stock_vision.storage.krx_backlog import backlog
        from donghak_stock_vision.storage.krx_operations_diagnostics import summarize
        from donghak_stock_vision.storage.krx_readiness import preflight

        if any(
            (
                args.apply,
                args.execute,
                args.scheduled,
                args.allow_network,
                args.confirm_operator_authorization,
            )
        ):
            raise ValueError("diagnostics_are_read_only")
        at = datetime.now(UTC)
        result = FrozenJSON.freeze(
            summarize(backlog(config, at).to_dict(), preflight(config, at, args.config).to_dict())
        )
    elif args.action == "pilot-preflight":
        from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
        from donghak_stock_vision.storage.krx_authorization import bounded
        from donghak_stock_vision.storage.krx_pilot import PilotEvidence, pilot_preflight

        if any(
            (
                args.apply,
                args.execute,
                args.scheduled,
                args.allow_network,
                args.confirm_operator_authorization,
            )
        ):
            raise ValueError("pilot_preflight_is_read_only")
        supplied = [
            args.source_plan,
            args.source_plan_pin,
            args.source_attempt,
            args.capture_pin,
            args.batch_pin,
        ]
        if any(value is not None for value in supplied) and not all(
            value is not None for value in supplied
        ):
            raise ValueError("complete_pinned_pilot_source_required")
        if bool(args.source_authorization_root) != bool(args.source_authorization_pin):
            raise ValueError("complete_original_authorization_reference_required")
        if args.source_plan is None and args.source_authorization_root is not None:
            raise ValueError("unbound_source_authorization_reference")
        evidence = None
        if args.source_plan is not None:
            assert args.source_plan_pin is not None
            assert args.source_attempt is not None
            assert args.capture_pin is not None
            assert args.batch_pin is not None
            reference = (
                FrozenJSON.freeze(
                    {
                        "root": str(args.source_authorization_root),
                        "grant_pin": args.source_authorization_pin,
                    }
                )
                if args.source_authorization_root is not None
                else config.reference
            )
            evidence = PilotEvidence(
                capture_root=config.path("capture_root"),
                plan=ForwardCapturePlan.from_dict(
                    FrozenJSON(bounded(args.source_plan, 32768).decode()).to_dict()
                ),
                plan_pin=args.source_plan_pin,
                attempt=args.source_attempt,
                capture_pin=args.capture_pin,
                batch_pin=args.batch_pin,
                authorization=reference,
                authorization_policy="operator_full_market_recurring_v1",
            )
        result = pilot_preflight(config, datetime.now(UTC), args.config, evidence=evidence)
    elif args.action == "preflight":
        from donghak_stock_vision.storage.krx_readiness import preflight

        if any(
            (
                args.apply,
                args.execute,
                args.scheduled,
                args.allow_network,
                args.confirm_operator_authorization,
            )
        ):
            raise ValueError("preflight_is_read_only")
        result = preflight(config, datetime.now(UTC), args.config)
    elif args.action == "status":
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
    collection_failed = result.to_dict().get("outcome") == "failed"
    if research_requested:
        from donghak_stock_vision.storage.daily_research import after_collection

        result = after_collection(
            config,
            result,
            args.research_output_db,
            args.research_model_db,
            args.research_model_version,
            tuple(sorted(set(args.research_tickers))),
            datetime.now(UTC),
            lookback_days=getattr(args, "research_lookback_days", 60),
            allow_synthetic_fixture=getattr(args, "research_allow_synthetic_fixture", False),
        )
    print(result.payload_json)
    return int(args.action in ("run-once", "run-due") and collection_failed)
