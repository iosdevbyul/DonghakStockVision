"""Local forward-capture journal over the existing raw archive and ingestion path.

Only a provisioned pilot DB is writable. Complete immutable manifests are the
publication boundary; the journal deliberately exposes interrupted stages.
"""

import fcntl
import hashlib
import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import (
    CalendarMonthSource,
    HistoricalReadScope,
)
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.ingestion.market import KRXMarketPipeline
from donghak_stock_vision.providers.base import ProviderError, RawPage, krx_market_scope
from donghak_stock_vision.providers.forward_capture import CaptureHTTP
from donghak_stock_vision.providers.http import RequestGate
from donghak_stock_vision.providers.krx import ENDPOINTS, KRXProvider
from donghak_stock_vision.storage.capture_evidence_sink import _receipt_bytes
from donghak_stock_vision.storage.sqlite import SQLiteStore

if TYPE_CHECKING:
    from donghak_stock_vision.storage.historical_pit import HistoricalPITAssessment


logger = logging.getLogger(__name__)
DB = "pilot.sqlite3"
SCHEMA = """
CREATE TABLE forward_intents (capture_id TEXT PRIMARY KEY,
 plan TEXT NOT NULL, attempt TEXT NOT NULL);
CREATE TABLE forward_responses (capture_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 raw_id INTEGER NOT NULL UNIQUE, metadata TEXT NOT NULL, PRIMARY KEY(capture_id,sequence));
CREATE TABLE forward_manifests (capture_id TEXT PRIMARY KEY,
 document TEXT NOT NULL, pin TEXT NOT NULL);
CREATE TABLE forward_stops (capture_id TEXT PRIMARY KEY, reason TEXT NOT NULL);
"""


def identity(plan: ForwardCapturePlan, attempt: str) -> str:
    plan.__post_init__()
    require(
        type(attempt) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", attempt) is not None,
        "invalid_attempt_identity",
    )
    return digest(
        {"version": "forward_capture_v1", "plan": plan.document.identifier, "attempt": attempt}
    )


def _connection(root: Path) -> sqlite3.Connection:
    path = root / DB
    require(
        path.is_file() and not path.is_symlink() and not root.is_symlink(), "pilot_archive_missing"
    )
    require(path.stat().st_size <= 256 * 1024 * 1024, "pilot_archive_byte_budget")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


@contextmanager
def _writer(root: Path) -> Iterator[SQLiteStore]:
    require(not root.is_symlink(), "pilot_symlink_forbidden")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".capture.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("capture_already_running") from error
        path = root / DB
        if path.exists():
            with closing(_connection(root)) as connection:
                require(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='forward_intents'"
                    ).fetchone()
                    is not None,
                    "not_a_pilot_archive",
                )
        store = SQLiteStore(path)
        path.chmod(0o600)
        try:
            if (
                store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='forward_intents'"
                ).fetchone()
                is None
            ):
                store.connection.executescript(SCHEMA)
                for table in (
                    "forward_intents",
                    "forward_responses",
                    "forward_manifests",
                    "forward_stops",
                ):
                    for operation in ("UPDATE", "DELETE"):
                        store.connection.execute(
                            f"CREATE TRIGGER {table}_no_{operation} BEFORE {operation} ON {table} "
                            "BEGIN SELECT RAISE(ABORT, 'forward evidence immutable'); END"
                        )
                store.connection.commit()
            yield store
        finally:
            store.close()
            fcntl.flock(lock, fcntl.LOCK_UN)


def _endpoint(plan: ForwardCapturePlan) -> str:
    return f"https://data-dbg.krx.co.kr/svc/apis/sto/{ENDPOINTS[plan.market]}"


class _RecordedProvider(KRXProvider):
    def __init__(self, plan: ForwardCapturePlan, page: RawPage) -> None:
        # Only parsing/replaying an already retained response: no credential or HTTP object.
        self.market, self.page = plan.market, page

    def fetch_market(self, day: date) -> RawPage:
        require(day == self.page.source_date, "recorded_date_mismatch")
        return self.page


class _ArchiveReuse(SQLiteStore):
    def __init__(self, store: SQLiteStore, capture_id: str, raw_id: int, page: RawPage) -> None:
        self.connection = store.connection
        self.capture_id, self.raw_id, self.page = capture_id, raw_id, page

    def archive(self, run_id: str, provider: str, ticker: str, page: RawPage) -> int:
        require(provider == "krx" and page == self.page, "ingestion_source_mismatch")
        return self.raw_id

    def record_run(
        self,
        run_id: str,
        ticker: str,
        start: date,
        end: date,
        status: str,
        rows: int,
        error: str | None,
    ) -> None:
        # Original collector receives a random internal ID; persist the explicit capture identity.
        super().record_run(self.capture_id, ticker, start, end, status, rows, error)


def _project(
    connection: sqlite3.Connection, plan: ForwardCapturePlan, capture_id: str
) -> FrozenJSON:
    intent = connection.execute(
        "SELECT plan,attempt FROM forward_intents WHERE capture_id=?", (capture_id,)
    ).fetchone()
    require(
        intent is not None
        and intent[0] == plan.document.payload_json
        and identity(plan, intent[1]) == capture_id,
        "capture_intent_mismatch",
    )
    rows = connection.execute(
        "SELECT sequence,raw_id,metadata FROM forward_responses "
        "WHERE capture_id=? ORDER BY sequence",
        (capture_id,),
    ).fetchall()
    require(0 <= len(rows) <= plan.max_requests, "response_count_budget")
    attempts = []
    for expected, (sequence, raw_id, serialized) in enumerate(rows):
        require(sequence == expected, "response_sequence_mismatch")
        metadata = FrozenJSON(serialized).to_dict()
        require(
            metadata["request_index"] == sequence
            and metadata["endpoint"] == _endpoint(plan)
            and metadata["parameters"] == {"basDd": plan.day.strftime("%Y%m%d")},
            "request_identity_mismatch",
        )
        receipt = json.loads(_receipt_bytes(connection, raw_id))
        require(
            receipt["runId"] == f"{capture_id}:{sequence}"
            and receipt["provider"] == "krx"
            and receipt["scope"] == krx_market_scope(plan.market)
            and receipt["sourceDate"] == plan.day.isoformat()
            and receipt["collectedAt"] == metadata["response_completed_at"],
            "capture_lineage_mismatch",
        )
        start = datetime.fromisoformat(metadata["request_started_at"])
        end = datetime.fromisoformat(metadata["response_completed_at"])
        require(
            start.utcoffset() is not None and end.utcoffset() is not None, "timestamp_unavailable"
        )
        require(
            end >= start or metadata["error"] == "capture_clock_regression", "timestamp_conflict"
        )
        body = connection.execute("SELECT body FROM raw_pages WHERE id=?", (raw_id,)).fetchone()[0]
        require(len(body) <= plan.max_response_bytes, "response_byte_budget")
        parsed = None
        parse_error = None
        if metadata["complete"] and metadata["http_status"] == 200 and metadata["error"] is None:
            page = RawPage(body, end, plan.day)
            try:
                values = _RecordedProvider(plan, page).parse_market(page)
                selected = [b.to_dict() for b in values if b.ticker == plan.ticker]
                require(len(selected) <= 1, "duplicate_scoped_rows")
                require(bool(selected), "scoped_instrument_missing")
                parsed = selected
            except (ProviderError, ValueError):
                parse_error = "invalid_or_missing_scoped_bar"
        attempts.append(
            {
                "metadata": metadata,
                "receipt": receipt,
                "raw_id": raw_id,
                "body_bytes": len(body),
                "parsed_bars": parsed,
                "parse_error": parse_error,
            }
        )
    # Archive/response insertion interruptions must never look complete.
    raw_count = connection.execute(
        "SELECT COUNT(*) FROM raw_pages WHERE run_id LIKE ?", (capture_id + ":%",)
    ).fetchone()[0]
    require(raw_count == len(attempts), "interrupted_response_commit")
    ingestion = connection.execute(
        "SELECT status,rows,error FROM collection_runs WHERE run_id=? ORDER BY id", (capture_id,)
    ).fetchall()
    stop = connection.execute(
        "SELECT reason FROM forward_stops WHERE capture_id=?", (capture_id,)
    ).fetchone()
    previous = []
    for previous_id, serialized_plan, previous_pin in connection.execute(
        "SELECT i.capture_id,i.plan,m.pin FROM forward_intents i "
        "JOIN forward_manifests m USING(capture_id) WHERE i.rowid < "
        "(SELECT rowid FROM forward_intents WHERE capture_id=?) ORDER BY i.rowid LIMIT 32",
        (capture_id,),
    ):
        prior = FrozenJSON(serialized_plan).to_dict()
        if all(prior[k] == plan.document.to_dict()[k] for k in ("market", "ticker", "day")):
            hashes = [
                json.loads(_receipt_bytes(connection, r[0]))["rawSha256"]
                for r in connection.execute(
                    "SELECT raw_id FROM forward_responses WHERE capture_id=? ORDER BY sequence",
                    (previous_id,),
                )
            ]
            previous.append({"capture_id": previous_id, "pin": previous_pin, "raw_hashes": hashes})
    return FrozenJSON.freeze(
        {
            "version": "forward_capture_manifest_v1",
            "previous_captures": previous,
            "stop_reason": stop[0] if stop else None,
            "capture_id": capture_id,
            "plan": plan.document.to_dict(),
            "attempt": intent[1],
            "responses": attempts,
            "ingestion": [list(r) for r in ingestion],
            "software": "donghak-stock-vision-2.0.0a3",
            "parser": "KRXProvider-daily-unadjusted-v1",
            "clock_source": "local_system_utc_unverified",
            "origin": plan.mode,
            "publication_at": None,
            "authenticated_at": None,
            "decision_safe_at": None,
            "pit_qualified": False,
            "execution_authorized": False,
        }
    )


def verify_capture(
    root: Path, plan: ForwardCapturePlan, attempt: str, trusted_pin: str
) -> FrozenJSON:
    capture_id = identity(plan, attempt)  # Authorization checked before source I/O.
    sha(trusted_pin)
    with closing(_connection(root)) as connection:
        row = connection.execute(
            "SELECT document,pin FROM forward_manifests WHERE capture_id=?", (capture_id,)
        ).fetchone()
        require(row is not None, "capture_incomplete_or_missing")
        owner = FrozenJSON(row[0])
        require(owner.identifier == row[1] == trusted_pin, "capture_pin_mismatch")
        require(_project(connection, plan, capture_id) == owner, "capture_semantic_mismatch")
        doc = owner.to_dict()
        successes = [r for r in doc["responses"] if r["parsed_bars"] is not None]
        require(len(doc["ingestion"]) == (1 if successes else 0), "ingestion_lineage_mismatch")
        if successes:
            outcome = doc["ingestion"][0]
            if outcome[0] == "failed":
                require(
                    outcome[1] == 0
                    and outcome[2]
                    in (
                        "ProviderError",
                        "ValueError",
                        "TypeError",
                        "KeyError",
                        "IntegrityError",
                        "OperationalError",
                    ),
                    "invalid_ingestion_failure",
                )
                return owner
            require(outcome == ["success", 1, None], "ingestion_failed")
            r = successes[-1]
            # Unchanged repeated bars legitimately retain the original ingestion timestamp.
            bar = r["parsed_bars"][0]
            current = connection.execute(
                "SELECT payload FROM bars WHERE ticker=? AND trading_date=?",
                (plan.ticker, plan.day.isoformat()),
            ).fetchone()
            require(current is not None, "ingested_bar_missing")
            from donghak_stock_vision.data.schema import DailyBar

            retained = DailyBar.from_dict(json.loads(current[0]))
            require(
                retained.provider == "krx" and retained.market == plan.market,
                "ingested_binding_mismatch",
            )
            # Reconstruct the mutable cleaned projection from original linked source owners.
            sources = connection.execute(
                "SELECT r.body,r.collected_at,r.source_date FROM raw_pages r JOIN bar_sources s "
                "ON r.id=s.raw_id WHERE s.ticker=? AND s.trading_date=? "
                "ORDER BY r.collected_at,r.id",
                (plan.ticker, plan.day.isoformat()),
            ).fetchall()
            require(0 < len(sources) <= 96, "ingestion_source_budget")
            latest = None
            for body, observed, source_day in sources:
                source_page = RawPage(
                    body, datetime.fromisoformat(observed), date.fromisoformat(source_day)
                )
                values = _RecordedProvider(plan, source_page).parse_market(source_page)
                selected = [b for b in values if b.ticker == plan.ticker]
                require(len(selected) == 1, "ingestion_source_binding")
                latest = selected[0]
            require(latest == retained, "ingested_economics_mismatch")
            require(
                connection.execute(
                    "SELECT 1 FROM bar_sources WHERE ticker=? AND trading_date=?",
                    (bar["ticker"], bar["trading_date"]),
                ).fetchone()
                is not None,
                "ingestion_provenance_missing",
            )
        return owner


def run_capture(
    root: Path,
    plan: ForwardCapturePlan,
    attempt: str,
    client: httpx.Client,
    *,
    trusted_resume_pin: str | None = None,
    api_key: str = "",
    allow_network: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> FrozenJSON:
    capture_id = identity(plan, attempt)
    observed = clock()
    require(observed.utcoffset() is not None, "naive_capture_clock")
    require(plan.day < observed.astimezone(SEOUL).date(), "final_daily_bar_unavailable")
    blockers = plan.blockers(bool(api_key.strip()))
    if plan.mode == "authorized_provider" and not allow_network:
        blockers.append("explicit_network_permission_required")
    if plan.mode == "offline_fixture":
        require(
            isinstance(client._transport_for_url(httpx.URL(_endpoint(plan))), httpx.MockTransport),
            "offline_transport_required",
        )
    require(not blockers, ",".join(blockers))
    with _writer(root) as store:
        connection = store.connection
        origins = connection.execute("SELECT plan FROM forward_intents LIMIT 33").fetchall()
        require(len(origins) <= 32, "pilot_capture_budget")
        require(
            all(FrozenJSON(row[0]).to_dict()["mode"] == plan.mode for row in origins),
            "mixed_capture_origins_forbidden",
        )
        existing = connection.execute(
            "SELECT pin FROM forward_manifests WHERE capture_id=?", (capture_id,)
        ).fetchone()
        if existing:
            require(trusted_resume_pin == existing[0], "trusted_resume_pin_required")
            return verify_capture(root, plan, attempt, existing[0])
        intent = connection.execute(
            "SELECT plan FROM forward_intents WHERE capture_id=?", (capture_id,)
        ).fetchone()
        if intent:
            require(intent[0] == plan.document.payload_json, "capture_plan_mismatch")
            # Resume retained responses offline, without a second HTTP request.
            pending = _project(connection, plan, capture_id)
            require(
                bool(pending.to_dict()["responses"]),
                "interrupted_request_outcome_unknown_new_attempt_required",
            )
        else:
            require(
                connection.execute("SELECT COUNT(*) FROM forward_intents").fetchone()[0] < 32,
                "pilot_capture_budget",
            )
            with connection:
                connection.execute(
                    "INSERT INTO forward_intents VALUES(?,?,?)",
                    (capture_id, plan.document.payload_json, attempt),
                )

            def record(metadata: dict[str, Any], body: bytes) -> None:
                index = metadata["request_index"]
                raw_id = store.archive(
                    f"{capture_id}:{index}",
                    "krx",
                    krx_market_scope(plan.market),
                    RawPage(
                        body, datetime.fromisoformat(metadata["response_completed_at"]), plan.day
                    ),
                )
                with connection:
                    connection.execute(
                        "INSERT INTO forward_responses VALUES(?,?,?,?)",
                        (capture_id, index, raw_id, FrozenJSON.freeze(metadata).payload_json),
                    )
                logger.info(
                    "forward_capture_response capture=%s sequence=%d bytes=%d",
                    capture_id,
                    index,
                    len(body),
                )

            gate = RequestGate(
                root / DB,
                plan.interval_seconds,
                plan.daily_limit,
                clock=lambda: clock().timestamp(),
                sleep=sleep,
            )
            http = CaptureHTTP(
                client,
                gate,
                plan,
                record,
                cancelled=cancelled,
                clock=clock,
                monotonic=monotonic,
                sleep=sleep,
            )
            provider = KRXProvider(api_key or "offline-fixture-not-a-key", http, plan.market)
            try:
                provider.fetch_market(plan.day)  # Original endpoint/request construction.
            except ProviderError as error:
                with connection:
                    connection.execute(
                        "INSERT INTO forward_stops VALUES(?,?)", (capture_id, str(error))
                    )
                logger.warning("forward_capture_stopped capture=%s reason=%s", capture_id, error)
        projected = _project(connection, plan, capture_id).to_dict()
        successes = [r for r in projected["responses"] if r["parsed_bars"] is not None]
        if successes and not projected["ingestion"]:
            source = successes[-1]
            raw_id = source["raw_id"]
            body = connection.execute(
                "SELECT body FROM raw_pages WHERE id=?", (raw_id,)
            ).fetchone()[0]
            page = RawPage(
                body, datetime.fromisoformat(source["metadata"]["response_completed_at"]), plan.day
            )
            bridge = _ArchiveReuse(store, capture_id, raw_id, page)
            KRXMarketPipeline(_RecordedProvider(plan, page), bridge).collect(
                plan.day, plan.day, tickers=[plan.ticker]
            )
        owner = _project(connection, plan, capture_id)
        with connection:
            connection.execute(
                "INSERT INTO forward_manifests VALUES(?,?,?)",
                (capture_id, owner.payload_json, owner.identifier),
            )
        logger.info("forward_capture_committed capture=%s pin=%s", capture_id, owner.identifier)
        return verify_capture(root, plan, attempt, owner.identifier)


def capture_status(root: Path) -> FrozenJSON:
    with closing(_connection(root)) as connection:
        rows = connection.execute(
            "SELECT i.capture_id,i.attempt,m.pin FROM forward_intents i "
            "LEFT JOIN forward_manifests m USING(capture_id) ORDER BY i.rowid LIMIT 33"
        ).fetchall()
        require(len(rows) <= 32, "pilot_capture_budget")
        return FrozenJSON.freeze(
            {
                "version": "forward_capture_status_v1",
                "captures": [
                    {
                        "capture_id": i,
                        "attempt": a,
                        "pin": p,
                        "status": "committed_unverified" if p else "interrupted",
                    }
                    for i, a, p in rows
                ],
            }
        )


def readback(root: Path, plan: ForwardCapturePlan, attempt: str, pin: str, sequence: int) -> bytes:
    owner = verify_capture(root, plan, attempt, pin)
    responses = owner.to_dict()["responses"]
    require(type(sequence) is int and 0 <= sequence < len(responses), "unknown_response")
    with closing(_connection(root)) as connection:
        body: bytes = connection.execute(
            "SELECT body FROM raw_pages WHERE id=?", (responses[sequence]["raw_id"],)
        ).fetchone()[0]
        require(
            hashlib.sha256(body).hexdigest() == responses[sequence]["receipt"]["rawSha256"],
            "readback_hash_mismatch",
        )
        return body


def assess_capture_pit(
    root: Path,
    plan: ForwardCapturePlan,
    attempt: str,
    pin: str,
    scope: HistoricalReadScope,
    scope_pin: str,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> "HistoricalPITAssessment":
    # Original scope validates final-test/holdout restrictions before any archive read.
    scope.__post_init__()
    require(
        scope.document.identifier == scope_pin
        and scope.partition == plan.partition
        and scope.window.start == scope.window.end == plan.day
        and scope.instrument.symbol == plan.ticker
        and scope.instrument.exchange.value == plan.market,
        "pit_capture_scope_mismatch",
    )
    verify_capture(root, plan, attempt, pin)
    from donghak_stock_vision.storage.historical_pit import (
        assess_historical_pit,
        availability_manifest,
    )

    return assess_historical_pit(
        root / DB, scope, scope_pin, (), availability_manifest(()).identifier, calendars
    )
