"""Explicitly synthetic, offline provider; never a fallback for live failures."""

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime

from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.providers.base import RawPage


class FakeProvider:
    name = "fake"
    market = "KOSPI"

    def __init__(self, bars: Sequence[DailyBar] | None = None) -> None:
        self.bars = list(bars) if bars is not None else demo_bars()
        self.calls: list[tuple[str, date, date]] = []

    def fetch(self, ticker: str, start: date, end: date) -> Iterator[RawPage]:
        self.calls.append((ticker, start, end))
        rows = [
            bar.to_dict()
            for bar in self.bars
            if bar.ticker == ticker and start <= bar.trading_date <= end
        ]
        yield RawPage(json.dumps(rows).encode(), datetime.now(UTC))

    def parse(self, page: RawPage, ticker: str) -> list[DailyBar]:
        return [DailyBar.from_dict(row) for row in json.loads(page.body)]


def demo_bars() -> list[DailyBar]:
    """Small fixed fixture, unrelated to real historical prices."""
    return [
        DailyBar(
            ticker,
            date(2024, 1, day),
            100,
            110,
            90,
            105,
            10,
            1020,
            "fake",
            "KOSPI",
            "unadjusted",
            datetime(2024, 2, 1, tzinfo=UTC),
        )
        for ticker in ("005930", "000660")
        for day in (2, 3, 4, 5, 8)
    ]
