"""Batch validation and deterministic normalization, without filling missing data."""

import logging
from collections.abc import Iterable
from datetime import date

from donghak_stock_vision.data.schema import DailyBar, validate_range, validate_ticker

logger = logging.getLogger(__name__)


def normalize(bars: Iterable[DailyBar], ticker: str, start: date, end: date) -> list[DailyBar]:
    validate_ticker(ticker)
    validate_range(start, end)
    unique: dict[date, DailyBar] = {}
    for bar in bars:
        bar.__post_init__()
        if bar.ticker != ticker or not start <= bar.trading_date <= end:
            raise ValueError("provider returned a different ticker or an out-of-range date")
        previous = unique.get(bar.trading_date)
        if previous is not None and previous.content() != bar.content():
            raise ValueError("conflicting duplicate bar")
        unique.setdefault(bar.trading_date, bar)
    result = sorted(unique.values(), key=lambda bar: bar.trading_date)
    if len({(b.provider, b.market, b.adjustment) for b in result}) > 1:
        raise ValueError("mixed provider, market or adjustment in one batch")
    for previous, current in zip(result, result[1:], strict=False):
        ratio = current.close / previous.close
        if ratio > 1.35 or ratio < 0.65:
            # Corporate actions and listing events can legitimately cause large gaps.
            logger.warning("price_jump ticker=%s date=%s", ticker, current.trading_date)
    return result
