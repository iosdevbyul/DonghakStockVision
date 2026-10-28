"""Private operational SQLite storage; structural validation confers no research eligibility."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.providers.base import ProviderError, RawPage, krx_market_scope
from donghak_stock_vision.providers.krx import ENDPOINTS, KRXProvider
from donghak_stock_vision.providers.krx_response import decode_response
from donghak_stock_vision.storage.capture_evidence_sink import _receipt_bytes
from donghak_stock_vision.storage.sqlite import (
    OPERATIONAL_APPLICATION_ID,
    RAW_SCHEMA,
    archive_raw_page,
)

DB = Path("database/operational.sqlite3")
INELIGIBLE_FIELDS = (
    "pit_qualified",
    "training_ready",
    "oos_approved",
    "forward_signal_eligible",
    "paper_execution_eligible",
    "execution_authorized",
)


def _eligibility() -> dict[str, bool]:
    return dict.fromkeys(INELIGIBLE_FIELDS, False)


SCHEMA = (
    RAW_SCHEMA
    + """
CREATE TABLE operational_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL
);
CREATE TABLE ingestion_batches (
    batch_id TEXT PRIMARY KEY, raw_id INTEGER NOT NULL UNIQUE REFERENCES raw_pages(id),
    request_hash TEXT NOT NULL, document TEXT NOT NULL, fingerprint TEXT NOT NULL
);
CREATE TABLE market_observations (
    market TEXT NOT NULL, trading_date TEXT NOT NULL, ticker TEXT NOT NULL,
    payload TEXT NOT NULL, reported_name TEXT, validation_status TEXT NOT NULL,
    primary_raw_id INTEGER NOT NULL REFERENCES raw_pages(id),
    PRIMARY KEY(market,trading_date,ticker)
);
CREATE TABLE observation_sources (
    market TEXT NOT NULL, trading_date TEXT NOT NULL, ticker TEXT NOT NULL,
    raw_id INTEGER NOT NULL REFERENCES raw_pages(id), source_row TEXT NOT NULL,
    PRIMARY KEY(market,trading_date,ticker,raw_id),
    FOREIGN KEY(market,trading_date,ticker)
        REFERENCES market_observations(market,trading_date,ticker)
);
CREATE INDEX sources_by_raw ON observation_sources(raw_id);
"""
    + "\n".join(
        f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'operational evidence immutable'); END;"
        for table in (
            "operational_settings",
            "ingestion_batches",
            "market_observations",
            "observation_sources",
            "raw_pages",
        )
        for action in ("UPDATE", "DELETE")
    )
)


@dataclass(frozen=True)
class StorageLimits:
    max_database_bytes: int = 10 * 1024**3
    min_free_bytes: int = 256 * 1024**2
    max_rows_per_batch: int = 2000

    def __post_init__(self) -> None:
        require(
            type(self.max_database_bytes) is int and 131072 <= self.max_database_bytes <= 1024**4,
            "invalid_database_limit",
        )
        require(
            type(self.min_free_bytes) is int and 0 <= self.min_free_bytes <= 1024**5,
            "invalid_free_space_limit",
        )
        require(
            type(self.max_rows_per_batch) is int and 1 <= self.max_rows_per_batch <= 10000,
            "invalid_batch_row_limit",
        )


def _private(path: Path, *, directory: bool) -> None:
    require(os.name == "posix", "private_storage_requires_posix_permissions")
    require(
        not path.is_symlink() and (path.is_dir() if directory else path.is_file()),
        "operational_storage_path_required",
    )
    info = path.stat()
    require(
        info.st_uid == os.geteuid() and not info.st_mode & 0o077,
        "operational_storage_permissions_required",
    )
    require(directory or info.st_nlink == 1, "operational_database_hardlink_rejected")


def _root(path: Path) -> Path:
    path = path.expanduser()
    require(
        path.is_absolute() and path == path.resolve(), "canonical_absolute_storage_root_required"
    )
    return path


def _schema_rows(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()


def _check_schema(connection: sqlite3.Connection) -> None:
    require(
        connection.execute("PRAGMA application_id").fetchone()[0] == OPERATIONAL_APPLICATION_ID
        and connection.execute("PRAGMA user_version").fetchone()[0] == 1,
        "unsupported_operational_storage_version",
    )
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(SCHEMA)
        require(_schema_rows(connection) == _schema_rows(expected), "operational_schema_mismatch")


def _parse(
    page: RawPage, market: str, encoding: str, representation: str, maximum_rows: int
) -> tuple[list[DailyBar], dict[str, dict[str, Any]], bytes]:
    require(
        market in ENDPOINTS and type(page.source_date) is date, "market_and_source_date_required"
    )
    require(
        page.collected_at.tzinfo is not None and page.collected_at.utcoffset() is not None,
        "aware_collection_time_required",
    )
    decoded = decode_response(page.body, encoding, representation, 2 * 1024**2)
    # Reject duplicate object keys before using the existing authoritative KRX parser.
    doc = FrozenJSON(decoded.decode("utf-8")).to_dict()
    pending: list[Any] = [doc]
    while pending:
        value = pending.pop()
        require(type(value) is not float, "fractional_json_numbers_require_explicit_contract")
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    require(
        isinstance(doc.get("OutBlock_1"), list) and len(doc["OutBlock_1"]) <= maximum_rows,
        "bounded_market_rows_required",
    )
    provider = object.__new__(KRXProvider)
    provider.market = market
    bars = provider.parse_market(RawPage(decoded, page.collected_at, page.source_date))
    rows: dict[str, dict[str, Any]] = {}
    for row in doc["OutBlock_1"]:
        ticker = row["ISU_CD"]
        require(ticker not in rows, "duplicate_market_observation")
        require(
            row.get("ISU_NM") is None or (type(row["ISU_NM"]) is str and len(row["ISU_NM"]) <= 512),
            "invalid_reported_instrument_name",
        )
        rows[ticker] = row
    return bars, rows, decoded


def _validate_capture_reference(request: FrozenJSON) -> None:
    """Local binding only; original archive/authorization replay lives in the adapter."""
    from donghak_stock_vision.backtest.validation import fields, sha

    value = request.to_dict()
    if "source_capture" not in value:
        return
    source = fields(
        value["source_capture"],
        "version capture_id capture_pin plan_pin authorization_pin trust_pin "
        "sequence raw_id receipt_pin raw_sha256 market day response_at origin",
    )
    require(source["version"] == "operational_capture_reference_v1", "capture_reference_version")
    for key in ("capture_id", "capture_pin", "plan_pin", "receipt_pin", "raw_sha256"):
        sha(source[key])
    for key in ("authorization_pin", "trust_pin"):
        if source[key] is not None:
            sha(source[key])
    require(
        type(source["sequence"]) is int
        and 0 <= source["sequence"] < 3
        and type(source["raw_id"]) is int
        and source["raw_id"] > 0
        and source["market"] == value["market"] == "KOSPI"
        and source["day"] == value["source_date"]
        and source["response_at"] == value["collected_at"]
        and source["raw_sha256"] == value["raw_sha256"],
        "capture_reference_binding_mismatch",
    )
    require(
        (
            source["origin"] == "offline_fixture"
            and value["declared_origin"] == "synthetic_fixture"
            and source["authorization_pin"] is None
            and source["trust_pin"] is None
        )
        or (
            source["origin"]
            in {"real_provider_https_local_capture", "authorized_provider_mock_fixture"}
            and source["authorization_pin"] is not None
            and value["declared_origin"] == "local_response_unqualified"
        ),
        "capture_reference_origin_mismatch",
    )


class OperationalMarketStore:
    """One connection per worker. No provider calls or recovery-artifact import API."""

    @classmethod
    def initialize(cls, root: Path, limits: StorageLimits | None = None) -> FrozenJSON:
        root = _root(root)
        if root.exists():
            with closing(cls(root, writable=True)) as store:
                require(
                    limits is None or limits == store.limits, "existing_storage_config_conflict"
                )
                return store.status()
        limits = limits or StorageLimits()
        limits.__post_init__()
        require(root.parent.is_dir(), "storage_parent_required")
        require(
            shutil.disk_usage(root.parent).free >= limits.min_free_bytes + 131072,
            "insufficient_free_disk_space",
        )
        root.mkdir(mode=0o700)  # Exclusive reservation; interrupted initialization fails closed.
        for name in ("database", "logs", "backups"):
            (root / name).mkdir(mode=0o700)
        fd = os.open(root / DB, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(root / DB)) as con:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("PRAGMA journal_mode=DELETE")
            con.executescript(SCHEMA)
            con.execute(f"PRAGMA application_id={OPERATIONAL_APPLICATION_ID}")
            con.execute("PRAGMA user_version=1")
            with con:
                con.execute(
                    "INSERT INTO operational_settings VALUES(1,?)",
                    (
                        FrozenJSON.freeze(
                            {"version": "operational_market_storage_v1", "limits": asdict(limits)}
                        ).payload_json,
                    ),
                )
        for path in (root / "database", root, root.parent):
            directory_fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        with closing(cls(root, writable=True)) as store:
            return store.status()

    def __init__(self, root: Path, *, writable: bool = False) -> None:
        self.root = _root(root)
        for path in (self.root, *(self.root / x for x in ("database", "logs", "backups"))):
            _private(path, directory=True)
        _private(self.root / DB, directory=False)
        # Identify our file before a writable SQLite open can recover a hot journal.
        # An unrelated research DB must never be repaired or migrated by this adapter.
        with (self.root / DB).open("rb") as stream:
            header = stream.read(100)
        require(header[:16] == b"SQLite format 3\x00", "invalid_operational_database_header")
        require(
            int.from_bytes(header[68:72], "big") == OPERATIONAL_APPLICATION_ID
            and int.from_bytes(header[60:64], "big") == 1,
            "unsupported_operational_storage_version",
        )
        self.connection = sqlite3.connect(
            (self.root / DB).as_uri() + ("?mode=rw" if writable else "?mode=ro"),
            uri=True,
            timeout=5,
        )
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            _check_schema(
                self.connection
            )  # Writable open also performs SQLite hot-journal recovery.
            row = self.connection.execute(
                "SELECT substr(document,1,8193),length(document) "
                "FROM operational_settings WHERE id=1"
            ).fetchone()
            require(row is not None, "operational_initialization_incomplete")
            require(row[1] <= 8192, "operational_settings_byte_limit")
            doc = FrozenJSON(row[0]).to_dict()
            require(
                set(doc) == {"version", "limits"}
                and doc["version"] == "operational_market_storage_v1",
                "unsupported_operational_settings",
            )
            try:
                self.limits = StorageLimits(**doc["limits"])
            except (TypeError, ValueError):
                raise ValueError("invalid_operational_settings") from None
            self.limits.__post_init__()
            require(
                self.connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete",
                "unsupported_operational_journal_mode",
            )
            self.writable = writable
            if writable:
                self.connection.execute("PRAGMA synchronous=FULL")
                page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
                actual = self.connection.execute(
                    f"PRAGMA max_page_count={self.limits.max_database_bytes // page_size}"
                ).fetchone()[0]
                require(
                    actual * page_size <= self.limits.max_database_bytes,
                    "operational_database_limit",
                )
            else:
                self.connection.execute("PRAGMA query_only=ON")
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def usage(self) -> dict[str, Any]:
        total = 0
        count = 0
        for path in self.root.rglob("*"):
            require(not path.is_symlink(), "storage_symlink_rejected")
            count += 1
            require(count <= 10000, "storage_inventory_limit")
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                # A concurrent SQLite commit can remove its transient rollback journal.
                continue
        size = (self.root / DB).stat().st_size
        free = shutil.disk_usage(self.root).free
        return {
            "stored_file_bytes": total,
            "database_bytes": size,
            "free_disk_bytes": free,
            "max_database_bytes": self.limits.max_database_bytes,
            "min_free_bytes": self.limits.min_free_bytes,
            "free_space_requirement_met": free >= self.limits.min_free_bytes,
            "database_limit_met": size <= self.limits.max_database_bytes,
        }

    def _manifest(self, batch: str, raw_id: int, request: FrozenJSON, count: int) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": (
                    "operational_krx_batch_v2"
                    if "source_capture" in request.to_dict()
                    else "operational_krx_batch_v1"
                ),
                "batch_id": batch,
                "request": request.to_dict(),
                "raw_receipt": json.loads(_receipt_bytes(self.connection, raw_id)),
                "observation_count": count,
                "eligibility": _eligibility(),
            }
        )

    def ingest(
        self,
        batch_id: str,
        market: str,
        page: RawPage,
        *,
        source_kind: str,
        content_encoding: str = "identity",
        representation: str = "wire",
        source_capture: FrozenJSON | None = None,
    ) -> FrozenJSON:
        require(self.writable, "explicit_writable_operational_store_required")
        require(
            isinstance(batch_id, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", batch_id) is not None,
            "invalid_batch_identity",
        )
        require(
            source_kind in {"synthetic_fixture", "local_response_unqualified"},
            "explicit_unqualified_origin_required",
        )
        bars, rows, decoded = _parse(
            page, market, content_encoding, representation, self.limits.max_rows_per_batch
        )
        request = FrozenJSON.freeze(
            {
                "market": market,
                "source_date": page.source_date.isoformat() if page.source_date else None,
                "collected_at": page.collected_at.astimezone(UTC).isoformat(),
                "raw_sha256": hashlib.sha256(page.body).hexdigest(),
                "content_encoding": content_encoding,
                "representation": representation,
                "declared_origin": source_kind,
                **({"source_capture": source_capture.to_dict()} if source_capture else {}),
            }
        )
        _validate_capture_reference(request)
        if source_capture is not None:
            require(batch_id == source_capture.identifier, "capture_batch_identity_mismatch")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT raw_id,request_hash FROM ingestion_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if existing:
                require(existing[1] == request.identifier, "idempotency_conflict")
                return self._verify_batch(batch_id, existing[0])
            usage = self.usage()
            estimate = len(page.body) + len(decoded) * 3 + len(bars) * 2048 + 65536
            require(
                usage["database_bytes"] + estimate <= self.limits.max_database_bytes,
                "operational_database_limit",
            )
            require(
                usage["free_disk_bytes"] >= self.limits.min_free_bytes + estimate * 2,
                "insufficient_free_disk_space",
            )
            raw_id = archive_raw_page(
                self.connection, batch_id, "krx", krx_market_scope(market), page
            )
            for bar in bars:
                key = (market, bar.trading_date.isoformat(), bar.ticker)
                name = rows[bar.ticker].get("ISU_NM")
                old = self.connection.execute(
                    "SELECT payload,reported_name FROM market_observations "
                    "WHERE market=? AND trading_date=? AND ticker=?",
                    key,
                ).fetchone()
                if old:
                    require(
                        DailyBar.from_dict(json.loads(old[0])).content() == bar.content()
                        and old[1] == name,
                        "conflicting_observation_requires_reviewed_revision",
                    )
                else:
                    self.connection.execute(
                        "INSERT INTO market_observations VALUES(?,?,?,?,?,?,?)",
                        (
                            *key,
                            FrozenJSON.freeze(bar.to_dict()).payload_json,
                            name,
                            "structurally_valid_unqualified",
                            raw_id,
                        ),
                    )
                self.connection.execute(
                    "INSERT INTO observation_sources VALUES(?,?,?,?,?)",
                    (*key, raw_id, FrozenJSON.freeze(rows[bar.ticker]).payload_json),
                )
            document = self._manifest(batch_id, raw_id, request, len(bars))
            self.connection.execute(
                "INSERT INTO ingestion_batches VALUES(?,?,?,?,?)",
                (batch_id, raw_id, request.identifier, document.payload_json, document.identifier),
            )
        return document

    def _verify_batch(self, batch: str, raw_id: int) -> FrozenJSON:
        row = self.connection.execute(
            "SELECT substr(document,1,16385),fingerprint,request_hash,length(document) "
            "FROM ingestion_batches "
            "WHERE batch_id=? AND raw_id=?",
            (batch, raw_id),
        ).fetchone()
        require(row is not None, "missing_batch_manifest")
        require(row[3] <= 16384, "batch_manifest_byte_limit")
        doc = FrozenJSON(row[0])
        require(doc.identifier == row[1], "batch_manifest_fingerprint_mismatch")
        request = FrozenJSON.freeze(doc.to_dict()["request"])
        require(request.identifier == row[2], "batch_request_mismatch")
        r = request.to_dict()
        require(
            set(r) - {"source_capture"}
            == {
                "market",
                "source_date",
                "collected_at",
                "raw_sha256",
                "content_encoding",
                "representation",
                "declared_origin",
            },
            "invalid_batch_request_fields",
        )
        require(
            r["declared_origin"] in {"synthetic_fixture", "local_response_unqualified"},
            "invalid_origin",
        )
        _validate_capture_reference(request)
        if "source_capture" in r:
            require(
                batch == FrozenJSON.freeze(r["source_capture"]).identifier,
                "capture_batch_identity_mismatch",
            )
        raw = self.connection.execute(
            "SELECT substr(body,1,2097153),collected_at,source_date,run_id,provider,ticker,"
            "length(body) FROM raw_pages WHERE id=?",
            (raw_id,),
        ).fetchone()
        require(
            raw is not None
            and raw[6] <= 2097152
            and raw[1:6]
            == (r["collected_at"], r["source_date"], batch, "krx", krx_market_scope(r["market"]))
            and hashlib.sha256(raw[0]).hexdigest() == r["raw_sha256"],
            "batch_source_binding_mismatch",
        )
        page = RawPage(raw[0], datetime.fromisoformat(raw[1]), date.fromisoformat(raw[2]))
        bars, rows, _ = _parse(
            page,
            r["market"],
            r["content_encoding"],
            r["representation"],
            self.limits.max_rows_per_batch,
        )
        require(
            self._manifest(batch, raw_id, request, len(bars)) == doc,
            "batch_manifest_semantic_mismatch",
        )
        count = self.connection.execute(
            "SELECT COUNT(*) FROM observation_sources WHERE raw_id=?", (raw_id,)
        ).fetchone()[0]
        require(count == len(bars), "batch_membership_mismatch")
        for bar in bars:
            key = (bar.market, bar.trading_date.isoformat(), bar.ticker)
            observation = self.connection.execute(
                "SELECT substr(payload,1,8193),reported_name,validation_status,primary_raw_id,"
                "length(payload) "
                "FROM market_observations WHERE market=? AND trading_date=? AND ticker=?",
                key,
            ).fetchone()
            source = self.connection.execute(
                "SELECT substr(source_row,1,12582913),length(source_row) FROM observation_sources "
                "WHERE market=? AND trading_date=? AND ticker=? AND raw_id=?",
                (*key, raw_id),
            ).fetchone()
            require(observation is not None and source is not None, "missing_normalized_provenance")
            require(
                observation[4] <= 8192 and source[1] <= 12582912, "source_projection_byte_limit"
            )
            retained = DailyBar.from_dict(json.loads(observation[0]))
            require(
                retained.content() == bar.content()
                and observation[1] == rows[bar.ticker].get("ISU_NM")
                and observation[2] == "structurally_valid_unqualified"
                and FrozenJSON(source[0]) == FrozenJSON.freeze(rows[bar.ticker]),
                "normalized_source_mismatch",
            )
            if observation[3] == raw_id:
                require(retained.to_dict() == bar.to_dict(), "original_collection_time_mismatch")
        return doc

    def status(self) -> FrozenJSON:
        with self.connection:
            self.connection.execute("BEGIN")
            markets = [
                dict(market=m, latest_trading_date=d, observation_count=n)
                for m, d, n in self.connection.execute(
                    "SELECT market,MAX(trading_date),COUNT(*) FROM market_observations "
                    "GROUP BY market ORDER BY market"
                )
            ]
            count = self.connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[
                0
            ]
            batches = self.connection.execute("SELECT COUNT(*) FROM ingestion_batches").fetchone()[
                0
            ]
            sources = self.connection.execute(
                "SELECT COUNT(*) FROM observation_sources"
            ).fetchone()[0]
            duplicates = self.connection.execute(
                "SELECT COUNT(*) FROM (SELECT market,trading_date,ticker FROM market_observations "
                "GROUP BY market,trading_date,ticker HAVING COUNT(*)>1)"
            ).fetchone()[0]
            return FrozenJSON.freeze(
                {
                    "version": "operational_market_storage_status_v1",
                    "schema_version": 1,
                    "unique_observation_key": ["market", "trading_date", "ticker"],
                    "store_kind": "operational_unqualified",
                    "tables": [
                        r[0]
                        for r in self.connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                        )
                    ],
                    "observation_count": count,
                    "batch_count": batches,
                    "markets": markets,
                    "latest_trading_date": max(
                        (m["latest_trading_date"] for m in markets), default=None
                    ),
                    "latest_response_date": self.connection.execute(
                        "SELECT MAX(source_date) FROM raw_pages"
                    ).fetchone()[0],
                    "duplicate_records": duplicates,
                    "additional_source_links": sources - count,
                    "disk_usage": self.usage(),
                    "eligibility": _eligibility(),
                }
            )

    def validate(self) -> FrozenJSON:
        issues = []
        total = 0
        with self.connection:
            self.connection.execute("BEGIN")
            _check_schema(self.connection)
            if self.connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                issues.append("sqlite_integrity_failure")
            if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                issues.append("foreign_key_violation")
            for batch, raw_id in self.connection.execute(
                "SELECT batch_id,raw_id FROM ingestion_batches ORDER BY batch_id"
            ):
                try:
                    self._verify_batch(batch, raw_id)
                except (ValueError, TypeError, KeyError, ProviderError):
                    total += 1
                    if len(issues) < 100:
                        issues.append("batch_source_or_manifest_mismatch")
            raw_count = self.connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0]
            batches = self.connection.execute("SELECT COUNT(*) FROM ingestion_batches").fetchone()[
                0
            ]
            receipt_count = self.connection.execute(
                "SELECT COUNT(*) FROM raw_capture_receipts"
            ).fetchone()[0]
            if raw_count != batches or raw_count != receipt_count:
                issues.append("orphan_source_or_receipt")
            missing = self.connection.execute(
                "SELECT COUNT(*) FROM market_observations o LEFT JOIN observation_sources s "
                "ON o.market=s.market AND o.trading_date=s.trading_date AND o.ticker=s.ticker "
                "AND o.primary_raw_id=s.raw_id WHERE s.raw_id IS NULL"
            ).fetchone()[0]
            if missing:
                issues.append("missing_primary_source")
        return FrozenJSON.freeze(
            {
                "version": "operational_market_storage_validation_v1",
                "valid": not issues and total == 0,
                "issues": issues,
                "invalid_batches": total,
                "eligibility": _eligibility(),
            }
        )
