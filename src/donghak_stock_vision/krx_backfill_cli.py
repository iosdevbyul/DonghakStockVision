"""Explicit pinned historical acquisition; preview never loads a key or changes a scheduler."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, utc
from donghak_stock_vision.data.krx_backfill import BackfillPolicy
from donghak_stock_vision.storage.krx_authorization import bounded
from donghak_stock_vision.storage.krx_backfill import (
    _private_file,
    calendar_days,
    context,
    load_plan,
    load_policy,
    prepare_plan,
    read_pin,
    run_backfill,
    write_new,
)


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("krx-backfill", help="separate approved historical KOSPI capture")
    cmd.add_argument("action", choices=("pin-policy", "prepare-plan", "inspect", "run"))
    for name in ("policy", "policy-pin-file"):
        cmd.add_argument("--" + name, type=Path, required=True)
    for name in ("plan", "plan-pin-file"):
        cmd.add_argument("--" + name, type=Path)
    cmd.add_argument("--execute", action="store_true")
    cmd.add_argument("--allow-network", action="store_true")
    cmd.add_argument("--confirm-operator-authorization", action="store_true")
    cmd.add_argument("--allow-synthetic-fixture", action="store_true")
    cmd.add_argument("--continue-date-failures", action="store_true")
    cmd.add_argument("--max-dates", type=int, default=2)
    cmd.add_argument("--max-seconds", type=int, default=120)


def run(args: argparse.Namespace) -> int:
    require(args.db is None, "backfill_cannot_write_research_database")
    if args.action != "run":
        require(
            not args.execute and not args.allow_network and not args.allow_synthetic_fixture,
            "backfill_preparation_is_offline",
        )
    require(
        args.action in ("pin-policy", "run") or not args.confirm_operator_authorization,
        "operator_confirmation_only_for_explicit_review_or_run",
    )
    at = datetime.now(UTC)
    if args.action == "pin-policy":
        require(args.confirm_operator_authorization, "explicit_historical_policy_review_required")
        _private_file(args.policy)
        policy = BackfillPolicy(FrozenJSON(bounded(args.policy, 65536).decode()))
        require(
            abs((at - utc(policy.values["reviewed_at"])).total_seconds()) <= 60,
            "new_backfill_policy_review_requires_current_time",
        )
        context(policy, at)
        calendar_days(policy)
    else:
        policy = load_policy(args.policy, read_pin(args.policy_pin_file))
    archive = Path(policy.values["archive_root"])
    require(
        args.policy_pin_file.parent != args.policy.parent
        and not args.policy_pin_file.is_relative_to(archive),
        "independent_backfill_policy_pin_required",
    )
    if args.action == "pin-policy":
        write_new(args.policy_pin_file, policy.document.identifier)
        result = FrozenJSON.freeze(
            dict(version="krx_backfill_policy_review_v1", pin_retained=True, network_requests=0)
        )
    else:
        require(
            args.plan is not None and args.plan_pin_file is not None,
            "explicit_backfill_plan_and_pin_required",
        )
        require(
            args.plan_pin_file.parent != args.plan.parent
            and not args.plan_pin_file.is_relative_to(archive),
            "independent_backfill_plan_pin_required",
        )
        if args.action == "prepare-plan":
            result_plan = prepare_plan(policy, at)
            write_new(args.plan, result_plan.document.payload_json)
            write_new(args.plan_pin_file, result_plan.document.identifier)
            result = FrozenJSON.freeze(
                dict(
                    version="krx_backfill_plan_prepared_v1",
                    planned_dates=result_plan.document.to_dict()["dates"],
                    skipped=result_plan.document.to_dict()["skipped"],
                    network_requests=0,
                )
            )
        else:
            plan = load_plan(args.plan, read_pin(args.plan_pin_file))
            if args.action == "inspect":
                require(
                    not args.execute and not args.allow_network, "backfill_inspection_is_read_only"
                )
            result = run_backfill(
                policy,
                plan,
                execute=args.execute if args.action == "run" else False,
                allow_network=args.allow_network,
                operator_confirmed=args.confirm_operator_authorization,
                allow_synthetic_fixture=args.allow_synthetic_fixture,
                continue_date_failures=args.continue_date_failures,
                max_dates=args.max_dates,
                max_seconds=args.max_seconds,
            )
    print(result.payload_json)
    value = result.to_dict()
    return int(
        any(r["outcome"] != "succeeded" for r in value.get("results", []))
        or value.get("stop_reason")
        in {
            "explicit_backfill_network_authorization_required",
            "backfill_previous_failure_requires_review_no_retry",
        }
    )
