"""Causal research indicators; closed observed periods are not authenticated sessions."""

import math
import statistics as st
from datetime import datetime
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.features.engine import check_segment, feature_vector

DAILY = (
    "return1",
    "return3",
    "return5",
    "sma10_gap",
    "volatility10",
    "range",
    "relative_volume10",
    "relative_value10",
    "return20",
    "sma20_gap",
    "ema12_gap",
    "ema26_gap",
    "ma_distance",
    "sma_slope",
    "trend_persistence",
    "rsi14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "bollinger_position",
    "bollinger_width",
    "atr14",
    "volatility_change",
    "volume_change",
    "volume_ma_ratio",
    "value_change",
    "value_ma_ratio",
    "volume_return_interaction",
    "body",
    "upper_wick",
    "lower_wick",
    "signed_streak",
    "gap",
    "reversal_candidate",
)
PERIOD = ("return", "range", "body", "upper", "lower", "volume_share")
FEATURES_V2 = DAILY + tuple(f"{p}_{v}" for p in ("week", "month") for v in PERIOD)
VERSION = "causal_daily_closed_observed_periods_v2"
GROUPS = {
    "legacy": tuple(range(8)),
    "daily": tuple(range(len(DAILY))),
    "weekly": tuple(range(len(DAILY), len(DAILY) + 6)),
    "monthly": tuple(range(len(DAILY) + 6, len(FEATURES_V2))),
}
GROUPS.update(
    daily_weekly=GROUPS["daily"] + GROUPS["weekly"],
    daily_monthly=GROUPS["daily"] + GROUPS["monthly"],
    multi=tuple(range(len(FEATURES_V2))),
)


def ema(values: list[float], period: int) -> list[float]:
    result = [values[0]]
    for v in values[1:]:
        result.append(result[-1] + 2 / (period + 1) * (v - result[-1]))
    return result


def period_key(b: DailyBar, period: str) -> tuple[int, int]:
    if period == "week":
        y, w, _ = b.trading_date.isocalendar()
        return y, w
    return b.trading_date.year, b.trading_date.month


def closed_period(history: list[DailyBar], period: str) -> list[DailyBar]:
    current = period_key(history[-1], period)
    first = period_key(history[0], period)
    keys = sorted({period_key(b, period) for b in history if period_key(b, period) < current})
    # The first retained period might begin before retention: never call it complete.
    keys = [k for k in keys if k != first]
    require(bool(keys), "research_incomplete_higher_timeframe")
    return [b for b in history if period_key(b, period) == keys[-1]]


def vector(history: list[DailyBar], snapshot: dict[str, Any], as_of: datetime) -> tuple[float, ...]:
    require(len(history) >= 35, "research_indicator_warmup")
    daily = history[-35:]
    periods = [closed_period(history, p) for p in ("week", "month")]
    used = {b.trading_date: b for b in daily + periods[0] + periods[1]}
    require(all(b.collected_at <= as_of for b in used.values()), "research_input_not_available")
    check_segment(sorted(used.values(), key=lambda b: b.trading_date), snapshot)
    c = [float(b.close) for b in daily]
    ret = [math.log(b / a) for a, b in zip(c, c[1:], strict=False)]
    b, previous = daily[-1], daily[-2]
    ma = st.mean(c[-20:])
    sd = st.pstdev(c[-20:])
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [a - z for a, z in zip(e12, e26, strict=True)]
    signal = ema(macd, 9)[-1]
    gains = st.mean(max(0, r) for r in ret[-14:])
    losses = st.mean(max(0, -r) for r in ret[-14:])
    rsi = gains / (gains + losses) if gains + losses else 0.5
    tr = [
        max(y.high - y.low, abs(y.high - x.close), abs(y.low - x.close))
        for x, y in zip(daily, daily[1:], strict=False)
    ]
    streak = 0
    sign = 1 if b.close >= b.open else -1
    for row in reversed(daily):
        if (1 if row.close >= row.open else -1) != sign:
            break
        streak += sign
    values = feature_vector(history, snapshot, as_of) + (
        math.log(c[-1] / c[-21]),
        c[-1] / ma - 1,
        c[-1] / e12[-1] - 1,
        c[-1] / e26[-1] - 1,
        e12[-1] / e26[-1] - 1,
        ma / st.mean(c[-21:-1]) - 1,
        sum(r > 0 for r in ret[-20:]) / 20,
        rsi,
        macd[-1] / c[-1],
        signal / c[-1],
        (macd[-1] - signal) / c[-1],
        (c[-1] - ma) / (2 * sd) if sd else 0,
        4 * sd / ma,
        st.mean(tr[-14:]) / c[-1],
        st.pstdev(ret[-5:]) / max(st.pstdev(ret[-20:]), 1e-12) - 1,
        math.log(b.volume / previous.volume),
        b.volume / st.mean(v.volume for v in daily[-20:]),
        math.log(b.trading_value / previous.trading_value),
        b.trading_value / st.mean(v.trading_value for v in daily[-20:]),
        ret[-1] * b.volume / st.mean(v.volume for v in daily[-20:]),
        (b.close - b.open) / b.close,
        (b.high - max(b.open, b.close)) / b.close,
        (min(b.open, b.close) - b.low) / b.close,
        streak / 35,
        b.open / previous.close - 1,
        float((b.close - b.open) * (previous.close - previous.open) < 0),
    )
    for rows in periods:
        o, close = rows[0].open, rows[-1].close
        high, low = max(r.high for r in rows), min(r.low for r in rows)
        values += (
            math.log(close / o),
            (high - low) / close,
            (close - o) / close,
            (high - max(o, close)) / close,
            (min(o, close) - low) / close,
            rows[-1].volume / sum(r.volume for r in rows),
        )
    require(
        len(values) == len(FEATURES_V2) and all(math.isfinite(v) for v in values),
        "research_feature_shape_or_finiteness",
    )
    return values
