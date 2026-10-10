"""Optional, immutable SQLite reader for original KRX daily bars.

Never repairs, adjusts or synthesizes a missing market observation.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from donghak_stock_vision.data.schema import DailyBar


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
        with self._connect() as conn:
            if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
                raise OhlcvReaderError("market database integrity check failed")
            scopes = set(conn.execute(
                "SELECT DISTINCT json_extract(payload,'$.provider'), "
                "json_extract(payload,'$.market'), "
                "json_extract(payload,'$.adjustment') FROM bars"
            ))
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
            bars.append(OhlcvBar(date=trading_date, open=original.open, high=original.high,
                low=original.low, close=original.close, volume=original.volume))
        return OhlcvSeries(ticker=ticker, bars=tuple(bars))
