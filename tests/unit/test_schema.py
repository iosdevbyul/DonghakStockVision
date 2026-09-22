from dataclasses import replace
from datetime import date, datetime
from typing import Any

import pytest

from donghak_stock_vision.providers.fake import demo_bars
from donghak_stock_vision.validation.bars import normalize


@pytest.mark.parametrize(
    "change",
    [
        {"ticker": "5930"},
        {"ticker": "../../"},
        {"open": -1},
        {"close": 0},
        {"volume": -1},
        {"volume": True},
        {"volume": 1.5},
        {"open": None},
        {"close": float("nan")},
        {"trading_value": float("inf")},
        {"high": 95},
        {"low": 106},
        {"close": 111},
        {"trading_value": 2**63},
        {"volume": 0},
        {"trading_value": 0},
        {"open": 0},
        {"adjustment": "yes"},
        {"market": "NYSE"},
        {"provider": ""},
        {"collected_at": datetime(2024, 2, 1)},
        {"trading_date": date(2024, 1, 6)},
        {"trading_date": date(2024, 2, 1)},
        {"trading_date": datetime(2024, 1, 2)},
    ],
)
def test_invalid_values(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(demo_bars()[0], **change)


def test_no_trade_bar_and_alphanumeric_ticker() -> None:
    bar = replace(demo_bars()[0], ticker="0000A0", open=0, high=0, low=0, volume=0, trading_value=0)
    assert bar.close == 105


def test_sort_and_identical_duplicates() -> None:
    bars = demo_bars()[:5]
    result = normalize([*reversed(bars), bars[0]], "005930", date(2024, 1, 2), date(2024, 1, 8))
    assert result == bars


def test_conflicting_duplicates() -> None:
    bar = demo_bars()[0]
    with pytest.raises(ValueError, match="conflicting"):
        normalize([bar, replace(bar, close=106)], bar.ticker, bar.trading_date, bar.trading_date)


def test_wrong_ticker_and_range() -> None:
    bar = demo_bars()[0]
    with pytest.raises(ValueError, match="different ticker"):
        normalize([bar], "000660", bar.trading_date, bar.trading_date)
    with pytest.raises(ValueError, match="out-of-range"):
        normalize([bar], bar.ticker, date(2024, 1, 3), date(2024, 1, 4))


def test_extreme_jump_warned_not_fabricated(caplog: pytest.LogCaptureFixture) -> None:
    bars = demo_bars()[:2]
    bars[1] = replace(bars[1], open=200, high=220, low=180, close=210)
    result = normalize(bars, "005930", date(2024, 1, 2), date(2024, 1, 3))
    assert result == bars
    assert "price_jump" in caplog.text
