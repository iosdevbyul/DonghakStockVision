"""One-shot local automation over existing recurring capture/operational storage.

Only mutable scheduler summaries live here, not another market DB or permission store.
Uncertain capture outcomes never authorize another network attempt.
"""

import fcntl
import io
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan, FullMarketScope
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.forward_capture import capture_client
from donghak_stock_vision.storage.forward_capture import (
    _recurring_state,
    recurring_preview,
    run_capture,
    verify_capture,
)
from donghak_stock_vision.storage.krx_authorization import bounded
from donghak_stock_vision.storage.krx_recurring import POLICY
from donghak_stock_vision.storage.operational_capture import ingest_capture
from donghak_stock_vision.storage.operational_market import OperationalMarketStore

ATTEMPT = "scheduler-daily"
MAX_STATE = 512 * 1024


def host_seoul() -> bool:
    """launchd's calendar uses the host zone, independently of the child's TZ."""
    return str(Path("/etc/localtime").resolve()).endswith("/Asia/Seoul")


def _path(value: str) -> Path:
    require(type(value) is str, "absolute_scheduler_path_required")
    p = Path(value)
    require(p.is_absolute() and p.resolve() == p, "canonical_scheduler_path_required")
    return p


@dataclass(frozen=True)
class SchedulerConfig:
    document: FrozenJSON

    def __post_init__(self) -> None:
        d = fields(
            self.document.to_dict(),
            "version policy_root policy_pin capture_root operational_root runner_root "
            "credentials_file python_executable working_directory hour minute timezone",
        )
        require(
            d["version"] == "krx_scheduler_config_v1" and d["timezone"] == "Asia/Seoul",
            "scheduler_config_version_or_timezone",
        )
        paths = {
            n: _path(d[n])
            for n in (
                "policy_root",
                "capture_root",
                "operational_root",
                "runner_root",
                "working_directory",
            )
        }
        for n in ("runner_root", "operational_root"):
            require(
                all(
                    not paths[n].is_relative_to(paths[s]) and not paths[s].is_relative_to(paths[n])
                    for s in ("capture_root", "policy_root")
                ),
                "separate_scheduler_storage_required",
            )
        require(
            paths["runner_root"] != paths["operational_root"], "separate_runner_directory_required"
        )
        if d["credentials_file"] is not None:
            _path(d["credentials_file"])
        sha(d["policy_pin"])
        executable = Path(d["python_executable"])
        # A venv interpreter is normally a symlink. Preserve its absolute venv path.
        require(
            executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK),
            "configured_python_executable_required",
        )
        require(paths["working_directory"].is_dir(), "scheduler_working_directory_missing")
        require(
            type(d["hour"]) is int
            and 0 <= d["hour"] <= 23
            and type(d["minute"]) is int
            and 0 <= d["minute"] <= 59,
            "scheduler_time_invalid",
        )

    def path(self, name: str) -> Path:
        return Path(self.document.to_dict()[name])

    @property
    def reference(self) -> FrozenJSON:
        d = self.document.to_dict()
        return FrozenJSON.freeze(dict(root=d["policy_root"], grant_pin=d["policy_pin"]))


def load_config(path: Path, pin: str) -> SchedulerConfig:
    require(
        path.is_absolute() and path.resolve() == path, "canonical_scheduler_config_path_required"
    )
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077 == 0,
        "private_scheduler_config_required",
    )
    doc = FrozenJSON(bounded(path, 32768).decode())
    require(doc.identifier == sha(pin), "scheduler_config_pin_mismatch")
    return SchedulerConfig(doc)


def _root(config: SchedulerConfig, *, create: bool = False) -> Path:
    root = config.path("runner_root")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(
        not root.is_symlink()
        and (
            not root.exists()
            or (
                root.is_dir()
                and root.stat().st_uid == os.getuid()
                and root.stat().st_mode & 0o077 == 0
            )
        ),
        "private_scheduler_directory_required",
    )
    return root


def _read(config: SchedulerConfig, name: str, default: dict[str, Any]) -> dict[str, Any]:
    path = _root(config) / name
    if not path.exists():
        return default
    require(
        not path.is_symlink() and path.stat().st_mode & 0o077 == 0,
        "private_scheduler_state_required",
    )
    envelope = fields(FrozenJSON(bounded(path, MAX_STATE).decode()).to_dict(), "document pin")
    doc = FrozenJSON.freeze(envelope["document"])
    require(
        doc.identifier == sha(envelope["pin"])
        and doc.to_dict().get("config_pin") == config.document.identifier,
        "scheduler_state_integrity_or_config_mismatch",
    )
    return doc.to_dict()


def _save(config: SchedulerConfig, name: str, value: dict[str, Any]) -> None:
    root = _root(config, create=True)
    doc = FrozenJSON.freeze(value | {"config_pin": config.document.identifier})
    body = FrozenJSON.freeze(dict(document=doc.to_dict(), pin=doc.identifier)).payload_json.encode()
    require(len(body) <= MAX_STATE, "scheduler_summary_size_limit")
    target = root / name
    require(not target.is_symlink(), "scheduler_state_symlink_forbidden")
    if target.exists():
        existing = _read(config, name, {})
        require(existing.get("version") == value.get("version"), "scheduler_state_version_mismatch")
    descriptor, temp = tempfile.mkstemp(prefix=".state-", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def _lock(config: SchedulerConfig, name: str) -> Iterator[None]:
    path = _root(config, create=True) / name
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("scheduler_already_running") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def control(config: SchedulerConfig) -> dict[str, Any]:
    d = _read(
        config,
        "control.json",
        dict(
            version="krx_scheduler_control_v1",
            enabled=False,
            network_opt_in=False,
            operator_confirmed=False,
            config_pin=config.document.identifier,
        ),
    )
    fields(d, "version enabled network_opt_in operator_confirmed config_pin")
    require(
        d["version"] == "krx_scheduler_control_v1"
        and all(type(d[n]) is bool for n in ("enabled", "network_opt_in", "operator_confirmed")),
        "invalid_scheduler_control",
    )
    require(
        not d["enabled"] or (d["network_opt_in"] and d["operator_confirmed"]),
        "scheduler_enablement_not_authorized",
    )
    return d


def set_enabled(
    config: SchedulerConfig,
    enabled: bool,
    *,
    allow_network: bool = False,
    operator_confirmed: bool = False,
) -> FrozenJSON:
    require(
        type(enabled) is bool
        and (not enabled or (allow_network is True and operator_confirmed is True)),
        "explicit_scheduler_network_and_operator_enablement_required",
    )
    with _lock(config, ".control.lock"):
        _save(
            config,
            "control.json",
            dict(
                version="krx_scheduler_control_v1",
                enabled=enabled,
                network_opt_in=enabled,
                operator_confirmed=enabled,
            ),
        )
    return FrozenJSON.freeze(
        dict(version="krx_scheduler_enablement_v1", enabled=enabled, network_requests=0)
    )


def _state(config: SchedulerConfig) -> dict[str, Any]:
    d = _read(
        config,
        "state.json",
        dict(
            version="krx_scheduler_state_v1",
            pending=None,
            last_run=None,
            successes=[],
            captures=[],
            config_pin=config.document.identifier,
        ),
    )
    fields(d, "version pending last_run successes captures config_pin")
    require(
        d["version"] == "krx_scheduler_state_v1"
        and type(d["successes"]) is list
        and len(d["successes"]) <= 366,
        "scheduler_history_limit_or_version",
    )
    require(
        type(d["captures"]) is list and len(d["captures"]) <= 366,
        "scheduler_capture_reference_limit",
    )
    for r in d["captures"]:
        fields(r, "day at capture_pin plan_pin")
        date.fromisoformat(r["day"])
        utc(r["at"])
        sha(r["capture_pin"])
        sha(r["plan_pin"])
    require(
        len({r["day"] for r in d["captures"]}) == len(d["captures"]), "duplicate_scheduler_capture"
    )
    for r in d["successes"]:
        fields(r, "day at observation_count capture_pin batch_pin origin")
        date.fromisoformat(r["day"])
        utc(r["at"])
        sha(r["capture_pin"])
        sha(r["batch_pin"])
    require(
        len({r["day"] for r in d["successes"]}) == len(d["successes"]),
        "duplicate_scheduler_completion",
    )
    if d["pending"] is not None:
        r = fields(d["pending"], "phase plan capture_pin started_at")
        require(r["phase"] in ("capture_pending", "ingest_pending"), "unknown_scheduler_phase")
        ForwardCapturePlan.from_dict(r["plan"])
        utc(r["started_at"])
        if r["capture_pin"] is not None:
            sha(r["capture_pin"])
    return d


def _credential(config: SchedulerConfig) -> str:
    key = os.environ.get("KRX_API_KEY", "")
    file = config.document.to_dict()["credentials_file"]
    if not key and file is not None:
        path = Path(file)
        require(
            path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077 == 0,
            "private_scheduler_credentials_required",
        )
        key = (
            dotenv_values(stream=io.StringIO(bounded(path, 16384).decode())).get("KRX_API_KEY")
            or ""
        )
    return key


def _policy(config: SchedulerConfig) -> dict[str, Any]:
    d = config.reference.to_dict()
    root = Path(d["root"])
    require(
        root.is_dir() and not root.is_symlink() and root.stat().st_mode & 0o077 == 0,
        "private_authorization_root_required",
    )
    owner = FrozenJSON(bounded(root / "recurring-policy.json", 65536).decode())
    require(owner.identifier == d["grant_pin"], "recurring_policy_pin_mismatch")
    return owner.to_dict()


def _plan(config: SchedulerConfig, at: datetime) -> tuple[ForwardCapturePlan, dict[str, Any]]:
    policy = _policy(config)
    owner = FrozenJSON.freeze(policy)
    scope = FullMarketScope.from_dict(policy["full_market_scope"])
    require(
        type(policy["max_observation_lag_days"]) is int
        and 1 <= policy["max_observation_lag_days"] <= 7,
        "recurring_observation_lag_invalid",
    )
    today = utc(at).astimezone(SEOUL).date()
    days = [
        day
        for day in scope.permitted_days
        if 1 <= (today - day).days <= policy["max_observation_lag_days"]
    ]
    require(bool(days), "no_eligible_completed_trading_date")
    day = max(days)  # Never choose an older missed date just because latest is a duplicate.
    plan = ForwardCapturePlan(
        "KOSPI",
        "",
        day,
        "forward_market_observation",
        None,
        None,
        1,
        policy["max_response_bytes"],
        policy["max_seconds"],
        policy["interval_seconds"],
        policy["daily_limit"],
        "authorized_provider",
        owner.identifier,
        policy["terms"]["sha256"],
        full_market_scope=scope,
    )
    return plan, policy


def _storage(config: SchedulerConfig) -> dict[str, Any]:
    with closing(OperationalMarketStore(config.path("operational_root"))) as store:
        return store.status().to_dict()


def inspect(config: SchedulerConfig, at: datetime) -> FrozenJSON:
    state = _state(config)
    enabled = control(config)
    blockers = []
    candidate = None
    policy = None
    scope = None
    assessment = None
    storage = None
    try:
        policy = _policy(config)
        scope = FullMarketScope.from_dict(policy["full_market_scope"])
        candidate, _ = _plan(config, at)
        key = _credential(config)
        assessment = recurring_preview(
            candidate,
            ATTEMPT,
            config.reference,
            None,
            at,
            bool(key),
            credential=key,
            capture_root=config.path("capture_root"),
        ).to_dict()
        blockers.extend(assessment["blockers"])
    except (ValueError, OSError, KeyError, TypeError):
        blockers.append("policy_or_credentials_unavailable")
    if candidate is not None and (
        assessment is None or assessment["locally_used_requests"] is None
    ):
        try:
            local = _recurring_state(config.path("capture_root"), candidate, at)
            assessment = (
                (assessment or {})
                | local
                | {
                    "configured_daily_limit": candidate.daily_limit,
                    "remaining_local_allowance": max(
                        0, candidate.daily_limit - local["locally_used_requests"]
                    ),
                }
            )
        except (ValueError, OSError, sqlite3.Error):
            blockers.append("local_capture_state_unavailable")
    try:
        storage = _storage(config)
    except (ValueError, OSError, sqlite3.Error):
        blockers.append("operational_storage_unavailable")
    complete = {r["day"] for r in state["successes"]}
    today = utc(at).astimezone(SEOUL).date()
    missing = (
        [d.isoformat() for d in scope.permitted_days if d < today and d.isoformat() not in complete]
        if scope
        else []
    )
    if not enabled["enabled"]:
        blockers.append("scheduler_disabled")
    if scope and today not in scope.permitted_days:
        blockers.append("today_not_permitted_collection_day")
    return FrozenJSON.freeze(
        dict(
            version="krx_scheduler_status_v1",
            enabled=enabled["enabled"],
            selected_trading_date=candidate.day.isoformat() if candidate else None,
            last_attempted_run=state["last_run"],
            last_successful_collection=(
                {n: state["successes"][-1][n] for n in ("day", "at", "observation_count", "origin")}
                if complete
                else None
            ),
            latest_persisted_trading_date=storage["latest_trading_date"] if storage else None,
            stored_observations=storage["observation_count"] if storage else None,
            free_disk_bytes=storage["disk_usage"]["free_disk_bytes"] if storage else None,
            local_request_budget=(
                {
                    n: assessment[n]
                    for n in (
                        "configured_daily_limit",
                        "locally_used_requests",
                        "remaining_local_allowance",
                        "paused",
                        "duplicate",
                    )
                }
                if assessment
                else None
            ),
            missing_runner_completion_dates=missing,
            blocked_dates=[
                x
                for x in missing
                if (today - date.fromisoformat(x)).days
                > (
                    policy["max_observation_lag_days"]
                    if policy
                    and type(policy.get("max_observation_lag_days")) is int
                    and 1 <= policy["max_observation_lag_days"] <= 7
                    else 7
                )
            ],
            pending_phase=state["pending"]["phase"] if state["pending"] else None,
            pending_trading_date=state["pending"]["plan"]["day"] if state["pending"] else None,
            pending_started_at=state["pending"]["started_at"] if state["pending"] else None,
            last_error_reason=(
                state["last_run"]["reason"]
                if state["last_run"] and state["last_run"]["outcome"] in ("failed", "blocked")
                else None
            ),
            blockers=blockers,
            network_requests=0,
        )
    )


def run_once(
    config: SchedulerConfig,
    *,
    execute: bool = False,
    scheduled: bool = False,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> FrozenJSON:
    if not execute:
        return inspect(config, clock())
    with _lock(config, ".run.lock"):
        state = _state(config)
        at = utc(clock())
        plan = None
        origin = None
        stage = "policy"
        blockers: list[str] = []

        def finish(outcome: str, reason: str, count: int = 0) -> FrozenJSON:
            result = dict(
                version="krx_scheduler_run_v1",
                at=utc(clock()).isoformat(),
                started_at=at.isoformat(),
                day=plan.day.isoformat() if plan else None,
                outcome=outcome,
                reason=reason,
                observation_count=count,
                origin=origin,
                blockers=blockers,
                pit_qualified=False,
                forward_signal_eligible=False,
                paper_execution_eligible=False,
                execution_authorized=False,
            )
            state["last_run"] = result
            _save(config, "state.json", state)
            return FrozenJSON.freeze(result)

        if not control(config)["enabled"]:
            return finish("blocked", "scheduler_disabled")
        pending = state["pending"]
        if pending and pending["phase"] == "capture_pending":
            plan = ForwardCapturePlan.from_dict(pending["plan"])
            return finish("blocked", "interrupted_capture_outcome_unknown")
        prior = None
        try:
            if pending:
                plan = ForwardCapturePlan.from_dict(pending["plan"])
            else:
                if scheduled and not host_seoul():
                    return finish("blocked", "host_timezone_not_asia_seoul")
                plan, policy = _plan(config, at)
                local = at.astimezone(SEOUL)
                d = config.document.to_dict()
                assert plan.full_market_scope is not None
                if local.date() not in plan.full_market_scope.permitted_days:
                    return finish("skipped", "today_not_permitted_collection_day")
                if scheduled and (local.hour, local.minute) < (d["hour"], d["minute"]):
                    return finish("skipped", "scheduled_time_not_due")
                prior = next(
                    (r for r in state["successes"] if r["day"] == plan.day.isoformat()), None
                )
                if prior:
                    # Revalidate/reimport through the same pinned offline path; no HTTP.
                    state["pending"] = dict(
                        phase="ingest_pending",
                        plan=plan.document.to_dict(),
                        capture_pin=prior["capture_pin"],
                        started_at=at.isoformat(),
                    )
                    _save(config, "state.json", state)
                else:
                    stage = "credentials"
                    key = _credential(config)
                    if not key:
                        return finish("blocked", "missing_credentials")
                    readiness = recurring_preview(
                        plan,
                        ATTEMPT,
                        config.reference,
                        None,
                        at,
                        bool(key),
                        credential=key,
                        capture_root=config.path("capture_root"),
                    ).to_dict()
                    if readiness["blockers"]:
                        blockers = readiness["blockers"]
                        return finish("blocked", "recurring_policy_blocked")
                    stage = "storage"
                    storage = _storage(config)
                    if not storage["disk_usage"]["free_space_requirement_met"]:
                        return finish("blocked", "storage_free_space_limit")
                    if client is None and policy["transport"] != "provider_https":
                        return finish("blocked", "mock_policy_test_only")
                    if not control(config)["enabled"]:
                        return finish("blocked", "scheduler_disabled")
                    stage = "capture"
                    state["pending"] = dict(
                        phase="capture_pending",
                        plan=plan.document.to_dict(),
                        capture_pin=None,
                        started_at=at.isoformat(),
                    )
                    _save(config, "state.json", state)

                    def capture(active: httpx.Client) -> FrozenJSON:
                        return run_capture(
                            config.path("capture_root"),
                            plan,
                            ATTEMPT,
                            active,
                            api_key=key,
                            allow_network=True,
                            authorization=config.reference,
                            authorization_policy=POLICY,
                            operator_confirmed=True,
                            clock=clock,
                            monotonic=monotonic,
                            sleep=sleep,
                            cancelled=lambda: not control(config)["enabled"],
                        )

                    if client is not None:
                        owner = capture(client)
                    else:
                        with capture_client() as active:
                            owner = capture(active)
                    state["pending"] = dict(
                        phase="ingest_pending",
                        plan=plan.document.to_dict(),
                        capture_pin=owner.identifier,
                        started_at=at.isoformat(),
                    )
                    _save(config, "state.json", state)
            assert plan is not None and state["pending"] is not None
            pin = state["pending"]["capture_pin"]
            stage = "ingestion"
            owner = verify_capture(config.path("capture_root"), plan, ATTEMPT, pin)
            if not any(r["day"] == plan.day.isoformat() for r in state["captures"]):
                state["captures"].append(
                    dict(
                        day=plan.day.isoformat(),
                        at=at.isoformat(),
                        capture_pin=pin,
                        plan_pin=plan.document.identifier,
                    )
                )
                _save(config, "state.json", state)
            responses = owner.to_dict()["responses"]
            if (
                len(responses) != 1
                or responses[0]["metadata"]["http_status"] != 200
                or not responses[0]["metadata"]["complete"]
            ):
                state["pending"] = None
                return finish("failed", "capture_http_or_transport_failure")
            if (
                responses[0]["parse_error"] is not None
                or responses[0]["metadata"]["error"] is not None
            ):
                state["pending"] = None
                return finish("failed", "invalid_market_response")
            result = ingest_capture(
                config.path("operational_root"),
                config.path("capture_root"),
                plan,
                plan.document.identifier,
                ATTEMPT,
                pin,
                authorization=config.reference,
                authorization_policy=POLICY,
            )
            origin = result.to_dict()["source_origin"]
            stage = "validation"
            with closing(OperationalMarketStore(config.path("operational_root"))) as store:
                require(store.validate().to_dict()["valid"], "operational_integrity_failure")
            count = result.to_dict()["observation_count"]
            if not any(r["day"] == plan.day.isoformat() for r in state["successes"]):
                state["successes"].append(
                    dict(
                        day=plan.day.isoformat(),
                        at=utc(clock()).isoformat(),
                        observation_count=count,
                        capture_pin=pin,
                        batch_pin=result.to_dict()["batch_pin"],
                        origin=origin,
                    )
                )
            require(len(state["successes"]) <= 366, "scheduler_completion_limit")
            state["pending"] = None
            return finish(
                "skipped" if prior else "succeeded",
                "already_collected"
                if prior
                else "offline_ingestion_resumed"
                if pending
                else "captured_and_ingested",
                count,
            )
        except (ValueError, OSError, sqlite3.Error, httpx.HTTPError, KeyError, TypeError) as error:
            if isinstance(error, ValueError) and str(error) == "no_eligible_completed_trading_date":
                return finish("skipped", "no_eligible_completed_trading_date")
            return finish(
                "failed",
                dict(
                    policy="policy_unavailable",
                    credentials="credentials_unavailable",
                    storage="operational_storage_unavailable",
                    capture="capture_outcome_uncertain",
                    ingestion="ingestion_pending",
                    validation="operational_integrity_failure",
                )[stage],
            )
