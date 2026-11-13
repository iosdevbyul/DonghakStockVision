"""Historical orchestration over the original KRX transport, receipts and market store.

The separate archive holds request intents/outcomes, not another market database.
Unknown request outcomes never authorize replay. No forward policy is rewritten.
"""

import hashlib
import os
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import CalendarMonthSource
from donghak_stock_vision.backtest.validation import fields, require, sha, utc
from donghak_stock_vision.data.krx_backfill import (
    PLAN,
    BackfillPlan,
    BackfillPolicy,
    BackfillRequestLimits,
)
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.base import ProviderError, RawPage, krx_market_scope
from donghak_stock_vision.providers.forward_capture import (
    CaptureHTTP,
    capture_client,
    verify_capture_client,
)
from donghak_stock_vision.providers.http import RequestGate
from donghak_stock_vision.providers.krx import KRXProvider
from donghak_stock_vision.storage.capture_raw_source_contract import prepare_raw_source_custody
from donghak_stock_vision.storage.forward_capture import DB as FORWARD_DB
from donghak_stock_vision.storage.krx_authorization import (
    OPERATOR_ACK,
    bounded,
    current_operation,
    review_time,
    verify_credential,
    verify_operator_documents,
)
from donghak_stock_vision.storage.krx_recurring import POLICY as FORWARD_POLICY
from donghak_stock_vision.storage.krx_scheduler import (
    SchedulerConfig,
    _credential,
    _lock,
    _policy,
    load_config,
)
from donghak_stock_vision.storage.operational_market import OperationalMarketStore, _parse
from donghak_stock_vision.storage.sqlite import SQLiteStore, archive_raw_page

DATE_FAILURES = {
    "empty_market_response",
    "invalid_market_response",
    "market_coverage_below_reviewed_bound",
}
DB = "backfill.sqlite3"
ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"


def _path(value: str) -> Path:
    require(type(value) is str, "absolute_backfill_path_required")
    p = Path(value)
    require(p.is_absolute() and p.resolve() == p, "canonical_backfill_path_required")
    return p


def _hash(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), "backfill_source_file_required")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def read_pin(path: Path) -> str:
    _private_file(path)
    return sha(bounded(path, 128).decode().strip())


def _private_file(path: Path) -> None:
    require(
        path.is_absolute()
        and path.resolve() == path
        and path.is_file()
        and not path.is_symlink()
        and path.stat().st_uid == os.getuid()
        and path.stat().st_mode & 0o077 == 0,
        "private_backfill_file_required",
    )


def load_policy(path: Path, pin: str) -> BackfillPolicy:
    _private_file(path)
    value = BackfillPolicy(FrozenJSON(bounded(path, 65536).decode()))
    require(value.document.identifier == sha(pin), "backfill_policy_pin_mismatch")
    return value


def load_plan(path: Path, pin: str) -> BackfillPlan:
    _private_file(path)
    value = BackfillPlan(FrozenJSON(bounded(path, 131072).decode()))
    require(value.document.identifier == sha(pin), "backfill_plan_pin_mismatch")
    return value


def write_new(path: Path, data: str) -> None:
    require(
        path.is_absolute() and path.parent.resolve() == path.parent, "canonical_output_required"
    )
    require(
        path.parent.is_dir() and path.parent.stat().st_mode & 0o077 == 0,
        "private_output_directory_required",
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(data + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def context(policy: BackfillPolicy, at: datetime) -> tuple[SchedulerConfig, dict[str, Any]]:
    d = policy.values
    review_time(policy.document, at)
    c = load_config(_path(d["scheduler_config"]), d["scheduler_config_pin"])
    original = _policy(c)
    require(
        original["version"] == FORWARD_POLICY
        and original["service_id"] == "stk_bydd_trd"
        and original["method"] == "GET"
        and original["endpoint"] == ENDPOINT
        and original["uses"] == {"private_capture": True, "forward_signal": False, "paper": False},
        "original_kospi_private_service_scope_required",
    )
    require(
        original["acknowledgement"] == OPERATOR_ACK
        and original["scope_acknowledgement"] == "explicit_full_market_recurring_read_only"
        and utc(original["reviewed_at"]) <= utc(d["reviewed_at"])
        and (original["transport"], original["document_origin"])
        in {
            ("provider_https", "operator_reviewed_source_copies"),
            ("provider_https", "operator_supplied_provider_originals"),
            ("mock_fixture", "synthetic_test_fixture"),
        },
        "original_service_review_or_origin_invalid",
    )
    verify_operator_documents(original, c.path("policy_root"))
    today = utc(at).astimezone(SEOUL).date()
    require(
        date.fromisoformat(original["approved_from"])
        <= today
        <= date.fromisoformat(original["approved_through"]),
        "backfill_service_approval_expired",
    )
    require(date.fromisoformat(d["end"]) < today, "backfill_requires_completed_date")
    require(
        d["reserved_daily_requests"] >= original["daily_limit"],
        "daily_scheduler_budget_not_reserved",
    )
    require(
        d["daily_limit"] + d["reserved_daily_requests"]
        <= min(9500, original["verified_service_daily_limit"]),
        "backfill_service_budget_exceeded",
    )
    archive = _path(d["archive_root"])
    pins = _path(d["source_pins_root"])
    require(
        pins.is_dir()
        and pins.stat().st_mode & 0o077 == 0
        and not pins.is_relative_to(archive)
        and not archive.is_relative_to(pins),
        "independent_private_source_pin_directory_required",
    )
    for other in (
        c.path("capture_root"),
        c.path("operational_root"),
        c.path("policy_root"),
        c.path("runner_root"),
        _path(d["research_database"]).parent,
    ):
        require(
            not archive.is_relative_to(other) and not other.is_relative_to(archive),
            "separate_backfill_archive_required",
        )
    for value in d["known_request_ledgers"]:
        _path(value)
    return c, original


def calendar_days(policy: BackfillPolicy) -> dict[str, str]:
    days: dict[str, str] = {}
    for value in policy.values["calendars"]:
        item = fields(value, "raw source normalized_pin")
        raw = bounded(_path(item["raw"]), 1024**2)
        source = FrozenJSON(bounded(_path(item["source"]), 32768).decode())
        month = CalendarMonthSource(raw, source, item["normalized_pin"]).verify().to_dict()
        require(not days.keys() & month["days"].keys(), "duplicate_backfill_calendar_month")
        days.update(month["days"])
    current = date.fromisoformat(policy.values["start"])
    end = date.fromisoformat(policy.values["end"])
    while current <= end:
        require(current.isoformat() in days, "backfill_calendar_coverage_missing")
        current += timedelta(days=1)
    return days


@contextmanager
def _read(path: Path) -> Iterator[sqlite3.Connection]:
    require(path.is_file() and not path.is_symlink(), "backfill_inventory_database_missing")
    require(
        not any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")),
        "backfill_inventory_requires_quiescent_database",
    )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db


def inventory(policy: BackfillPolicy, c: SchedulerConfig) -> dict[str, Any]:
    d = policy.values
    research = _path(d["research_database"])
    require(
        research.stat().st_size <= 2 * 1024**3 and _hash(research) == d["research_database_sha256"],
        "backfill_research_inventory_pin_mismatch",
    )
    with _read(research) as db:
        originals = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT source_date FROM raw_pages WHERE provider='krx' "
                "AND source_date BETWEEN ? AND ? ORDER BY source_date",
                (d["start"], d["end"]),
            )
        ]
        bars = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT trading_date FROM bars WHERE trading_date BETWEEN ? AND ? "
                "ORDER BY trading_date",
                (d["start"], d["end"]),
            )
        ]
    require(_hash(research) == d["research_database_sha256"], "backfill_inventory_changed")
    with closing(OperationalMarketStore(c.path("operational_root"))) as store:
        require(store.validate().to_dict()["valid"], "backfill_operational_integrity_failure")
        stored = [
            r[0]
            for r in store.connection.execute(
                "SELECT DISTINCT trading_date FROM market_observations WHERE market='KOSPI' "
                "AND trading_date BETWEEN ? AND ? ORDER BY trading_date",
                (d["start"], d["end"]),
            )
        ]
    # Existing forward originals, including failed/empty responses, are never requested again.
    forward: list[str] = []
    source = c.path("capture_root") / FORWARD_DB
    if source.exists():
        with _read(source) as db:
            forward = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT source_date FROM raw_pages WHERE provider='krx' "
                    "AND source_date BETWEEN ? AND ? ORDER BY source_date",
                    (d["start"], d["end"]),
                )
            ]
    others: set[str] = set()
    for name in d["known_request_ledgers"]:
        with _read(_path(name)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_pages'").fetchone():
                others.update(
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT source_date FROM raw_pages WHERE provider='krx' "
                        "AND source_date BETWEEN ? AND ?",
                        (d["start"], d["end"]),
                    )
                )
    return dict(
        research_originals=originals,
        research_bars=bars,
        operational=stored,
        forward=forward,
        other_originals=sorted(others),
    )


def prepare_plan(policy: BackfillPolicy, at: datetime) -> BackfillPlan:
    c, _ = context(policy, at)
    days = calendar_days(policy)
    existing = inventory(policy, c)
    return _prepared_plan(policy, days, existing)


def _prepared_plan(
    policy: BackfillPolicy,
    days: dict[str, str],
    existing: dict[str, Any],
) -> BackfillPlan:
    retained = set(
        existing["research_originals"] + existing["forward"] + existing["other_originals"]
    )
    bars = set(existing["research_bars"] + existing["operational"])
    d = policy.values
    current, end = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
    dates, skipped = [], []
    while current <= end:
        day = current.isoformat()
        if days[day] != "session":
            skipped.append(
                dict(
                    day=day,
                    reason="non_session_original_retained" if day in retained else "non_session",
                )
            )
        elif day in retained or day in bars:
            skipped.append(
                dict(
                    day=day,
                    reason="original_already_retained"
                    if day in retained
                    else "bars_already_stored",
                )
            )
        else:
            dates.append(day)
        current += timedelta(days=1)
    dates.reverse()
    skipped += [
        dict(day=day, reason="request_budget_not_selected") for day in dates[d["max_requests"] :]
    ]
    return BackfillPlan(
        FrozenJSON.freeze(
            dict(
                version=PLAN,
                policy_pin=policy.document.identifier,
                dates=dates[: d["max_requests"]],
                skipped=skipped,
                inventory_pin=FrozenJSON.freeze(existing).identifier,
            )
        )
    )


def _open_archive(policy: BackfillPolicy) -> SQLiteStore:
    root = _path(policy.values["archive_root"])
    require(not root.is_symlink(), "backfill_archive_symlink")
    if root.exists():
        require(
            root.is_dir()
            and root.stat().st_uid == os.getuid()
            and root.stat().st_mode & 0o077 == 0,
            "private_backfill_archive_required",
        )
    else:
        root.mkdir(mode=0o700)
    path = root / DB
    if path.exists():
        _private_file(path)
        with _read(path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require(
                {
                    "backfill_plans",
                    "backfill_intents",
                    "backfill_responses",
                    "backfill_completions",
                    "raw_pages",
                    "raw_capture_receipts",
                }
                <= tables,
                "unrelated_or_incomplete_backfill_archive_rejected",
            )
            triggers = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
            require(
                all(
                    f"{table}_no_{operation}" in triggers
                    for table in (
                        "backfill_plans",
                        "backfill_intents",
                        "backfill_responses",
                        "backfill_completions",
                    )
                    for operation in ("UPDATE", "DELETE")
                )
                and all(f"backfill_raw_no_{op}" in triggers for op in ("UPDATE", "DELETE")),
                "backfill_archive_immutability_schema_modified",
            )
    old = os.umask(0o077)
    try:
        store = SQLiteStore(path)
        for table, schema in (
            ("backfill_plans", "pin TEXT PRIMARY KEY,document TEXT NOT NULL"),
            ("backfill_intents", "day TEXT PRIMARY KEY,document TEXT NOT NULL"),
            (
                "backfill_responses",
                "day TEXT PRIMARY KEY,raw_id INTEGER NOT NULL,metadata TEXT NOT NULL",
            ),
            ("backfill_completions", "day TEXT PRIMARY KEY,document TEXT NOT NULL"),
        ):
            store.connection.execute(f"CREATE TABLE IF NOT EXISTS {table} ({schema})")
            for operation in ("UPDATE", "DELETE"):
                store.connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    "SELECT RAISE(ABORT,'immutable backfill evidence'); END"
                )
        for operation in ("UPDATE", "DELETE"):
            store.connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS backfill_raw_no_{operation} "
                f"BEFORE {operation} ON raw_pages BEGIN "
                "SELECT RAISE(ABORT,'immutable original'); END"
            )
        store.connection.commit()
        return store
    finally:
        os.umask(old)


def known_used(policy: BackfillPolicy, c: SchedulerConfig, at: datetime) -> int:
    paths = {_path(v) for v in policy.values["known_request_ledgers"]}
    paths |= {
        _path(policy.values["research_database"]),
        c.path("capture_root") / FORWARD_DB,
        _path(policy.values["archive_root"]) / DB,
    }
    total = 0
    for path in sorted(paths):
        if not path.exists():
            require(
                path not in {_path(v) for v in policy.values["known_request_ledgers"]},
                "declared_account_ledger_missing",
            )
            continue
        with _read(path) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='request_budget'").fetchone():
                row = db.execute(
                    "SELECT count FROM request_budget WHERE day=?",
                    (utc(at).astimezone(SEOUL).date().isoformat(),),
                ).fetchone()
                if row:
                    require(type(row[0]) is int and row[0] >= 0, "invalid_known_request_count")
                    total += row[0]
    return total


def _check_plan(policy: BackfillPolicy, plan: BackfillPlan) -> None:
    d, p = policy.values, plan.document.to_dict()
    require(p["policy_pin"] == policy.document.identifier, "backfill_plan_policy_mismatch")
    days = calendar_days(policy)
    require(len(p["dates"]) <= d["max_requests"], "backfill_plan_request_budget")
    require(
        all(d["start"] <= day <= d["end"] and days.get(day) == "session" for day in p["dates"]),
        "backfill_plan_calendar_or_date_mismatch",
    )


def _source(
    db: sqlite3.Connection,
    policy: BackfillPolicy,
    plan: BackfillPlan,
    day: str,
    *,
    sealed: bool = True,
) -> tuple[RawPage, dict[str, Any], FrozenJSON]:
    row = db.execute("SELECT document FROM backfill_intents WHERE day=?", (day,)).fetchone()
    require(row is not None, "original_backfill_intent_missing")
    intent = FrozenJSON(row[0])
    expected = dict(
        version="krx_backfill_request_v1",
        policy_pin=policy.document.identifier,
        plan_pin=plan.document.identifier,
        day=day,
    )
    require(intent.to_dict() == expected, "backfill_request_owner_mismatch")
    response = db.execute(
        "SELECT raw_id,metadata FROM backfill_responses WHERE day=?", (day,)
    ).fetchone()
    require(response is not None, "backfill_request_outcome_unknown_no_retry")
    raw_id, serialized = response
    metadata = fields(
        FrozenJSON(serialized).to_dict(),
        "request_index endpoint parameters request_started_at http_status headers complete error "
        "body_representation response_completed_at",
    )
    require(
        type(metadata["request_index"]) is int
        and type(metadata["complete"]) is bool
        and (
            metadata["http_status"] is None
            or (type(metadata["http_status"]) is int and 100 <= metadata["http_status"] <= 599)
        )
        and type(metadata["headers"]) is dict
        and metadata["body_representation"] in {"wire", "httpx_decoded"},
        "backfill_response_metadata_contract",
    )
    require(
        metadata["request_index"] == 0
        and metadata["endpoint"] == ENDPOINT
        and metadata["parameters"] == {"basDd": day.replace("-", "")},
        "backfill_response_identity_mismatch",
    )
    at = utc(metadata["response_completed_at"])
    require(utc(metadata["request_started_at"]) <= at, "backfill_response_clock_regression")
    context(policy, utc(metadata["request_started_at"]))
    raw = prepare_raw_source_custody(
        db, raw_id, archive_namespace="krx-backfill-" + policy.document.identifier[:32]
    )
    require(
        raw.run_id == intent.identifier + ":0"
        and raw.provider == "krx"
        and raw.scope == krx_market_scope("KOSPI")
        and raw.source_date == day
        and raw.collected_at == at.isoformat()
        and raw.byte_length <= 2 * 1024**2,
        "backfill_raw_source_ownership_mismatch",
    )
    owner = FrozenJSON.freeze(
        dict(
            version="krx_backfill_capture_v1",
            request=intent.to_dict(),
            metadata=metadata,
            source_identifier=raw.identifier,
            receipt=raw.receipt_payload(),
        )
    )
    if sealed:
        require(
            read_pin(_path(policy.values["source_pins_root"]) / f"source-{day}.pin")
            == owner.identifier,
            "backfill_capture_pin_mismatch",
        )
    return RawPage(raw.body, at, date.fromisoformat(day)), metadata, owner


def _reference(
    policy: BackfillPolicy, plan: BackfillPlan, owner: FrozenJSON, original: dict[str, Any]
) -> FrozenJSON:
    d = owner.to_dict()
    receipt, intent = d["receipt"], FrozenJSON.freeze(d["request"])
    return FrozenJSON.freeze(
        dict(
            version="operational_backfill_reference_v1",
            capture_id=intent.identifier,
            capture_pin=owner.identifier,
            plan_pin=plan.document.identifier,
            authorization_pin=policy.document.identifier,
            trust_pin=None,
            sequence=0,
            raw_id=receipt["rawId"],
            receipt_pin=FrozenJSON.freeze(receipt).identifier,
            raw_sha256=receipt["rawSha256"],
            market="KOSPI",
            day=intent.to_dict()["day"],
            response_at=receipt["collectedAt"],
            origin=(
                "real_provider_https_local_capture"
                if original["transport"] == "provider_https"
                else "authorized_provider_mock_fixture"
            ),
        )
    )


def _stored(c: SchedulerConfig, reference: FrozenJSON) -> FrozenJSON:
    with closing(OperationalMarketStore(c.path("operational_root"))) as store:
        row = store.connection.execute(
            "SELECT raw_id FROM ingestion_batches WHERE batch_id=?", (reference.identifier,)
        ).fetchone()
        require(row is not None, "backfill_committed_batch_missing")
        batch = store._verify_batch(reference.identifier, row[0])
        require(
            batch.to_dict()["request"]["source_capture"] == reference.to_dict(),
            "backfill_stored_source_mismatch",
        )
        require(store.validate().to_dict()["valid"], "backfill_stored_integrity_failure")
        return batch


def _complete(
    db: sqlite3.Connection,
    policy: BackfillPolicy,
    plan: BackfillPlan,
    c: SchedulerConfig,
    original: dict[str, Any],
    day: str,
    *,
    ingest: bool,
) -> FrozenJSON:
    page, metadata, owner = _source(db, policy, plan, day)
    count, batch_pin = 0, None
    outcome = "failed"
    reason = "http_or_transport_failure"
    if metadata["complete"] and metadata["http_status"] == 200 and metadata["error"] is None:
        try:
            bars, _, _ = _parse(
                page,
                "KOSPI",
                metadata["headers"].get("content-encoding", ""),
                metadata["body_representation"],
                10000,
            )
        except (ValueError, ProviderError, UnicodeError):
            reason = "invalid_market_response"
        else:
            if not bars:
                outcome, reason = "no_data", "empty_market_response"
            else:
                if len(bars) < policy.values["min_rows"] or not set(
                    policy.values["required_tickers"]
                ) <= {b.ticker for b in bars}:
                    return FrozenJSON.freeze(
                        dict(
                            version="krx_backfill_completion_v1",
                            day=day,
                            outcome="failed",
                            reason="market_coverage_below_reviewed_bound",
                            http_status=metadata["http_status"],
                            observation_count=0,
                            capture_pin=owner.identifier,
                            batch_pin=None,
                            raw_bytes_verified=True,
                            pit_qualified=False,
                            performance_approved=False,
                            performance_metrics=None,
                            forward_signal_eligible=False,
                            execution_authorized=False,
                        )
                    )
                reference = _reference(policy, plan, owner, original)
                if ingest:
                    with closing(
                        OperationalMarketStore(c.path("operational_root"), writable=True)
                    ) as store:
                        store.ingest(
                            reference.identifier,
                            "KOSPI",
                            page,
                            source_kind="local_response_unqualified",
                            content_encoding=metadata["headers"].get("content-encoding", ""),
                            representation=metadata["body_representation"],
                            source_capture=reference,
                        )
                batch = _stored(c, reference)
                count, batch_pin = batch.to_dict()["observation_count"], batch.identifier
                require(count == len(bars), "backfill_stored_count_mismatch")
                outcome, reason = "succeeded", "captured_stored_and_read_back"
    return FrozenJSON.freeze(
        dict(
            version="krx_backfill_completion_v1",
            day=day,
            outcome=outcome,
            reason=reason,
            http_status=metadata["http_status"],
            observation_count=count,
            capture_pin=owner.identifier,
            batch_pin=batch_pin,
            raw_bytes_verified=True,
            pit_qualified=False,
            performance_approved=False,
            performance_metrics=None,
            forward_signal_eligible=False,
            execution_authorized=False,
        )
    )


def inspect_backfill(policy: BackfillPolicy, plan: BackfillPlan, at: datetime) -> FrozenJSON:
    c, original = context(policy, utc(policy.values["reviewed_at"]))
    _check_plan(policy, plan)
    path = _path(policy.values["archive_root"]) / DB
    completed: list[dict[str, Any]] = []
    pending: list[str] = []
    if path.exists():
        with _read(path) as db:
            for day in plan.document.to_dict()["dates"]:
                row = db.execute(
                    "SELECT document FROM backfill_completions WHERE day=?", (day,)
                ).fetchone()
                if row:
                    result = _complete(db, policy, plan, c, original, day, ingest=False)
                    require(result.payload_json == row[0], "backfill_completion_modified")
                    completed.append(
                        {
                            k: v
                            for k, v in result.to_dict().items()
                            if k not in ("capture_pin", "batch_pin")
                        }
                    )
                elif db.execute("SELECT 1 FROM backfill_intents WHERE day=?", (day,)).fetchone():
                    pending.append(day)
    return FrozenJSON.freeze(
        dict(
            version="krx_backfill_status_v1",
            results=completed,
            pending_dates=pending,
            planned_dates=plan.document.to_dict()["dates"],
            skipped=plan.document.to_dict()["skipped"],
            known_local_requests_used=known_used(policy, c, at),
            account_wide_remaining_quota="UNKNOWN",
            independent_publication_time_verified=False,
            network_requests=0,
            performance_metrics=None,
            pit_qualified=False,
            execution_authorized=False,
        )
    )


def _scheduled_window(c: SchedulerConfig, at: datetime) -> None:
    local = utc(at).astimezone(SEOUL)
    d = c.document.to_dict()
    distance = abs(local.hour * 60 + local.minute - (d["hour"] * 60 + d["minute"]))
    require(local.weekday() >= 5 or distance > 30, "backfill_paused_for_daily_scheduler_window")


def run_backfill(
    policy: BackfillPolicy,
    plan: BackfillPlan,
    *,
    execute: bool = False,
    allow_network: bool = False,
    operator_confirmed: bool = False,
    allow_synthetic_fixture: bool = False,
    continue_date_failures: bool = False,
    max_dates: int = 2,
    max_seconds: int = 120,
    client: httpx.Client | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> FrozenJSON:
    require(
        type(max_dates) is int
        and 1 <= max_dates <= 366
        and type(max_seconds) is int
        and 1 <= max_seconds <= 300,
        "bounded_backfill_batch_required",
    )
    if not execute:
        require(
            not allow_network and not operator_confirmed,
            "backfill_preview_cannot_authorize_network",
        )
        return inspect_backfill(policy, plan, clock())
    c, original = context(policy, clock())
    _check_plan(policy, plan)
    if original["transport"] == "mock_fixture":
        require(allow_synthetic_fixture, "explicit_backfill_fixture_required")
    else:
        require(not allow_synthetic_fixture, "backfill_real_origin_cannot_be_fixture")
        current_operation("real_provider_https_local_capture", clock())
        clock, monotonic, sleep = lambda: datetime.now(UTC), time.monotonic, time.sleep
    _scheduled_window(c, clock())
    # Same authoritative lock as run-due/run-once, without changing enablement or configuration.
    with _lock(c, ".run.lock"):
        fresh_inventory = inventory(policy, c)
        research_file = _path(policy.values["research_database"])
        research_signature = _signature(research_file)

        with closing(_open_archive(policy)) as store:
            db = store.connection
            row = db.execute(
                "SELECT document FROM backfill_plans WHERE pin=?", (plan.document.identifier,)
            ).fetchone()
            if row:
                require(row[0] == plan.document.payload_json, "retained_backfill_plan_modified")
            else:
                require(
                    _prepared_plan(policy, calendar_days(policy), fresh_inventory) == plan,
                    "backfill_plan_inventory_changed",
                )
                with db:
                    db.execute(
                        "INSERT INTO backfill_plans VALUES (?,?)",
                        (plan.document.identifier, plan.document.payload_json),
                    )
            before_outcomes = db.execute("SELECT COUNT(*) FROM backfill_responses").fetchone()[0]
            start = monotonic()
            reserved, reason = 0, "batch_limit_reached"
            results: list[dict[str, Any]] = []
            with closing(OperationalMarketStore(c.path("operational_root"))) as market:
                before_count = market.status().to_dict()["observation_count"]
            for day in plan.document.to_dict()["dates"]:
                if len(results) >= max_dates:
                    break
                if monotonic() - start >= max_seconds:
                    reason = "cooperative_batch_time_limit"
                    break
                previous = db.execute(
                    "SELECT document FROM backfill_completions WHERE day=?", (day,)
                ).fetchone()
                if previous:
                    verified = _complete(db, policy, plan, c, original, day, ingest=False)
                    require(verified.payload_json == previous[0], "backfill_completion_modified")
                    if verified.to_dict()["outcome"] != "succeeded":
                        if continue_date_failures and verified.to_dict()["reason"] in DATE_FAILURES:
                            continue
                        reason = "backfill_previous_failure_requires_review_no_retry"
                        break
                    continue
                intent = db.execute(
                    "SELECT document FROM backfill_intents WHERE day=?", (day,)
                ).fetchone()
                if not intent:
                    existing = set().union(*(set(v) for v in fresh_inventory.values()))
                    if day in existing:
                        reason = "backfill_original_or_data_already_present"
                        continue
                    if not (allow_network and operator_confirmed):
                        reason = "explicit_backfill_network_authorization_required"
                        break
                    context(policy, clock())
                    require(
                        _signature(research_file) == research_signature,
                        "backfill_research_inventory_changed_during_run",
                    )
                    _scheduled_window(c, clock())
                    require(
                        known_used(policy, c, clock()) + 1 <= policy.values["daily_limit"],
                        "backfill_known_account_budget_exhausted_daily_reserve_preserved",
                    )
                    count = db.execute(
                        "SELECT COUNT(*) FROM backfill_intents WHERE "
                        "json_extract(document,'$.policy_pin')=?",
                        (policy.document.identifier,),
                    ).fetchone()[0]
                    require(
                        count < policy.values["max_requests"],
                        "backfill_policy_total_budget_exhausted",
                    )
                    archive_file = _path(policy.values["archive_root"]) / DB
                    require(
                        archive_file.stat().st_size + 6 * 1024**2 <= 2 * 1024**3
                        and shutil.disk_usage(archive_file.parent).free >= 256 * 1024**2,
                        "backfill_archive_capacity_exhausted",
                    )
                    key = _credential(c)
                    verify_credential(FrozenJSON.freeze(original), key)
                    require(
                        len(key) <= 1024 and key.isascii() and key.isprintable(),
                        "invalid_backfill_credential",
                    )
                    limits = BackfillRequestLimits(
                        interval_seconds=max(1, original["interval_seconds"])
                    )
                    with capture_client() if client is None else nullcontext(client) as active:
                        verify_capture_client(active, ENDPOINT, original["transport"])
                        request = FrozenJSON.freeze(
                            dict(
                                version="krx_backfill_request_v1",
                                policy_pin=policy.document.identifier,
                                plan_pin=plan.document.identifier,
                                day=day,
                            )
                        )
                        with db:
                            db.execute(
                                "INSERT INTO backfill_intents VALUES (?,?)",
                                (day, request.payload_json),
                            )
                        reserved += 1

                        def record(
                            metadata: dict[str, Any],
                            body: bytes,
                            day: str = day,
                            request: FrozenJSON = request,
                        ) -> None:
                            page = RawPage(
                                body,
                                utc(metadata["response_completed_at"]),
                                date.fromisoformat(day),
                            )
                            with db:
                                raw_id = archive_raw_page(
                                    db,
                                    request.identifier + ":0",
                                    "krx",
                                    krx_market_scope("KOSPI"),
                                    page,
                                )
                                db.execute(
                                    "INSERT INTO backfill_responses VALUES (?,?,?)",
                                    (day, raw_id, FrozenJSON.freeze(metadata).payload_json),
                                )
                            _, _, owner = _source(db, policy, plan, day, sealed=False)
                            write_new(
                                _path(policy.values["source_pins_root"]) / f"source-{day}.pin",
                                owner.identifier,
                            )

                        gate = RequestGate(
                            _path(policy.values["archive_root"]) / DB,
                            limits.interval_seconds,
                            policy.values["daily_limit"],
                            clock=lambda: clock().timestamp(),
                            sleep=sleep,
                        )

                        def authorize() -> None:
                            try:
                                context(policy, clock())
                            except ValueError:
                                raise ProviderError(
                                    "backfill_authorization_changed_or_expired"
                                ) from None

                        http = CaptureHTTP(
                            active,
                            gate,
                            limits,
                            record,
                            clock=clock,
                            monotonic=monotonic,
                            sleep=sleep,
                            authorization_check=authorize,
                        )
                        provider = KRXProvider(key, http, "KOSPI")
                        try:
                            provider.fetch_market(date.fromisoformat(day))
                        except ProviderError:
                            # The recorder retains failed HTTP/transport bytes; never retry here.
                            pass
                result = _complete(db, policy, plan, c, original, day, ingest=True)
                with db:
                    db.execute(
                        "INSERT INTO backfill_completions VALUES (?,?)", (day, result.payload_json)
                    )
                safe = {
                    k: v
                    for k, v in result.to_dict().items()
                    if k not in ("capture_pin", "batch_pin")
                }
                results.append(safe)
                if result.to_dict()["outcome"] != "succeeded":
                    reason = result.to_dict()["reason"]
                    if continue_date_failures and reason in DATE_FAILURES:
                        continue
                    break
            require(
                _hash(research_file) == policy.values["research_database_sha256"],
                "backfill_research_inventory_changed_during_run",
            )
            with closing(OperationalMarketStore(c.path("operational_root"))) as market:
                after_count = market.status().to_dict()["observation_count"]
            return FrozenJSON.freeze(
                dict(
                    version="krx_backfill_run_v1",
                    results=results,
                    requests_reserved=reserved,
                    http_requests=db.execute("SELECT COUNT(*) FROM backfill_responses").fetchone()[
                        0
                    ]
                    - before_outcomes,
                    new_observations=after_count - before_count,
                    successful_dates=sum(r["outcome"] == "succeeded" for r in results),
                    stop_reason=reason,
                    account_wide_remaining_quota="UNKNOWN",
                    independent_publication_time_verified=False,
                    pit_qualified=False,
                    forward_signal_eligible=False,
                    execution_authorized=False,
                    performance_approved=False,
                    performance_metrics=None,
                )
            )
