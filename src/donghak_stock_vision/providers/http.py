"""Bounded retries; a durable per-database Seoul-day request budget."""

import logging
import math
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.base import ProviderError

logger = logging.getLogger(__name__)


class RequestGate:
    def __init__(
        self,
        path: Path,
        interval: float = 1.0,
        daily_limit: int = 9500,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not math.isfinite(interval) or interval < 1 or not 1 <= daily_limit <= 9500:
            raise ValueError("interval must be >= 1 second; daily limit must be 1..9500")
        self.path, self.interval, self.daily_limit = path, interval, daily_limit
        self.clock, self.sleep = clock, sleep
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS request_budget "
                "(day TEXT PRIMARY KEY, count INTEGER NOT NULL, last_slot REAL NOT NULL)"
            )

    def acquire(self) -> None:
        now = self.clock()
        with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT MAX(last_slot) FROM request_budget").fetchone()[0]
            slot = max(now, previous + self.interval) if previous is not None else now
            day = datetime.fromtimestamp(slot, SEOUL).date().isoformat()
            row = connection.execute(
                "SELECT count FROM request_budget WHERE day=?", (day,)
            ).fetchone()
            count = row[0] if row else 0
            if count >= self.daily_limit:
                raise ProviderError("local daily request budget exhausted")
            connection.execute(
                "INSERT INTO request_budget VALUES (?, ?, ?) ON CONFLICT(day) "
                "DO UPDATE SET count=excluded.count, last_slot=excluded.last_slot",
                (day, count + 1, slot),
            )
        if slot > now:
            self.sleep(slot - now)


class RetryingHTTP:
    def __init__(
        self,
        client: httpx.Client,
        gate: RequestGate,
        *,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= attempts <= 5:
            raise ValueError("attempts must be 1..5")
        self.client, self.gate, self.attempts, self.sleep = client, gate, attempts, sleep

    def get(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> bytes:
        for attempt in range(self.attempts):
            self.gate.acquire()
            delay = float(2**attempt)
            try:
                response = self.client.get(url, headers=headers, params=params)
            except httpx.TransportError:
                reason = "network transport error"
            else:
                if response.status_code == 200:
                    return response.content
                reason = f"HTTP {response.status_code}"
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise ProviderError(reason)
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(
                                delay, parsedate_to_datetime(retry_after).timestamp() - time.time()
                            )
                        except (ValueError, TypeError, OverflowError):
                            pass
                    if not math.isfinite(delay) or delay > 60:
                        raise ProviderError("server requested a longer pause; retry later")
            if attempt + 1 == self.attempts:
                raise ProviderError(f"{reason}; retries exhausted") from None
            logger.warning("request_retry attempt=%d reason=%s", attempt + 1, reason)
            self.sleep(delay)
        raise AssertionError("unreachable")
