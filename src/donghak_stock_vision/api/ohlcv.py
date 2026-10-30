"""Optional, immutable SQLite reader for original KRX daily bars.

Never repairs, adjusts or synthesizes a missing market observation.
"""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.storage.sqlite import OPERATIONAL_APPLICATION_ID


class OhlcvBar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    date: str
    open: int
    high: int
    low: int
    close: int
    volume: int


class OhlcvSeries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ticker: str
    bars: tuple[OhlcvBar, ...]


class OhlcvReaderError(ValueError):
    pass


@dataclass(frozen=True)
class CollectionAuditRow:
    identifier: int
    finished_at: str
    status: str
    rows: int


class OhlcvMarketReader:
    """Opt-in market DB transport, bound at startup to one verified file hash."""

    def __init__(self, path: Path, *, expected_sha256: str) -> None:
        if not path.is_file():
            raise OhlcvReaderError("market database does not exist")
        if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
            raise OhlcvReaderError("explicit lowercase database sha256 required")
        for suffix in ("-wal", "-journal"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise OhlcvReaderError("active market database sidecar")
        self._path = path.resolve()
        self._sha = expected_sha256
        self._verify_file()
        with closing(self._connect()) as conn:
            if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
                raise OhlcvReaderError("market database integrity check failed")
            scopes = set(
                conn.execute(
                    "SELECT DISTINCT json_extract(payload,'$.provider'), "
                    "json_extract(payload,'$.market'), "
                    "json_extract(payload,'$.adjustment') FROM bars"
                )
            )
            if scopes != {("krx", "KOSPI", "unadjusted")}:
                raise OhlcvReaderError("market database metadata scope mismatch")

    def _verify_file(self) -> None:
        with self._path.open("rb") as fh:
            digest = hashlib.file_digest(fh, "sha256").hexdigest()
        if digest != self._sha:
            raise OhlcvReaderError("market database fingerprint mismatch")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path.as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.execute("PRAGMA query_only=ON")
        return conn

    def collection_audit(self) -> tuple[CollectionAuditRow, ...]:
        """Recorded outcomes only; these rows do not prove raw-source provenance.

        Reuses the pinned, query-only immutable DB boundary. No raw exceptions,
        run names, scopes or provider payloads leave this adapter.
        """
        self._verify_file()
        with closing(self._connect()) as conn:
            rows = []
            for identifier, finished, status, count in conn.execute(
                "SELECT id,finished_at,status,rows FROM collection_runs ORDER BY id DESC"
            ):
                if (
                    type(identifier) is not int
                    or identifier < 1
                    or type(count) is not int
                    or not isinstance(status, str)
                    or not isinstance(finished, str)
                ):
                    raise OhlcvReaderError("invalid collection audit row")
                rows.append(CollectionAuditRow(identifier, finished, status, count))
        self._verify_file()
        return tuple(rows)

    def series(self, ticker: str) -> OhlcvSeries | None:
        # Verify fingerprint on each query so an unexpected replacement fails closed.
        self._verify_file()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT trading_date,payload FROM bars WHERE ticker=? "
                "ORDER BY trading_date DESC LIMIT 2500",
                (ticker,),
            ).fetchall()
        if not rows:
            return None
        bars = []
        for trading_date, payload in reversed(rows):
            try:
                original = DailyBar.from_dict(json.loads(payload))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                raise OhlcvReaderError("invalid persisted daily bar") from error
            if original.ticker != ticker or original.trading_date.isoformat() != trading_date:
                raise OhlcvReaderError("bar identity mismatch")
            bars.append(
                OhlcvBar(
                    date=trading_date,
                    open=original.open,
                    high=original.high,
                    low=original.low,
                    close=original.close,
                    volume=original.volume,
                )
            )
        return OhlcvSeries(ticker=ticker, bars=tuple(bars))


class OperationalReadError(ValueError):
    pass


class OperationalTicker(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ticker: str
    latestDataDate: str


class OperationalCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schemaVersion: str = "operational_market_v1"
    evidenceStatus: str = "unreviewed"
    items: tuple[OperationalTicker, ...]
    total: int
    offset: int
    limit: int


class OperationalSnapshotSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schemaVersion: str = "operational_snapshot_summary_v1"
    evidenceStatus: str = "unreviewed"
    market: str = "KOSPI"
    observationCount: int
    tickerCount: int
    tradingDayCount: int
    firstTradingDate: str | None
    lastTradingDate: str | None


class PinnedOperationalReader:
    """Require an offline immutable copy, never the actively written operational DB."""

    def __init__(self, path: Path, *, expected_sha256: str) -> None:
        if not path.is_file() or not path.name.endswith(".snapshot.sqlite3"):
            raise OperationalReadError("explicit offline .snapshot.sqlite3 copy required")
        if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
            raise OperationalReadError("lowercase sha256 required")
        self.path = path.resolve()
        self.expected_sha256 = expected_sha256
        self._verify()
        with closing(self._connect()) as db:
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise OperationalReadError("operational snapshot integrity failure")
            if (
                db.execute("PRAGMA application_id").fetchone()[0] != OPERATIONAL_APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != 1
            ):
                raise OperationalReadError("operational snapshot identity mismatch")
            columns = {row[1] for row in db.execute("PRAGMA table_info(market_observations)")}
            if not {"market", "ticker", "trading_date", "payload", "validation_status"} <= columns:
                raise OperationalReadError("operational observation schema mismatch")

    def _verify(self) -> None:
        for suffix in ("-wal", "-journal"):
            other = Path(str(self.path) + suffix)
            if other.exists() and other.stat().st_size:
                raise OperationalReadError("active snapshot SQLite sidecar")
        with self.path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != self.expected_sha256:
            raise OperationalReadError("snapshot fingerprint mismatch")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
        db.execute("PRAGMA query_only=ON")
        return db

    def summary(self) -> OperationalSnapshotSummary:
        """Actual observed coverage, not a statement of market completeness."""
        self._verify()
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT ticker), "
                "COUNT(DISTINCT trading_date), MIN(trading_date), MAX(trading_date) "
                "FROM market_observations WHERE market='KOSPI'"
            ).fetchone()
        self._verify()
        assert row is not None
        return OperationalSnapshotSummary(
            observationCount=row[0],
            tickerCount=row[1],
            tradingDayCount=row[2],
            firstTradingDate=row[3],
            lastTradingDate=row[4],
        )

    def catalog(self, offset: int, limit: int) -> OperationalCatalog:
        self._verify()
        with closing(self._connect()) as db:
            total = db.execute(
                "SELECT COUNT(DISTINCT ticker) FROM market_observations WHERE market='KOSPI'"
            ).fetchone()[0]
            rows = db.execute(
                "SELECT ticker, MAX(trading_date) FROM market_observations "
                "WHERE market='KOSPI' GROUP BY ticker ORDER BY ticker LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        self._verify()
        return OperationalCatalog(
            items=tuple(
                OperationalTicker(ticker=ticker, latestDataDate=day) for ticker, day in rows
            ),
            total=total,
            offset=offset,
            limit=limit,
        )

    def series(self, ticker: str) -> OhlcvSeries | None:
        self._verify()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT trading_date,payload FROM market_observations "
                "WHERE market='KOSPI' AND ticker=? ORDER BY trading_date DESC LIMIT 2500",
                (ticker,),
            ).fetchall()
        self._verify()
        if not rows:
            return None
        bars = []
        for day, raw in reversed(rows):
            try:
                original = DailyBar.from_dict(json.loads(raw))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                raise OperationalReadError("invalid operational bar") from error
            if (
                original.ticker != ticker
                or original.trading_date.isoformat() != day
                or original.market != "KOSPI"
            ):
                raise OperationalReadError("operational bar identity mismatch")
            bars.append(
                OhlcvBar(
                    date=day,
                    open=original.open,
                    high=original.high,
                    low=original.low,
                    close=original.close,
                    volume=original.volume,
                )
            )
        return OhlcvSeries(ticker=ticker, bars=tuple(bars))
