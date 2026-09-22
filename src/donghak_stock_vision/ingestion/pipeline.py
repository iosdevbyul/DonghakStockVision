"""Collect each ticker atomically; a bad ticker does not prevent other tickers."""

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from uuid import uuid4

from donghak_stock_vision.data.schema import SEOUL, validate_range, validate_ticker
from donghak_stock_vision.providers.base import MarketDataProvider, ProviderError
from donghak_stock_vision.storage.base import MarketDataStore
from donghak_stock_vision.validation.bars import normalize

logger = logging.getLogger(__name__)


@dataclass
class CollectionResult:
    succeeded: int = 0
    failed: int = 0
    empty: int = 0
    changed: int = 0


class Pipeline:
    def __init__(self, provider: MarketDataProvider, store: MarketDataStore) -> None:
        self.provider, self.store = provider, store

    def collect(
        self,
        tickers: Sequence[str],
        start: Date,
        end: Date,
        *,
        incremental: bool = False,
        overlap_days: int = 7,
    ) -> CollectionResult:
        validate_range(start, end)
        if end >= datetime.now(SEOUL).date():
            raise ValueError("end must be before today in Asia/Seoul; final daily bars only")
        if not tickers or overlap_days < 0:
            raise ValueError("at least one ticker and nonnegative overlap_days are required")
        for ticker in tickers:
            validate_ticker(ticker)
        result = CollectionResult()
        run_id = uuid4().hex
        for ticker in dict.fromkeys(tickers):
            effective_start = start
            if incremental:
                latest = self.store.latest(ticker, start, end)
                if latest is not None:
                    effective_start = max(start, latest - timedelta(days=overlap_days))
            try:
                bars, raw_ids = [], []
                for page in self.provider.fetch(ticker, effective_start, end):
                    raw_ids.append(self.store.archive(run_id, self.provider.name, ticker, page))
                    bars.extend(self.provider.parse(page, ticker))
                cleaned = normalize(bars, ticker, effective_start, end)
                if any(
                    b.provider != self.provider.name or b.market != self.provider.market
                    for b in cleaned
                ):
                    raise ValueError("provider metadata mismatch")
                changed = self.store.save(cleaned, raw_ids)
            except (ProviderError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                # Never persist arbitrary exception strings or server echoes of AUTH_KEY.
                category = type(error).__name__
                self.store.record_run(run_id, ticker, effective_start, end, "failed", 0, category)
                logger.error("collection_failed ticker=%s category=%s", ticker, category)
                result.failed += 1
                continue
            status = "success" if cleaned else "empty"
            self.store.record_run(run_id, ticker, effective_start, end, status, len(cleaned), None)
            result.succeeded += 1
            result.empty += not cleaned
            result.changed += changed
            logger.info(
                "collection_%s ticker=%s rows=%d changed=%d", status, ticker, len(cleaned), changed
            )
        logger.info(
            "collection_summary succeeded=%d failed=%d empty=%d changed=%d",
            result.succeeded,
            result.failed,
            result.empty,
            result.changed,
        )
        return result
