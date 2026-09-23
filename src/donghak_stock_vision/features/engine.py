"""Eight causal features, quality segmentation and explicit availability failures."""

import math
import statistics
from datetime import datetime
from typing import Any

from donghak_stock_vision.data.learning import AnalysisError
from donghak_stock_vision.data.schema import DailyBar


def check_segment(bars: list[DailyBar], snapshot: dict[str, Any]) -> None:
    if not bars:
        raise AnalysisError("insufficient_history")
    quality = snapshot["quality"]
    entry = quality["coverage"].get(bars[0].ticker) if quality else None
    excluded = set(entry["excluded_dates"]) if entry else set()
    first, last = bars[0].trading_date.isoformat(), bars[-1].trading_date.isoformat()
    if any(first <= d <= last for d in excluded):
        raise AnalysisError("excluded_corporate_action")
    if any(first <= d <= last for d in snapshot["unavailable_dates"].get(bars[0].ticker, [])):
        raise AnalysisError("insufficient_point_in_time_data")
    if snapshot["session_basis"] == "verified_sessions":
        expected = [d for d in quality["sessions"] if first <= d <= last]
        if expected != [b.trading_date.isoformat() for b in bars]:
            raise AnalysisError("missing_verified_session")
    for i, bar in enumerate(bars):
        if min(bar.open, bar.high, bar.low, bar.close, bar.volume, bar.trading_value) <= 0:
            raise AnalysisError("no_trade_bar")
        if i:
            previous = bars[i - 1]
            if bar.trading_date <= previous.trading_date:
                raise AnalysisError("invalid_market_order_or_duplicates")
            if (
                snapshot["session_basis"] != "verified_sessions"
                and (bar.trading_date - previous.trading_date).days > 7
            ):
                raise AnalysisError("suspected_gap")
            if abs(math.log(bar.close / previous.close)) > 0.20:
                raise AnalysisError("suspected_discontinuity")


def feature_vector(
    history: list[DailyBar],
    snapshot: dict[str, Any],
    as_of: datetime | None = None,
) -> tuple[float, ...]:
    if len(history) < 11:
        raise AnalysisError("insufficient_history")
    # Include the previous edge in the quality check: an event at window start must reset warmup.
    check_segment(history[-12:], snapshot)
    bars = history[-11:]
    if as_of is not None and any(b.collected_at > as_of for b in bars):
        raise AnalysisError("insufficient_point_in_time_data")
    close = bars[-1].close
    returns = [math.log(b.close / a.close) for a, b in zip(bars, bars[1:], strict=False)]
    values = (
        math.log(close / bars[-2].close),
        math.log(close / bars[-4].close),
        math.log(close / bars[-6].close),
        close / statistics.mean(b.close for b in bars[-10:]) - 1,
        statistics.stdev(returns),
        (bars[-1].high - bars[-1].low) / close,
        math.log(bars[-1].volume / statistics.mean(b.volume for b in bars[:-1])),
        math.log(bars[-1].trading_value / statistics.mean(b.trading_value for b in bars[:-1])),
    )
    if not all(math.isfinite(v) for v in values):
        raise AnalysisError("nonfinite_features")
    if values[4] <= 0:
        raise AnalysisError("constant_volatility")
    return values
