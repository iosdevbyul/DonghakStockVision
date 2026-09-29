"""Atomic cleaned writes, append-only raw payloads and collection audit trail."""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.providers.base import RawPage, krx_market_scope

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_pages (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, provider TEXT NOT NULL,
    ticker TEXT NOT NULL, collected_at TEXT NOT NULL, source_date TEXT,
    sha256 TEXT NOT NULL, body BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS bars (
    ticker TEXT NOT NULL, trading_date TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(ticker, trading_date)
);
CREATE TABLE IF NOT EXISTS bar_sources (
    ticker TEXT NOT NULL, trading_date TEXT NOT NULL, raw_id INTEGER NOT NULL,
    PRIMARY KEY(ticker, trading_date, raw_id),
    FOREIGN KEY(ticker, trading_date) REFERENCES bars(ticker, trading_date),
    FOREIGN KEY(raw_id) REFERENCES raw_pages(id)
);
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, finished_at TEXT NOT NULL,
    status TEXT NOT NULL, rows INTEGER NOT NULL, error TEXT
);
"""


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        rows = self.connection.execute(
            "SELECT payload FROM bars WHERE ticker=? AND trading_date BETWEEN ? AND ? "
            "ORDER BY trading_date",
            (ticker, start.isoformat(), end.isoformat()),
        )
        return [DailyBar.from_dict(json.loads(row[0])) for row in rows]

    def latest(self, ticker: str, start: date, end: date) -> date | None:
        row = self.connection.execute(
            "SELECT MAX(trading_date) FROM bars WHERE ticker=? AND trading_date BETWEEN ? AND ?",
            (ticker, start.isoformat(), end.isoformat()),
        ).fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def archive(self, run_id: str, provider: str, ticker: str, page: RawPage) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO raw_pages "
                "(run_id,provider,ticker,collected_at,source_date,sha256,body) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    provider,
                    ticker,
                    page.collected_at.astimezone(UTC).isoformat(),
                    page.source_date.isoformat() if page.source_date else None,
                    hashlib.sha256(page.body).hexdigest(),
                    page.body,
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def save(self, bars: Sequence[DailyBar], raw_ids: Sequence[int]) -> int:
        changed = 0
        if bars and not raw_ids:
            raise ValueError("raw provenance is required")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            sources: dict[str | None, list[tuple[int, str, str]]] = {}
            for raw_id in raw_ids:
                source = self.connection.execute(
                    "SELECT source_date,provider,ticker FROM raw_pages WHERE id=?",
                    (raw_id,),
                ).fetchone()
                if source is None:
                    raise ValueError("raw provenance missing")
                sources.setdefault(source[0], []).append((raw_id, source[1], source[2]))
            for bar in bars:
                series = self.connection.execute(
                    "SELECT payload FROM bars WHERE ticker=? LIMIT 1",
                    (bar.ticker,),
                ).fetchone()
                if series:
                    sample = DailyBar.from_dict(json.loads(series[0]))
                    if (sample.provider, sample.market, sample.adjustment) != (
                        bar.provider,
                        bar.market,
                        bar.adjustment,
                    ):
                        raise ValueError("cannot mix provider/market/adjustment; use a separate DB")
                bar.__post_init__()
                key = (bar.ticker, bar.trading_date.isoformat())
                existing = self.connection.execute(
                    "SELECT payload FROM bars WHERE ticker=? AND trading_date=?",
                    key,
                ).fetchone()
                if existing:
                    old = DailyBar.from_dict(json.loads(existing[0]))
                    if (old.provider, old.market, old.adjustment) != (
                        bar.provider,
                        bar.market,
                        bar.adjustment,
                    ):
                        raise ValueError("cannot mix provider/market/adjustment; use a separate DB")
                    if old.content() == bar.content():
                        continue
                    if bar.collected_at <= old.collected_at:
                        raise ValueError("refusing stale or ambiguously timed revision")
                self.connection.execute(
                    "INSERT INTO bars VALUES(?,?,?) ON CONFLICT(ticker,trading_date) "
                    "DO UPDATE SET payload=excluded.payload",
                    (*key, json.dumps(bar.to_dict())),
                )
                matching = [
                    raw_id
                    for source_date in (None, key[1])
                    for raw_id, provider, ticker in sources.get(source_date, [])
                    if provider == bar.provider
                    and (
                        ticker == bar.ticker
                        or (
                            bar.provider == "krx"
                            and source_date == key[1]
                            and ticker == krx_market_scope(bar.market)
                        )
                    )
                ]
                if not matching:
                    raise ValueError("no matching raw provenance for bar")
                for raw_id in matching:
                    self.connection.execute(
                        "INSERT OR IGNORE INTO bar_sources VALUES(?,?,?)",
                        (*key, raw_id),
                    )
                changed += 1
        return changed

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
        with self.connection:
            self.connection.execute(
                "INSERT INTO collection_runs "
                "(run_id,ticker,start_date,end_date,finished_at,status,rows,error) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    ticker,
                    start.isoformat(),
                    end.isoformat(),
                    datetime.now(UTC).isoformat(),
                    status,
                    rows,
                    error,
                ),
            )

    def check_integrity(self) -> list[str]:
        issues = []
        for row in self.connection.execute("PRAGMA integrity_check"):
            if row[0] != "ok":
                issues.append("SQLite integrity failure")
        if self.connection.execute("PRAGMA foreign_key_check").fetchall():
            issues.append("foreign key violation")
        previous: dict[str, DailyBar] = {}
        for ticker, day, payload in self.connection.execute(
            "SELECT ticker,trading_date,payload FROM bars ORDER BY ticker,trading_date"
        ):
            try:
                bar = DailyBar.from_dict(json.loads(payload))
                if (bar.ticker, bar.trading_date.isoformat()) != (ticker, day):
                    raise ValueError("key mismatch")
                prior = previous.get(ticker)
                if prior and (prior.provider, prior.market, prior.adjustment) != (
                    bar.provider,
                    bar.market,
                    bar.adjustment,
                ):
                    raise ValueError("mixed series metadata")
                previous[ticker] = bar
            except (ValueError, TypeError, KeyError, AttributeError):
                issues.append(f"invalid bar: {ticker}/{day}")
        for raw_id, body, digest in self.connection.execute("SELECT id,body,sha256 FROM raw_pages"):
            if hashlib.sha256(body).hexdigest() != digest:
                issues.append(f"raw checksum mismatch: {raw_id}")
        missing = self.connection.execute(
            "SELECT b.ticker,b.trading_date FROM bars b LEFT JOIN bar_sources s "
            "ON b.ticker=s.ticker AND b.trading_date=s.trading_date WHERE s.raw_id IS NULL"
        )
        issues.extend(f"missing raw provenance: {ticker}/{day}" for ticker, day in missing)
        return issues
