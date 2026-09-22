"""Storage boundary for a future database adapter."""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.providers.base import RawPage


class MarketDataStore(Protocol):
    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]: ...
    def latest(self, ticker: str, start: date, end: date) -> date | None: ...
    def archive(self, run_id: str, provider: str, ticker: str, page: RawPage) -> int: ...
    def save(self, bars: Sequence[DailyBar], raw_ids: Sequence[int]) -> int: ...
    def record_run(
        self,
        run_id: str,
        ticker: str,
        start: date,
        end: date,
        status: str,
        rows: int,
        error: str | None,
    ) -> None: ...
    def check_integrity(self) -> list[str]: ...
