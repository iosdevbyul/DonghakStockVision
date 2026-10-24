"""Research-only historical access metadata; never an approval or a synthetic execution request."""

from dataclasses import dataclass
from datetime import timedelta

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.official_evidence import normalize_month
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.trading.domain import Instrument, Market
from donghak_stock_vision.trading.paper_types import wire


@dataclass(frozen=True)
class HistoricalReadScope:
    instrument: Instrument
    window: ResearchDateRange
    partition: str
    split: ExplicitResearchSplit
    trusted_split_hash: str
    trusted_database_hash: str
    max_rows: int = 64
    max_raw_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        require(type(self.instrument) is Instrument, "instrument_owner_required")
        self.instrument.__post_init__()
        require(self.instrument.market is Market.KR, "historical_us_source_unavailable")
        require(
            type(self.window) is ResearchDateRange and type(self.split) is ExplicitResearchSplit,
            "original_split_owners_required",
        )
        self.window.__post_init__()
        self.split.__post_init__()
        require(self.split.identifier == sha(self.trusted_split_hash), "split_pin_mismatch")
        require(self.partition in ("training", "tuning"), "historical_partition_forbidden")
        span = self.split.training if self.partition == "training" else self.split.validation
        require(
            span.start <= self.window.start <= self.window.end <= span.end,
            "outside_authorized_partition",
        )
        require(self.window.end - self.window.start <= timedelta(days=366), "window_budget")
        sha(self.trusted_database_hash)
        require(type(self.max_rows) is int and 0 < self.max_rows <= 64, "row_budget")
        require(
            type(self.max_raw_bytes) is int and 0 < self.max_raw_bytes <= 8 * 1024 * 1024,
            "raw_byte_budget",
        )

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "historical_read_scope_v1",
                "instrument": wire(self.instrument),
                "window": self.window.to_dict(),
                "partition": self.partition,
                "split_hash": self.trusted_split_hash,
                "database_hash": self.trusted_database_hash,
                "max_rows": self.max_rows,
                "max_raw_bytes": self.max_raw_bytes,
                "authorization_scope": "readiness_inspection_not_execution_or_pit_approval",
            }
        )


@dataclass(frozen=True)
class CalendarMonthSource:
    raw: bytes
    source: FrozenJSON
    trusted_normalized_hash: str

    def verify(self) -> FrozenJSON:
        require(type(self.raw) is bytes and len(self.raw) <= 1024 * 1024, "calendar_byte_budget")
        require(type(self.source) is FrozenJSON, "calendar_source_owner_required")
        normalized = normalize_month(self.raw, self.source.to_dict())
        require(normalized.identifier == sha(self.trusted_normalized_hash), "calendar_pin_mismatch")
        return normalized


@dataclass(frozen=True)
class HistoricalReadinessResult:
    report: FrozenJSON
    observations: FrozenJSON
