"""Bounded oldest-first orchestration over original capture and stored-source proofs.

No DB schema, permission authority or alternate HTTP implementation lives here.
"""

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

import httpx

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan, FullMarketScope
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.base import ProviderError
from donghak_stock_vision.storage.forward_capture import DB as CAPTURE_DB
from donghak_stock_vision.storage.forward_capture import _recurring_state, recurring_preview
from donghak_stock_vision.storage.krx_recurring import POLICY
from donghak_stock_vision.storage.krx_scheduler import (
    ATTEMPT,
    SchedulerConfig,
    _credential,
    _daily_plan,
    _lock,
    _policy,
    _read,
    _run_locked,
    _save,
    _state,
    _storage,
    control,
)
from donghak_stock_vision.storage.operational_capture import verify_stored_capture
from donghak_stock_vision.storage.operational_market import OperationalMarketStore

REGISTRY = "verified-completions.json"
VERSION = "krx_scheduler_verified_completions_v1"


def _external(config: SchedulerConfig) -> dict[str, Any]:
    d = _read(
        config, REGISTRY, dict(version=VERSION, records=[], config_pin=config.document.identifier)
    )
    fields(d, "version records config_pin")
    require(
        d["version"] == VERSION and type(d["records"]) is list and len(d["records"]) <= 366,
        "verified_completion_registry_version_or_limit",
    )
    return d


def _records(config: SchedulerConfig) -> list[dict[str, Any]]:
    state, policy = _state(config), _policy(config)
    records = list(_external(config)["records"])
    for r in state["successes"]:
        plan = _daily_plan(policy, datetime.fromisoformat(r["day"]).date())
        require(
            any(
                c["day"] == r["day"]
                and c["capture_pin"] == r["capture_pin"]
                and c["plan_pin"] == plan.document.identifier
                for c in state["captures"]
            ),
            "scheduler_completion_source_reference_missing",
        )
        records.append(
            dict(
                day=r["day"],
                at=r["at"],
                observation_count=r["observation_count"],
                origin=r["origin"],
                plan=plan.document.to_dict(),
                plan_pin=plan.document.identifier,
                attempt=ATTEMPT,
                capture_pin=r["capture_pin"],
                batch_pin=r["batch_pin"],
                authorization=config.reference.to_dict(),
            )
        )
    require(
        len(records) <= 366 and len({r["day"] for r in records}) == len(records),
        "duplicate_or_excess_completed_days",
    )
    return records


def _verify(config: SchedulerConfig, record: dict[str, Any]) -> dict[str, Any]:
    r = fields(
        record,
        "day at observation_count origin plan plan_pin attempt capture_pin batch_pin authorization",
    )
    utc(r["at"])
    plan = ForwardCapturePlan.from_dict(r["plan"])
    require(
        plan.full_market_scope is not None and plan.day.isoformat() == r["day"],
        "completion_requires_original_full_market_plan",
    )
    reference = FrozenJSON.freeze(fields(r["authorization"], "root grant_pin"))
    for n in ("plan_pin", "capture_pin", "batch_pin"):
        sha(r[n])
    proof = verify_stored_capture(
        config.path("operational_root"),
        config.path("capture_root"),
        plan,
        r["plan_pin"],
        r["attempt"],
        r["capture_pin"],
        r["batch_pin"],
        authorization=reference,
        authorization_policy=POLICY,
    ).to_dict()
    require(
        type(r["observation_count"]) is int
        and r["observation_count"] > 0
        and proof["observation_count"] == r["observation_count"]
        and proof["origin"] == r["origin"],
        "stored_completion_evidence_mismatch",
    )
    return {n: r[n] for n in ("day", "at", "observation_count", "origin")}


def register_completion(
    config: SchedulerConfig,
    plan: ForwardCapturePlan,
    plan_pin: str,
    attempt: str,
    capture_pin: str,
    batch_pin: str,
    *,
    authorization: FrozenJSON | None = None,
    at: datetime | None = None,
) -> FrozenJSON:
    """Explicit offline registration; never trust archive/DB self-pins as new roots."""
    plan.__post_init__()
    require(plan.full_market_scope is not None, "completion_requires_original_full_market_plan")
    # Original completed history remains verifiable after a policy renewal; it
    # never grants current permission to request an out-of-scope/stale date.
    reference = authorization if authorization is not None else config.reference
    with _lock(config, ".run.lock"):
        pending = _state(config)["pending"]
        if pending:
            require(
                pending["plan"] == plan.document.to_dict()
                and attempt == ATTEMPT
                and pending["capture_pin"] in (None, capture_pin),
                "pending_registration_identity_mismatch",
            )
        proof = verify_stored_capture(
            config.path("operational_root"),
            config.path("capture_root"),
            plan,
            plan_pin,
            attempt,
            capture_pin,
            batch_pin,
            authorization=reference,
            authorization_policy=POLICY,
        ).to_dict()
        record = dict(
            day=plan.day.isoformat(),
            at=utc(at or datetime.now(UTC)).isoformat(),
            observation_count=proof["observation_count"],
            origin=proof["origin"],
            plan=plan.document.to_dict(),
            plan_pin=sha(plan_pin),
            attempt=attempt,
            capture_pin=sha(capture_pin),
            batch_pin=sha(batch_pin),
            authorization=reference.to_dict(),
        )
        records = _records(config)
        existing = next((r for r in records if r["day"] == record["day"]), None)
        if existing:
            _verify(config, existing)
            require(
                all(
                    existing[n] == record[n]
                    for n in ("plan_pin", "attempt", "capture_pin", "batch_pin", "authorization")
                ),
                "conflicting_completed_day",
            )
        else:
            require(len(records) < 366, "verified_completion_limit")
            data = _external(config)
            data["records"].append(record)
            data["records"].sort(key=lambda r: r["day"])
            require(len(data["records"]) <= 366, "verified_completion_limit")
            _save(config, REGISTRY, data)
        if pending:
            state = _state(config)
            state["pending"] = None
            _save(config, "state.json", state)
    return FrozenJSON.freeze(
        dict(
            version="krx_completion_registration_v1",
            day=record["day"],
            observation_count=proof["observation_count"],
            already_registered=existing is not None,
            original_source_and_database_verified=True,
            network_requests=0,
        )
    )


def backlog(config: SchedulerConfig, at: datetime) -> FrozenJSON:
    """Read-only; policy dates and verified original stored sources reconstruct gaps."""
    policy, state = _policy(config), _state(config)
    scope = FullMarketScope.from_dict(policy["full_market_scope"])
    lag = policy["max_observation_lag_days"]
    require(type(lag) is int and 1 <= lag <= 7, "recurring_observation_lag_invalid")
    today = utc(at).astimezone(SEOUL).date()
    completed = sorted((_verify(config, r) for r in _records(config)), key=lambda r: r["day"])
    known = {r["day"] for r in completed}
    # Existing observations block a new request even when local capture history is lost.
    # They are not declared complete without independent original-source/batch proofs.
    with closing(OperationalMarketStore(config.path("operational_root"))) as store:
        days = [d.isoformat() for d in scope.permitted_days]
        ledger_missing = (
            not (config.path("capture_root") / CAPTURE_DB).is_file()
            and store.connection.execute(
                "SELECT 1 FROM ingestion_batches WHERE "
                "json_type(document,'$.request.source_capture')='object' LIMIT 1",
            ).fetchone()
            is not None
        )
        stored = {
            r[0]
            for r in store.connection.execute(
                "SELECT DISTINCT trading_date FROM market_observations WHERE market='KOSPI' "
                + "AND trading_date IN ("
                + ",".join("?" for _ in days)
                + ") LIMIT 367",
                days,
            )
        }
    unverified_stored: list[str] = []
    due: list[str] = []
    reserved: list[str] = []
    expired: list[str] = []
    future: list[str] = []
    local = None
    for day in scope.permitted_days:
        if day.isoformat() in known:
            continue
        age = (today - day).days
        if age <= 0:
            future.append(day.isoformat())
        elif age > lag:
            expired.append(day.isoformat())
        else:
            local = _recurring_state(config.path("capture_root"), _daily_plan(policy, day), at)
            if local["duplicate"]:
                reserved.append(day.isoformat())
            elif day.isoformat() in stored:
                unverified_stored.append(day.isoformat())
            else:
                due.append(day.isoformat())
    readiness = None
    if due:
        try:
            key = _credential(config)
            readiness = recurring_preview(
                _daily_plan(policy, datetime.fromisoformat(due[0]).date()),
                ATTEMPT,
                config.reference,
                None,
                at,
                bool(key),
                credential=key,
                capture_root=config.path("capture_root"),
            ).to_dict()
        except (ValueError, OSError, KeyError, TypeError):
            readiness = dict(
                ready_for_explicit_read_only_capture=False,
                blockers=["credentials_unavailable"],
                network_requests=0,
            )
    storage = _storage(config)
    last = _read(config, "last-batch.json", {})
    if last:
        require(
            last.get("version") == "krx_collection_batch_v1"
            and type(last.get("results")) is list
            and len(last["results"]) <= 7,
            "batch_summary_version_or_limit",
        )
        last = {k: v for k, v in last.items() if k != "config_pin"}
    return FrozenJSON.freeze(
        dict(
            version="krx_collection_backlog_v1",
            last_batch=last or None,
            next_request_readiness=readiness,
            completed=completed,
            due_dates=due,
            expired_dates=expired,
            reserved_without_verified_completion=reserved,
            stored_without_verified_history=unverified_stored,
            capture_ledger_missing_with_stored_sources=ledger_missing,
            not_yet_eligible_dates=future,
            missing_dates=sorted(due + expired + reserved + unverified_stored),
            oldest_due_date=due[0] if due else None,
            pending_phase=state["pending"]["phase"] if state["pending"] else None,
            local_request_budget=(
                dict(
                    used=local["locally_used_requests"],
                    limit=policy["daily_limit"],
                    remaining=max(0, policy["daily_limit"] - local["locally_used_requests"]),
                    paused=local["paused"],
                )
                if local
                else None
            ),
            latest_persisted_trading_date=storage["latest_trading_date"],
            stored_observations=storage["observation_count"],
            storage_capacity_ready=(
                storage["disk_usage"]["free_space_requirement_met"]
                and storage["disk_usage"]["database_limit_met"]
            ),
            enabled=control(config)["enabled"],
            network_requests=0,
        )
    )


def run_due(
    config: SchedulerConfig,
    *,
    execute: bool = False,
    scheduled: bool = False,
    max_runs: int = 7,
    max_seconds: int = 240,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> FrozenJSON:
    require(
        type(max_runs) is int
        and 1 <= max_runs <= 7
        and type(max_seconds) is int
        and 1 <= max_seconds <= 300,
        "bounded_backlog_run_limits_required",
    )
    if not execute:
        return backlog(config, clock())
    with _lock(config, ".run.lock"):
        start, started = monotonic(), utc(clock())
        results: list[dict[str, Any]] = []
        reason = "run_limit_reached"
        try:
            with closing(OperationalMarketStore(config.path("operational_root"))) as store:
                require(store.validate().to_dict()["valid"], "operational_integrity_failure")
            for _ in range(max_runs):
                if monotonic() - start >= max_seconds:
                    reason = "cooperative_batch_time_limit"
                    break
                if not control(config)["enabled"]:
                    reason = "scheduler_disabled"
                    break
                state = _state(config)
                if state["pending"] is not None:
                    # Resume pinned ingestion offline, or stop on unknown request outcome.
                    result = _run_locked(
                        config,
                        scheduled=scheduled,
                        client=client,
                        clock=clock,
                        monotonic=monotonic,
                        sleep=sleep,
                    )
                else:
                    view = backlog(config, clock()).to_dict()
                    if (
                        view["stored_without_verified_history"]
                        or view["capture_ledger_missing_with_stored_sources"]
                    ):
                        reason = "stored_data_requires_original_completion_registration"
                        break
                    if not view["storage_capacity_ready"]:
                        reason = "storage_capacity_limit"
                        break
                    if not view["due_dates"]:
                        reason = "no_due_dates"
                        break
                    if len(view["completed"]) >= 366 or len(state["captures"]) >= 366:
                        reason = "completion_capacity_limit"
                        break
                    budget = view["local_request_budget"]
                    if budget and (budget["paused"] or budget["remaining"] == 0):
                        reason = (
                            "collection_paused" if budget["paused"] else "daily_budget_exhausted"
                        )
                        break
                    if monotonic() - start >= max_seconds:
                        reason = "cooperative_batch_time_limit"
                        break
                    result = _run_locked(
                        config,
                        scheduled=scheduled,
                        client=client,
                        clock=clock,
                        monotonic=monotonic,
                        sleep=sleep,
                        target_day=datetime.fromisoformat(view["oldest_due_date"]).date(),
                    )
                value = result.to_dict()
                results.append(value)
                if value["outcome"] != "succeeded":
                    reason = value["reason"]
                    break
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, ProviderError):
            reason = "completion_or_storage_verification_failed"
        outcome = (
            "failed"
            if reason == "completion_or_storage_verification_failed"
            or any(r["outcome"] == "failed" for r in results)
            else "stopped"
        )
        report = FrozenJSON.freeze(
            dict(
                version="krx_collection_batch_v1",
                started_at=started.isoformat(),
                completed_at=utc(clock()).isoformat(),
                outcome=outcome,
                stop_reason=reason,
                max_runs=max_runs,
                max_seconds=max_seconds,
                results=results,
                pit_qualified=False,
                forward_signal_eligible=False,
                paper_execution_eligible=False,
                execution_authorized=False,
            )
        )
        _save(config, "last-batch.json", report.to_dict())
        return report
