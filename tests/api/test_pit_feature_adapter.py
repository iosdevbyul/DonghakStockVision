"""PIT adapter must use the actual store and preserve source quality blockers."""

from datetime import date, datetime, timedelta, timezone

from donghak_stock_vision.api.pit_feature_adapter import build_pit_features
from donghak_stock_vision.data.schema import DailyBar


class ReadOnlyStore:
    def __init__(self, bars: list[DailyBar]) -> None:
        self.bars = bars

    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        return [
            bar for bar in self.bars if bar.ticker == ticker and start <= bar.trading_date <= end
        ]


def test_missing_independent_quality_manifest_fails_closed() -> None:
    result = build_pit_features(
        ReadOnlyStore([]),  # type: ignore[arg-type]
        "005930",
        start=date(2026, 9, 1),
        end=date(2026, 10, 8),
        as_of=datetime.fromisoformat("2026-10-10T12:00:00+09:00"),
        quality=None,
    )
    assert result.candidate is None
    assert "quality_unverified" in result.blockers
    assert not result.inference_authorized


def test_real_bars_produce_features_but_keep_quality_limitations() -> None:
    end = date(2026, 10, 8)
    dates: list[date] = []
    day = end
    while len(dates) < 12:
        if day.weekday() < 5:
            dates.append(day)
        day -= timedelta(days=1)
    dates.reverse()
    bars = []
    for index, trading_day in enumerate(dates):
        close = 10000 + 30 * index + (index % 3) * 9
        bars.append(
            DailyBar(
                ticker="005930",
                trading_date=trading_day,
                open=close - 20,
                high=close + 60,
                low=close - 60,
                close=close,
                volume=1000 + index * 3,
                trading_value=close * (1000 + index * 3),
                provider="krx",
                market="KOSPI",
                adjustment="unadjusted",
                collected_at=datetime.combine(
                    trading_day + timedelta(days=1),
                    datetime.min.time(),
                ).replace(tzinfo=timezone(timedelta(hours=9))),
            )
        )
    quality = {
        "schema_version": 1,
        "source": "independent_test_review",
        "verified_at": "2026-10-09T09:00:00+09:00",
        "sessions": [d.isoformat() for d in dates],
        "coverage": {
            "005930": {
                "start": dates[0].isoformat(),
                "end": end.isoformat(),
                "adjustment": "unadjusted",
                "excluded_dates": [],
            }
        },
    }
    result = build_pit_features(
        ReadOnlyStore(bars),  # type: ignore[arg-type]
        "005930",
        start=dates[0],
        end=end,
        as_of=datetime.fromisoformat("2026-10-10T12:00:00+09:00"),
        quality=quality,
    )
    assert result.candidate is not None
    assert len(result.candidate.featureValues) == 8
    assert result.candidate.qualityFlags
    assert not result.inference_authorized
