"""Small provider boundary: raw retrieval and parsing are intentionally separate."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from donghak_stock_vision.data.schema import DailyBar


class ProviderError(Exception):
    """A safe, credential-free provider failure."""


@dataclass(frozen=True)
class RawPage:
    body: bytes
    collected_at: datetime
    source_date: date | None = None


class MarketDataProvider(Protocol):
    name: str
    market: str

    def fetch(self, ticker: str, start: date, end: date) -> Iterator[RawPage]: ...
    def parse(self, page: RawPage, ticker: str) -> list[DailyBar]: ...


def krx_market_scope(market: str) -> str:
    """Reserved raw/audit scope, never a ticker or a wildcard for other providers."""
    if market not in {"KOSPI", "KOSDAQ", "KONEX"}:
        raise ValueError("unsupported KRX market")
    return f"@krx_market:{market}"
