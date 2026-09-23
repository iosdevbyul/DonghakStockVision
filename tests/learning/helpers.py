import math
import random
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.ingestion.pipeline import Pipeline
from donghak_stock_vision.providers.fake import FakeProvider
from donghak_stock_vision.storage.sqlite import SQLiteStore

CUTOFF = datetime(2024, 2, 1, tzinfo=UTC)


def bars(count: int = 240, tickers: int = 2, bulk: bool = True) -> list[DailyBar]:
    result = []
    for ticker in range(tickers):
        rng = random.Random(42 + ticker)
        price = 10000
        day = date(2020, 1, 2)
        for _ in range(count):
            while day.weekday() > 4:
                day += timedelta(days=1)
            opening = price
            price = max(100, round(price * math.exp(rng.gauss(0, 0.014))))
            volume = rng.randint(10000, 100000)
            collected = (
                CUTOFF - timedelta(days=1)
                if bulk
                else datetime.combine(day + timedelta(days=1), time(8), UTC)
            )
            result.append(
                DailyBar(
                    f"{ticker + 1:06d}",
                    day,
                    opening,
                    max(opening, price) + 10,
                    min(opening, price) - 10,
                    price,
                    volume,
                    volume * (price + opening) // 2,
                    "fake",
                    "KOSPI",
                    "unadjusted",
                    collected,
                )
            )
            day += timedelta(days=1)
    return result


def seed(store: SQLiteStore, source: list[DailyBar]) -> None:
    result = Pipeline(FakeProvider(source), store).collect(
        sorted({b.ticker for b in source}),
        min(b.trading_date for b in source),
        max(b.trading_date for b in source),
    )
    assert result.failed == 0


def quality(source: list[DailyBar]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "synthetic_fixture_not_market_evidence",
        "verified_at": CUTOFF.isoformat(),
        "sessions": sorted({b.trading_date.isoformat() for b in source}),
        "coverage": {
            t: {
                "start": min(b.trading_date for b in source).isoformat(),
                "end": max(b.trading_date for b in source).isoformat(),
                "adjustment": "unadjusted",
                "excluded_dates": [],
            }
            for t in {b.ticker for b in source}
        },
    }
