from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from donghak_stock_vision.backtest.clock import VirtualClock


@pytest.mark.parametrize(
    "stamp", ["2026-01-05T09:00:00+09:00", "2026-01-05T00:00:00Z", "2026-01-04T19:00:00-05:00"]
)
def test_offsets_and_same_instant_sequence(stamp: str) -> None:
    first = VirtualClock.at(stamp, 0)
    second = first.advance("2026-01-05T00:00:00Z", 1)
    assert first.cutoff == second.cutoff
    assert first.sequence == 0 and second.sequence == 1
    assert first.trading_date == date(2026, 1, 5)
    with pytest.raises(FrozenInstanceError):
        first.sequence = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "stamp,sequence",
    [("2026-01-05T00:00:00Z", 1), ("2026-01-06T00:00:00Z", 0), ("2026-01-04T23:59:59.999999Z", 2)],
)
def test_no_reversal_or_duplicate(stamp: str, sequence: int) -> None:
    clock = VirtualClock.at("2026-01-05T00:00:00Z", 1)
    with pytest.raises(ValueError):
        clock.advance(stamp, sequence)


@pytest.mark.parametrize(
    "stamp,sequence",
    [("2026-01-05T00:00:00", 0), ("2026-01-05T00:00:00Z", True), ("2026-01-05T00:00:00Z", -1)],
)
def test_explicit_aware_time_and_sequence(stamp: str, sequence: int) -> None:
    with pytest.raises(ValueError):
        VirtualClock.at(stamp, sequence)
