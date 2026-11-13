"""Local forward-capture journal over the existing raw archive and ingestion path.

Only a provisioned pilot DB is writable. Complete immutable manifests are the
publication boundary; the journal deliberately exposes interrupted stages.
"""

import fcntl
import hashlib
import json
import logging
import math
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
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.ingestion.market import KRXMarketPipeline
from donghak_stock_vision.providers.base import ProviderError, RawPage, krx_market_scope
from donghak_stock_vision.providers.forward_capture import CaptureHTTP, verify_capture_client
from donghak_stock_vision.providers.http import RequestGate
from donghak_stock_vision.providers.krx import ENDPOINTS, KRXProvider
from donghak_stock_vision.providers.krx_response import decode_response
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


def _response_page(plan: ForwardCapturePlan, body: bytes, metadata: dict[str, Any]) -> RawPage:
    # Missing representation means the original v1 parser semantics, including failures.
    # Never reinterpret an already sealed legacy gzip capture as successful ingestion.
    if "body_representation" in metadata:
        body = decode_response(
            body,
            metadata["headers"].get("content-encoding", ""),
            metadata["body_representation"],
            plan.max_response_bytes,
        )
    return RawPage(body, datetime.fromisoformat(metadata["response_completed_at"]), plan.day)


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
            try:
                page = _response_page(plan, body, metadata)
                values = _RecordedProvider(plan, page).parse_market(page)
                if plan.full_market_scope is not None:
                    require(
                        0 < len(values) <= 10000 and len({b.ticker for b in values}) == len(values),
                        "invalid_full_market_membership",
                    )
                    selected = [b.to_dict() for b in values]
                else:
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
        if all(prior.get(k) == plan.document.to_dict().get(k) for k in ("market", "ticker", "day")):
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
            "version": (
                "forward_capture_manifest_v2"
                if plan.full_market_scope is not None
                else "forward_capture_manifest_v1"
            ),
            **(
                {"normalization": "operational_store_only", "scope": "full_market"}
                if plan.full_market_scope is not None
                else {}
            ),
            "previous_captures": previous,
            "stop_reason": stop[0] if stop else None,
            "capture_id": capture_id,
            "plan": plan.document.to_dict(),
            "attempt": intent[1],
            "responses": attempts,
            "ingestion": [list(r) for r in ingestion],
            "software": "donghak-stock-vision-2.0.0a3",
            "parser": (
                "KRXProvider-daily-unadjusted-v2"
                if any("body_representation" in r["metadata"] for r in attempts)
                else "KRXProvider-daily-unadjusted-v1"
            ),
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
        if plan.full_market_scope is not None:
            require(doc["ingestion"] == [], "full_market_capture_must_not_write_research_bars")
            return owner
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
                "SELECT r.body,r.collected_at,r.source_date,f.metadata FROM raw_pages r "
                "JOIN bar_sources s ON r.id=s.raw_id "
                "LEFT JOIN forward_responses f ON r.id=f.raw_id "
                "WHERE s.ticker=? AND s.trading_date=? "
                "ORDER BY r.collected_at,r.id",
                (plan.ticker, plan.day.isoformat()),
            ).fetchall()
            require(0 < len(sources) <= 96, "ingestion_source_budget")
            latest = None
            for body, observed, source_day, metadata in sources:
                require(source_day == plan.day.isoformat(), "ingestion_source_binding")
                source_metadata = json.loads(metadata) if metadata else {}
                require(
                    source_metadata.get("response_completed_at", observed) == observed,
                    "ingestion_source_timestamp_mismatch",
                )
                source_page = _response_page(
                    plan, body, source_metadata | {"response_completed_at": observed}
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
    authorization: FrozenJSON | None = None,
    authorization_trust: FrozenJSON | None = None,
    authorization_policy: str = "independent_review_v1",
    operator_confirmed: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> FrozenJSON:
    capture_id = identity(plan, attempt)
    require(
        authorization_policy
        in (
            "independent_review_v1",
            "operator_read_only_pilot_v1",
            "operator_full_market_recurring_v1",
        ),
        "unknown_capture_authorization_policy",
    )
    require(
        not operator_confirmed
        or authorization_policy
        in ("operator_read_only_pilot_v1", "operator_full_market_recurring_v1"),
        "operator_confirmation_requires_explicit_operator_policy",
    )
    require(
        authorization_policy != "operator_read_only_pilot_v1" or plan.mode == "authorized_provider",
        "operator_policy_requires_provider_plan",
    )
    recurring = plan.full_market_scope is not None
    require(
        (authorization_policy == "operator_full_market_recurring_v1") == recurring,
        "full_market_requires_distinct_recurring_policy",
    )
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
    grant = None
    transport_kind = None
    if plan.mode == "authorized_provider":
        from donghak_stock_vision.storage.krx_authorization import (
            verify_authorization,
            verify_credential,
            verify_operator_authorization,
        )

        operator_policy = authorization_policy in (
            "operator_read_only_pilot_v1",
            "operator_full_market_recurring_v1",
        )
        require(
            not operator_policy or operator_confirmed is True,
            "explicit_operator_authorization_confirmation_required",
        )
        verifier = verify_operator_authorization if operator_policy else verify_authorization
        if recurring:
            from donghak_stock_vision.storage.krx_recurring import verify_recurring_authorization

            verifier = verify_recurring_authorization
        require(
            authorization is not None,
            "original_permission_terms_specification_and_operator_policy_required"
            if operator_policy
            else "original_permission_terms_and_signed_review_required",
        )
        assert authorization is not None
        grant = verifier(plan, attempt, authorization, authorization_trust, observed)
        verify_credential(grant, api_key)
        if operator_policy:
            require(
                len(api_key) <= 1024 and api_key.isascii() and api_key.isprintable(),
                "invalid_operator_credential_header",
            )
        transport_kind = grant.to_dict()["transport"]
        verify_capture_client(client, _endpoint(plan), transport_kind)
        if transport_kind != "mock_fixture":
            # Live permission/freshness/deadlines cannot be backdated with fixture clocks.
            verifier(plan, attempt, authorization, authorization_trust, datetime.now(UTC))

            def actual_clock() -> datetime:
                return datetime.now(UTC)

            clock = actual_clock
            monotonic, sleep = time.monotonic, time.sleep
    if grant is not None:
        from donghak_stock_vision.storage.krx_authorization import capture_root_check

        capture_root_check(grant, root)
    with _writer(root) as store:
        connection = store.connection
        origins = connection.execute("SELECT plan FROM forward_intents LIMIT 33").fetchall()
        require(recurring or len(origins) <= 32, "pilot_capture_budget")
        if recurring:
            require(
                all(
                    FrozenJSON(row[0]).to_dict()["version"] == "forward_capture_plan_v3"
                    for row in origins
                ),
                "recurring_archive_cannot_mix_legacy_pilot",
            )
            other = connection.execute(
                "SELECT capture_id FROM forward_intents WHERE json_extract(plan,'$.day')=? "
                "AND capture_id<>? LIMIT 1",
                (plan.day.isoformat(), capture_id),
            ).fetchone()
            require(other is None, "duplicate_collection_date")
            if (
                connection.execute(
                    "SELECT 1 FROM forward_intents WHERE capture_id=?", (capture_id,)
                ).fetchone()
                is None
            ):
                require(
                    (root / DB).stat().st_size + plan.max_response_bytes * 8 + 65536
                    <= 256 * 1024**2,
                    "recurring_capture_archive_headroom_exhausted",
                )
                state = _recurring_state(root, plan, clock())
                require(
                    state["locally_used_requests"] < plan.daily_limit,
                    "local_daily_budget_exhausted",
                )
                _recurring_running(root)
        else:
            require(
                all(
                    FrozenJSON(row[0]).to_dict()["version"] != "forward_capture_plan_v3"
                    for row in origins
                ),
                "legacy_pilot_cannot_mix_recurring_archive",
            )
        require(
            all(FrozenJSON(row[0]).to_dict()["mode"] == plan.mode for row in origins),
            "mixed_capture_origins_forbidden",
        )
        if grant is not None:
            if authorization_policy in (
                "operator_read_only_pilot_v1",
                "operator_full_market_recurring_v1",
            ):
                binding = _operator_binding(plan, attempt, grant)
            else:
                assert authorization_trust is not None
                binding = _authorization_binding(plan, attempt, grant, authorization_trust)
            existing_intent = connection.execute(
                "SELECT 1 FROM forward_intents WHERE capture_id=?", (capture_id,)
            ).fetchone()
            if existing_intent:
                _authorization_row(connection, capture_id, binding)
            else:
                with connection:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS forward_authorizations "
                        "(capture_id TEXT PRIMARY KEY, document TEXT NOT NULL, pin TEXT NOT NULL)"
                    )
                    for operation in ("UPDATE", "DELETE"):
                        connection.execute(
                            f"CREATE TRIGGER IF NOT EXISTS forward_auth_no_{operation.lower()} "
                            f"BEFORE {operation} ON forward_authorizations "
                            "BEGIN SELECT RAISE(ABORT,'forward authorization immutable'); END"
                        )
                    connection.execute(
                        "INSERT INTO forward_authorizations VALUES(?,?,?)",
                        (capture_id, binding.payload_json, binding.identifier),
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
                connection.execute("SELECT COUNT(*) FROM forward_intents").fetchone()[0]
                < (366 if recurring else 32),
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

            def permission_guard() -> None:
                if grant is not None:
                    _permission_clock(grant, clock())
                if recurring:
                    _recurring_running(root)

            http = CaptureHTTP(
                client,
                gate,
                plan,
                record,
                cancelled=cancelled,
                authorization_check=permission_guard,
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
        if successes and not projected["ingestion"] and not recurring:
            source = successes[-1]
            raw_id = source["raw_id"]
            body = connection.execute(
                "SELECT body FROM raw_pages WHERE id=?", (raw_id,)
            ).fetchone()[0]
            page = _response_page(plan, body, source["metadata"])
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
            "LEFT JOIN forward_manifests m USING(capture_id) ORDER BY i.rowid LIMIT 367"
        ).fetchall()
        versions = [
            FrozenJSON(r[0]).to_dict()["version"]
            for r in connection.execute("SELECT plan FROM forward_intents ORDER BY rowid LIMIT 367")
        ]
        full = bool(versions) and all(v == "forward_capture_plan_v3" for v in versions)
        require(len(rows) <= (366 if full else 32), "pilot_capture_budget")
        return FrozenJSON.freeze(
            {
                "version": "forward_capture_status_v2" if full else "forward_capture_status_v1",
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
    require(
        plan.observation_scope is None and plan.full_market_scope is None,
        "read_only_forward_scope_not_pit_eligible",
    )
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


def _permission_clock(grant: FrozenJSON, at: datetime) -> None:
    from donghak_stock_vision.backtest.validation import utc

    d = grant.to_dict()
    if not utc(d["valid_from"]) <= utc(at) < utc(d["valid_until"]):
        raise ProviderError("provider_permission_expired_during_capture")


def _authorization_binding(
    plan: ForwardCapturePlan, attempt: str, grant: FrozenJSON, trust: FrozenJSON
) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": "krx_forward_capture_authorization_v1",
            "capture_id": identity(plan, attempt),
            "plan_pin": plan.document.identifier,
            "grant_pin": grant.identifier,
            "reviewer_registry_pin": trust.to_dict()["registry_sha256"],
            "transport": grant.to_dict()["transport"],
            "publication_authenticated": False,
        }
    )


def _authorization_row(
    connection: sqlite3.Connection, capture_id: str, expected: FrozenJSON
) -> None:
    require(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='forward_authorizations'"
        ).fetchone()
        is not None,
        "original_capture_authorization_binding_missing",
    )
    rows = connection.execute(
        "SELECT document,pin FROM forward_authorizations WHERE capture_id=?", (capture_id,)
    ).fetchall()
    require(
        len(rows) == 1 and rows[0] == (expected.payload_json, expected.identifier),
        "capture_authorization_binding_mismatch",
    )


def verify_capture_authorization(
    root: Path,
    plan: ForwardCapturePlan,
    attempt: str,
    capture_pin: str,
    reference: FrozenJSON,
    trust: FrozenJSON | None,
    at: datetime,
) -> str:
    from donghak_stock_vision.backtest.validation import utc
    from donghak_stock_vision.storage.krx_authorization import verify_authorization

    grant = verify_authorization(plan, attempt, reference, trust, at)
    from donghak_stock_vision.storage.krx_authorization import capture_root_check

    capture_root_check(grant, root)
    assert trust is not None
    owner = verify_capture(root, plan, attempt, capture_pin)
    expected = _authorization_binding(plan, attempt, grant, trust)
    with closing(_connection(root)) as connection:
        _authorization_row(connection, identity(plan, attempt), expected)
    for response in owner.to_dict()["responses"]:
        for key in ("request_started_at", "response_completed_at"):
            verify_authorization(plan, attempt, reference, trust, utc(response["metadata"][key]))
    return (
        "real_provider_https_local_capture"
        if grant.to_dict()["transport"] == "provider_https"
        else "authorized_provider_mock_fixture"
    )


def _operator_binding(plan: ForwardCapturePlan, attempt: str, policy: FrozenJSON) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": (
                "krx_recurring_read_only_capture_binding_v1"
                if plan.full_market_scope is not None
                else "krx_operator_read_only_capture_binding_v1"
            ),
            "policy": (
                "operator_full_market_recurring_v1"
                if plan.full_market_scope is not None
                else "operator_read_only_pilot_v1"
            ),
            "capture_id": identity(plan, attempt),
            "plan_pin": plan.document.identifier,
            "policy_pin": policy.identifier,
            "transport": policy.to_dict()["transport"],
            "independent_authorization_verified": False,
            "forward_signal_eligible": False,
            "paper_execution_eligible": False,
            "publication_authenticated": False,
        }
    )


def verify_operator_capture_authorization(
    root: Path,
    plan: ForwardCapturePlan,
    attempt: str,
    capture_pin: str,
    reference: FrozenJSON,
    at: datetime,
) -> str:
    """Verify original read-only capture evidence, without promoting its trust basis."""
    from donghak_stock_vision.backtest.validation import utc
    from donghak_stock_vision.storage.krx_authorization import (
        capture_root_check,
        verify_operator_authorization,
    )

    verifier = verify_operator_authorization
    if plan.full_market_scope is not None:
        from donghak_stock_vision.storage.krx_recurring import verify_recurring_authorization

        verifier = verify_recurring_authorization
    policy = verifier(plan, attempt, reference, None, at)
    capture_root_check(policy, root)
    owner = verify_capture(root, plan, attempt, capture_pin)
    with closing(_connection(root)) as connection:
        _authorization_row(
            connection, identity(plan, attempt), _operator_binding(plan, attempt, policy)
        )
    for response in owner.to_dict()["responses"]:
        for key in ("request_started_at", "response_completed_at"):
            verifier(plan, attempt, reference, None, utc(response["metadata"][key]))
    return (
        "real_provider_https_local_capture"
        if policy.to_dict()["transport"] == "provider_https"
        else "authorized_provider_mock_fixture"
    )


def _recurring_state(root: Path, plan: ForwardCapturePlan, at: datetime) -> dict[str, Any]:
    """Read-only preview; absence is zero use, never an implicit archive initialization."""

    require(not root.is_symlink(), "capture_symlink_forbidden")
    state: dict[str, Any] = dict(paused=False, duplicate=False, locally_used_requests=0)
    if not (root / DB).exists():
        return state
    with closing(_connection(root)) as connection:

        def exists(name: str) -> bool:
            return (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()
                is not None
            )

        require(
            all(exists(n) for n in ("forward_intents", "forward_responses", "forward_manifests")),
            "recurring_archive_schema_missing",
        )
        versions = connection.execute("SELECT plan FROM forward_intents LIMIT 367").fetchall()
        require(
            len(versions) <= 366
            and all(
                FrozenJSON(row[0]).to_dict()["version"] == "forward_capture_plan_v3"
                for row in versions
            ),
            "recurring_archive_cannot_mix_legacy_pilot",
        )
        if exists("forward_recurring_control"):
            row = connection.execute(
                "SELECT paused FROM forward_recurring_control ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                require(type(row[0]) is int and row[0] in (0, 1), "invalid_recurring_control")
                state["paused"] = bool(row[0])
        if exists("forward_intents"):
            state["duplicate"] = (
                connection.execute(
                    "SELECT 1 FROM forward_intents "
                    "WHERE json_extract(plan,'$.version')='forward_capture_plan_v3' "
                    "AND json_extract(plan,'$.day')=? LIMIT 1",
                    (plan.day.isoformat(),),
                ).fetchone()
                is not None
            )
        require(
            not versions or exists("request_budget"), "recurring_request_budget_history_missing"
        )
        if exists("request_budget"):
            row = connection.execute(
                "SELECT count FROM request_budget WHERE day=?",
                (utc(at).astimezone(SEOUL).date().isoformat(),),
            ).fetchone()
            state["locally_used_requests"] = row[0] if row else 0
            slot = connection.execute("SELECT MAX(last_slot) FROM request_budget").fetchone()[0]
            require(
                slot is None
                or (
                    type(slot) in (int, float)
                    and math.isfinite(slot)
                    and slot + plan.interval_seconds <= utc(at).timestamp() + plan.max_seconds
                ),
                "invalid_or_unbounded_request_slot",
            )
        require(
            type(state["locally_used_requests"]) is int and state["locally_used_requests"] >= 0,
            "invalid_local_request_usage",
        )
        recorded_today = connection.execute(
            "SELECT COUNT(*) FROM forward_responses WHERE "
            "date(json_extract(metadata,'$.request_started_at'),'+9 hours')=?",
            (utc(at).astimezone(SEOUL).date().isoformat(),),
        ).fetchone()[0]
        require(
            state["locally_used_requests"] >= recorded_today,
            "recurring_request_budget_history_mismatch",
        )
    return state


def _recurring_running(root: Path) -> None:
    """Pause is checked during cooperative transport guards as well as before acceptance."""

    with closing(_connection(root)) as c:
        if c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='forward_recurring_control'"
        ).fetchone():
            row = c.execute(
                "SELECT paused FROM forward_recurring_control ORDER BY id DESC LIMIT 1"
            ).fetchone()
            require(row is None or row[0] == 0, "recurring_collection_paused")


def recurring_control(
    root: Path, plan: ForwardCapturePlan, reference: FrozenJSON, at: datetime, paused: bool
) -> FrozenJSON:
    from donghak_stock_vision.storage.krx_authorization import capture_root_check
    from donghak_stock_vision.storage.krx_recurring import verify_recurring_authorization

    require(type(paused) is bool, "pause_boolean_required")
    policy = verify_recurring_authorization(plan, "control", reference, None, at)
    capture_root_check(policy, root)
    _recurring_state(root, plan, at)  # Reject legacy/unknown archives before writable open.

    with _writer(root) as store:
        _recurring_state(root, plan, at)  # Recheck under the original capture lock.
        with store.connection:
            store.connection.execute(
                "CREATE TABLE IF NOT EXISTS forward_recurring_control "
                "(id INTEGER PRIMARY KEY,paused INTEGER NOT NULL,"
                "at TEXT NOT NULL,policy_pin TEXT NOT NULL)"
            )
            for operation in ("UPDATE", "DELETE"):
                store.connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS recurring_no_{operation} "
                    f"BEFORE {operation} ON forward_recurring_control "
                    "BEGIN SELECT RAISE(ABORT,'immutable control history'); END"
                )
            store.connection.execute(
                "INSERT INTO forward_recurring_control(paused,at,policy_pin) VALUES(?,?,?)",
                (int(paused), utc(at).isoformat(), policy.identifier),
            )
    return FrozenJSON.freeze(
        dict(version="krx_recurring_control_v1", paused=paused, network_requests=0)
    )


def recurring_preview(
    plan: ForwardCapturePlan,
    attempt: str,
    reference: FrozenJSON | None,
    trust: FrozenJSON | None,
    at: datetime,
    credential_present: bool,
    *,
    credential: str | None = None,
    capture_root: Path | None = None,
) -> FrozenJSON:
    from donghak_stock_vision.storage.krx_authorization import capture_root_check, verify_credential
    from donghak_stock_vision.storage.krx_recurring import POLICY, verify_recurring_authorization

    plan.__post_init__()
    require(plan.full_market_scope is not None, "explicit_full_market_plan_required")
    credential_checked = False
    blockers = plan.blockers(credential_present)
    policy = None
    state: dict[str, Any] = dict(paused=None, duplicate=None, locally_used_requests=None)
    try:
        require(
            reference is not None and capture_root is not None,
            "recurring_policy_and_archive_required",
        )
        assert reference is not None and capture_root is not None
        policy = verify_recurring_authorization(plan, attempt, reference, trust, at)
        capture_root_check(policy, capture_root)
        require(
            credential is not None and bool(credential.strip()), "credential_binding_not_checked"
        )
        assert credential is not None
        require(
            len(credential) <= 1024 and credential.isascii() and credential.isprintable(),
            "invalid_operator_credential_header",
        )
        verify_credential(policy, credential)
        credential_checked = True
        state = _recurring_state(capture_root, plan, at)
        for flag, reason in (
            (state["paused"], "recurring_collection_paused"),
            (state["duplicate"], "duplicate_collection_date"),
            (state["locally_used_requests"] >= plan.daily_limit, "local_daily_budget_exhausted"),
        ):
            if flag:
                blockers.append(reason)
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as error:
        blockers.append(
            str(error) if isinstance(error, ValueError) else "recurring_evidence_unreadable"
        )
    d = policy.to_dict() if policy else {}
    return FrozenJSON.freeze(
        dict(
            version="krx_recurring_preview_v1",
            policy=POLICY,
            scope="full_market",
            market="KOSPI",
            endpoint=_endpoint(plan),
            method="GET",
            parameters={"basDd": plan.day.strftime("%Y%m%d")},
            approved_from=d.get("approved_from"),
            approved_through=d.get("approved_through"),
            valid_from=d.get("valid_from"),
            valid_until=d.get("valid_until"),
            policy_integrity_verified=policy is not None,
            credential_binding_verified=credential_checked,
            authorization_basis="explicit_owner_local_full_market_document_review",
            requests_maximum=1,
            retries=0,
            concurrency=1,
            max_response_bytes=plan.max_response_bytes,
            max_seconds=plan.max_seconds,
            configured_daily_limit=plan.daily_limit,
            verified_service_daily_limit=d.get("verified_service_daily_limit"),
            remaining_local_allowance=(
                max(0, plan.daily_limit - state["locally_used_requests"])
                if state["locally_used_requests"] is not None
                else None
            ),
            **state,
            blockers=list(dict.fromkeys(blockers)),
            ready_for_explicit_read_only_capture=not blockers,
            origin=d.get("transport"),
            independent_authorization_verified=False,
            provider_signature_verified=False,
            forward_signal_eligible=False,
            paper_execution_eligible=False,
            pit_qualified=False,
            execution_authorized=False,
            network_requests=0,
            explicit_network_opt_in_still_required=True,
            explicit_operator_confirmation_still_required=True,
        )
    )
