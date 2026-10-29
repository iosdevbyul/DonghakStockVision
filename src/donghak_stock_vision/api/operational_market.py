"""Opt-in, SHA-pinned SQLite snapshot reader for collected operational KRX observations.

This transport does not grant research, training or trading eligibility.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from donghak_stock_vision.api.ohlcv import OhlcvBar, OhlcvSeries
from donghak_stock_vision.data.schema import DailyBar


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
            items=tuple(OperationalTicker(ticker=ticker, latestDataDate=day) for ticker, day in rows),
            total=total, offset=offset, limit=limit,
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
            if original.ticker != ticker or original.trading_date.isoformat() != day or original.market != "KOSPI":
                raise OperationalReadError("operational bar identity mismatch")
            bars.append(OhlcvBar(date=day, open=original.open, high=original.high,
                                 low=original.low, close=original.close, volume=original.volume))
        return OhlcvSeries(ticker=ticker, bars=tuple(bars))
