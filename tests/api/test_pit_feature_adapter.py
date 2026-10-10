"""PIT adapter must use the actual store and preserve source quality blockers."""

from datetime import date, datetime

from donghak_stock_vision.api.pit_feature_adapter import build_pit_features
from donghak_stock_vision.data.schema import DailyBar


class ReadOnlyStore:
    def __init__(self, bars: list[DailyBar]) -> None:
        self.bars = bars

    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        return [
            bar
            for bar in self.bars
            if bar.ticker == ticker and start <= bar.trading_date <= end
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
