"""Pure, offline PAPER equity-curve metrics. No price sourcing or trade authority."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class EquityPoint:
    trading_date: date
    equity: Decimal
    benchmark: Decimal | None = None


@dataclass(frozen=True)
class PerformanceSummary:
    first_date: date
    last_date: date
    observations: int
    initial_equity: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    benchmark_return_pct: Decimal | None
    excess_return_pct: Decimal | None


def summarize(points: tuple[EquityPoint, ...]) -> PerformanceSummary:
    """Compute observed returns only; prices, fees and trades are external inputs."""
    if not points:
        raise ValueError("at least one observed valuation is required")
    previous: date | None = None
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    benchmark_count = sum(p.benchmark is not None for p in points)
    if benchmark_count not in (0, len(points)):
        raise ValueError("benchmark series must be complete or entirely absent")
    for point in points:
        if previous is not None and point.trading_date <= previous:
            raise ValueError("valuation dates must be strictly increasing")
        if not point.equity.is_finite() or point.equity <= 0:
            raise ValueError("equity must be finite and positive")
        if point.benchmark is not None and (
            not point.benchmark.is_finite() or point.benchmark <= 0
        ):
            raise ValueError("benchmark must be finite and positive")
        peak = max(peak, point.equity)
        max_drawdown = max(max_drawdown, (peak - point.equity) / peak * 100)
        previous = point.trading_date
    initial = points[0].equity
    final = points[-1].equity
    total = (final / initial - 1) * 100
    benchmark_return = None
    excess = None
    if benchmark_count:
        start = points[0].benchmark
        end = points[-1].benchmark
        assert start is not None and end is not None
        benchmark_return = (end / start - 1) * 100
        excess = total - benchmark_return
    return PerformanceSummary(
        first_date=points[0].trading_date,
        last_date=points[-1].trading_date,
        observations=len(points),
        initial_equity=initial,
        final_equity=final,
        total_return_pct=total,
        max_drawdown_pct=max_drawdown,
        benchmark_return_pct=benchmark_return,
        excess_return_pct=excess,
    )
