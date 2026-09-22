"""Exact KRW/share values; trading dates are Asia/Seoul calendar dates."""

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
Adjustment = Literal["unadjusted", "adjusted", "unknown"]
MAX_INTEGER = 2**63 - 1


def validate_ticker(ticker: str) -> None:
    # KRX also issues alphanumeric six-character short codes.
    if not isinstance(ticker, str) or not re.fullmatch(r"[0-9A-Z]{6}", ticker):
        raise ValueError("ticker must be a six-character KRX short code")


def validate_range(start: date, end: date) -> None:
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("expected calendar dates with start <= end")


@dataclass(frozen=True)
class DailyBar:
    ticker: str
    trading_date: date
    open: int
    high: int
    low: int
    close: int
    volume: int
    trading_value: int
    provider: str
    market: str
    adjustment: Adjustment
    collected_at: datetime

    def __post_init__(self) -> None:
        validate_ticker(self.ticker)
        if type(self.trading_date) is not date:
            raise ValueError("trading_date must be a date, not a timestamp")
        if (
            not isinstance(self.collected_at, datetime)
            or self.collected_at.tzinfo is None
            or self.collected_at.utcoffset() is None
        ):
            raise ValueError("collected_at must have an explicit timezone")
        if self.trading_date >= self.collected_at.astimezone(SEOUL).date():
            raise ValueError("only past Seoul trading dates are supported")
        if self.trading_date.weekday() >= 5:
            raise ValueError("weekend bars are not supported")
        if (
            not isinstance(self.provider, str)
            or not self.provider.strip()
            or self.market not in {"KOSPI", "KOSDAQ", "KONEX"}
        ):
            raise ValueError("provider and a supported market are required")
        if self.adjustment not in {"unadjusted", "adjusted", "unknown"}:
            raise ValueError("invalid adjustment status")
        for name in ("open", "high", "low", "close", "volume", "trading_value"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= MAX_INTEGER:
                raise ValueError(f"{name} must be a nonnegative signed-64-bit integer")
        if self.close <= 0:
            raise ValueError("close must be positive")
        # KRX can publish zero OHLC except a reference close on no-trade days.
        no_trade = self.volume == self.trading_value == 0
        zero_ohl = self.open == self.high == self.low == 0
        if not (no_trade and zero_ohl):
            if self.low <= 0 or not self.low <= self.open <= self.high:
                raise ValueError("invalid open/high/low relationship")
            if not self.low <= self.close <= self.high:
                raise ValueError("close must be within low/high")
        if (self.volume == 0) != (self.trading_value == 0):
            raise ValueError("zero volume and zero trading_value must agree")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["trading_date"] = self.trading_date.isoformat()
        result["collected_at"] = self.collected_at.astimezone(UTC).isoformat()
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DailyBar":
        data = dict(value)
        data["trading_date"] = date.fromisoformat(data["trading_date"])
        data["collected_at"] = datetime.fromisoformat(data["collected_at"])
        return cls(**data)

    def content(self) -> dict[str, Any]:
        """Identity/value comparison ignores a repeated retrieval's timestamp."""
        return {key: value for key, value in self.to_dict().items() if key != "collected_at"}
