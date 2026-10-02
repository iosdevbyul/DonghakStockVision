"""KRX market/day collection: one page, full validation, one atomic cleaned save."""

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import uuid4

from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_range, validate_ticker
from donghak_stock_vision.ingestion.pipeline import CollectionResult
from donghak_stock_vision.providers.base import ProviderError, krx_market_scope
from donghak_stock_vision.providers.krx import KRXProvider
from donghak_stock_vision.storage.base import MarketDataStore
from donghak_stock_vision.validation.bars import normalize

logger = logging.getLogger(__name__)


@dataclass
class MarketCollectionResult(CollectionResult):
    """Success/failure/empty count market dates; rows count valid deduplicated bars."""

    rows: int = 0


class KRXMarketPipeline:
    def __init__(self, provider: KRXProvider, store: MarketDataStore) -> None:
        if not isinstance(provider, KRXProvider):
            raise ValueError("whole-market collection requires KRXProvider")
        self.provider, self.store = provider, store

    def collect(
        self, start: date, end: date, *, tickers: Sequence[str] | None = None
    ) -> MarketCollectionResult:
        """Optionally retain an explicit universe after validating the whole market page.

        Raw audit pages still contain the complete market response. Empty counts refer
        to retained bars, not evidence that the exchange was closed.
        """
        validate_range(start, end)
        if tickers is not None:
            if isinstance(tickers, (str, bytes)) or not tickers:
                raise ValueError("tickers must be a nonempty sequence of KRX codes")
            for ticker in tickers:
                validate_ticker(ticker)
        universe = frozenset(tickers) if tickers is not None else None
        if end >= datetime.now(SEOUL).date():
            raise ValueError("end must be before today in Asia/Seoul; final daily bars only")
        scope = krx_market_scope(self.provider.market)
        result = MarketCollectionResult()
        run_id = uuid4().hex
        day = start
        while day <= end:
            if day.weekday() >= 5:
                day += timedelta(days=1)
                continue
            try:
                # No ticker iteration can perform network I/O. Archive once even if parsing fails.
                page = self.provider.fetch_market(day)
                raw_id = self.store.archive(run_id, self.provider.name, scope, page)
                if page.source_date != day:
                    raise ValueError("source date mismatch")
                grouped: dict[str, list[DailyBar]] = {}
                for bar in self.provider.parse_market(page):
                    grouped.setdefault(bar.ticker, []).append(bar)
                cleaned = [
                    bar
                    for ticker in sorted(grouped)
                    for bar in normalize(grouped[ticker], ticker, day, day)
                ]
                if any(
                    b.provider != self.provider.name or b.market != self.provider.market
                    for b in cleaned
                ):
                    raise ValueError("provider metadata mismatch")
                if universe is not None:
                    cleaned = [bar for bar in cleaned if bar.ticker in universe]
                # Existing SQLite transaction covers every ticker on this date.
                changed = self.store.save(cleaned, [raw_id])
            except (ProviderError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                category = type(error).__name__
                self.store.record_run(run_id, scope, day, day, "failed", 0, category)
                logger.error(
                    "market_collection_failed market=%s date=%s category=%s",
                    self.provider.market,
                    day,
                    category,
                )
                result.failed += 1
            else:
                status = "success" if cleaned else "empty"
                self.store.record_run(run_id, scope, day, day, status, len(cleaned), None)
                result.succeeded += 1
                result.empty += not cleaned
                result.rows += len(cleaned)
                result.changed += changed
                logger.info(
                    "market_collection_%s market=%s date=%s rows=%d changed=%d",
                    status,
                    self.provider.market,
                    day,
                    len(cleaned),
                    changed,
                )
            day += timedelta(days=1)
        logger.info(
            "market_collection_summary succeeded=%d failed=%d empty=%d rows=%d changed=%d",
            result.succeeded,
            result.failed,
            result.empty,
            result.rows,
            result.changed,
        )
        return result
