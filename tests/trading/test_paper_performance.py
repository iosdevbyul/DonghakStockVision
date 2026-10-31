"""PAPER report math is independent of order execution and KRX data."""

from datetime import date
from decimal import Decimal

import pytest

from donghak_stock_vision.trading.paper_performance import EquityPoint, summarize


def point(day: int, equity: str, benchmark: str | None = None) -> EquityPoint:
    return EquityPoint(
        date(2026, 10, day),
        Decimal(equity),
        Decimal(benchmark) if benchmark else None,
    )


def test_report_with_benchmark() -> None:
    result = summarize((point(1, "1000", "200"), point(2, "900", "210"), point(3, "1100", "220")))
    assert result.total_return_pct == Decimal("10.0")
    assert result.max_drawdown_pct == Decimal("10.0")
    assert result.benchmark_return_pct == Decimal("10.0")
    assert result.excess_return_pct == 0


def test_without_benchmark() -> None:
    result = summarize((point(1, "1000"),))
    assert result.total_return_pct == 0
    assert result.max_drawdown_pct == 0
    assert result.benchmark_return_pct is None


@pytest.mark.parametrize(
    "points",
    [
        (),
        (point(2, "100"), point(1, "200")),
        (point(1, "100"), point(1, "200")),
        (point(1, "0"),),
        (point(1, "100", "200"), point(2, "120")),
        (point(1, "100", "0"),),
    ],
)
def test_invalid_inputs_fail_closed(points: tuple[EquityPoint, ...]) -> None:
    with pytest.raises(ValueError):
        summarize(points)
