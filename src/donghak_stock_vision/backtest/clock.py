"""An explicit, immutable observation cursor; no wall clock or runner."""

from dataclasses import dataclass
from datetime import date, datetime

from donghak_stock_vision.backtest.validation import instant, integer, require, utc
from donghak_stock_vision.data.schema import SEOUL


@dataclass(frozen=True)
class VirtualClock:
    cutoff: str
    sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "cutoff", instant(self.cutoff))
        integer(self.sequence)

    @classmethod
    def at(cls, cutoff: str | datetime, sequence: int) -> "VirtualClock":
        return cls(instant(cutoff), sequence)

    @property
    def trading_date(self) -> date:
        return utc(self.cutoff).astimezone(SEOUL).date()

    def advance(self, cutoff: str | datetime, sequence: int) -> "VirtualClock":
        next_clock = VirtualClock.at(cutoff, sequence)
        require(next_clock.sequence > self.sequence, "clock_duplicate_or_reversed_sequence")
        require(utc(next_clock.cutoff) >= utc(self.cutoff), "clock_time_reversed")
        return next_clock
