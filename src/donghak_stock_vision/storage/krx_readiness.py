"""Read-only operational preflight over existing KRX policy/store/scheduler owners.

This summary is not authorization, independent custody or a request reservation.
"""

import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, utc
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage import krx_launchd
from donghak_stock_vision.storage.forward_capture import recurring_preview
from donghak_stock_vision.storage.krx_recurring import verify_recurring_authorization
from donghak_stock_vision.storage.krx_scheduler import (
    ATTEMPT,
    SchedulerConfig,
    _credential,
    _plan,
    _state,
    control,
    host_seoul,
)
from donghak_stock_vision.storage.operational_market import OperationalMarketStore


def preflight(config: SchedulerConfig, at: datetime, config_path: Path) -> FrozenJSON:
    at = utc(at)
    checks: dict[str, Any] = {}
    blockers: list[str] = []

    def check(
        name: str, action: str, operation: Callable[[], Any], *, required: bool = True
    ) -> Any:
        try:
            value = operation()
            checks[name] = dict(state="verified_locally", reason=None, next_action=None)
            return value
        except (
            ValueError,
            OSError,
            sqlite3.Error,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ):
            # Never emit original exception text, private paths, pins or credentials.
            checks[name] = dict(state="blocked", reason=name + "_not_ready", next_action=action)
            if required:
                blockers.append(name + "_not_ready")
            return None

    def credentials() -> str:
        value = _credential(config)
        require(bool(value), "missing_credentials")
        return value

    key = check("credential_presence", "Configure the existing private KRX_API_KEY.", credentials)
    plan_policy = check(
        "plan_scope",
        "Review and pin a full-market recurring policy with explicit permitted sessions and lag.",
        lambda: _plan(config, at),
    )
    preview = None
    if plan_policy is not None:
        plan, _ = plan_policy
        owner = check(
            "service_policy_originals",
            "Review service validity, original bytes, scope, policy window and independent pin.",
            lambda: verify_recurring_authorization(plan, ATTEMPT, config.reference, None, at),
        )
        preview = recurring_preview(
            plan,
            ATTEMPT,
            config.reference,
            None,
            at,
            bool(key),
            credential=key,
            capture_root=config.path("capture_root"),
        ).to_dict()
        # Existing preview emits fixed validation codes; reject accidental non-code output.
        reasons = preview["blockers"]
        safe = [r for r in reasons if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", r)]
        if reasons:
            blockers.extend(safe or ["recurring_preview_not_ready"])
        checks["credential_binding_and_local_budget"] = dict(
            state="verified_locally" if not reasons else "blocked",
            reason=safe if reasons else None,
            next_action="Resolve existing recurring authorization-readiness blockers."
            if reasons
            else None,
        )
        today = at.astimezone(SEOUL).date()
        assert plan.full_market_scope is not None
        if today not in plan.full_market_scope.permitted_days:
            blockers.append("today_not_permitted_collection_day")
        transport = owner.to_dict()["transport"] if owner else None
    else:
        transport = None
        checks["service_policy_originals"] = dict(
            state="unavailable",
            reason="pinned_recurring_policy_required",
            next_action="Provision a reviewed recurring package; never reuse a pilot grant.",
        )

    def archive() -> None:
        p = config.path("capture_root")
        require(
            p.is_dir()
            and not p.is_symlink()
            and p.stat().st_uid == os.getuid()
            and p.stat().st_mode & 0o077 == 0
            and os.access(p, os.R_OK | os.W_OK | os.X_OK),
            "private_accessible_archive_required",
        )

    check(
        "archive_access",
        "Prepare the reviewed new private 0700 full-market archive directory.",
        archive,
    )

    def storage() -> dict[str, Any]:
        with closing(OperationalMarketStore(config.path("operational_root"))) as store:
            status = store.status().to_dict()
            require(store.validate().to_dict()["valid"], "operational_storage_integrity_failure")
            require(
                status["disk_usage"]["free_space_requirement_met"]
                and status["disk_usage"]["database_limit_met"],
                "storage_capacity_limit",
            )
            return status

    stored = check(
        "operational_storage",
        "Initialize the separate operational store, then run market-storage validate.",
        storage,
    )

    def scheduler() -> dict[str, Any]:
        state = _state(config)
        require(state["pending"] is None, "investigate_pending_capture_before_new_request")
        return control(config)

    settings = check(
        "scheduler_state",
        "Inspect existing pending capture/state; never retry an uncertain request.",
        scheduler,
    )
    agent = check(
        "launchagent",
        "Inspect the owned LaunchAgent configuration without enabling it.",
        lambda: krx_launchd.status(config, config_path),
        required=False,
    )
    timezone = host_seoul()
    checks["host_timezone"] = dict(
        state="verified_locally" if timezone else "blocked",
        reason=None if timezone else "host_timezone_not_asia_seoul",
        next_action=None
        if timezone
        else "Set the Mac system timezone to Asia/Seoul before scheduled enablement.",
    )
    ready = not blockers
    return FrozenJSON.freeze(
        dict(
            version="krx_production_preflight_v1",
            checked_at=at.isoformat(),
            checks=checks,
            blockers=list(dict.fromkeys(blockers)),
            selected_trading_date=plan_policy[0].day.isoformat() if plan_policy else None,
            request=(
                {
                    n: preview[n]
                    for n in (
                        "endpoint",
                        "method",
                        "parameters",
                        "requests_maximum",
                        "retries",
                        "concurrency",
                        "max_seconds",
                        "max_response_bytes",
                        "approved_from",
                        "approved_through",
                        "valid_from",
                        "valid_until",
                        "configured_daily_limit",
                        "locally_used_requests",
                        "remaining_local_allowance",
                        "paused",
                        "duplicate",
                        "credential_binding_verified",
                    )
                }
                if preview
                else None
            ),
            origin=transport,
            ready_for_manual_provider_capture=ready and transport == "provider_https",
            ready_for_mock_capture=ready and transport == "mock_fixture",
            ready_for_scheduled_collection=bool(
                ready and transport == "provider_https" and timezone and agent and agent["enabled"]
            ),
            scheduler_application_enabled=settings["enabled"] if settings else None,
            launchagent=agent,
            storage=(
                {
                    n: stored[n]
                    for n in (
                        "observation_count",
                        "latest_trading_date",
                        "duplicate_records",
                        "disk_usage",
                    )
                }
                if stored
                else None
            ),
            explicit_operator_confirmation_still_required=True,
            network_requests=0,
            policy_created=False,
            launchagent_changed=False,
            pit_qualified=False,
            forward_signal_eligible=False,
            paper_execution_eligible=False,
            execution_authorized=False,
        )
    )
