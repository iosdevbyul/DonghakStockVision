"""Explicit research exclusions; never exchange holidays or repaired PIT evidence."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from donghak_stock_vision.data.learning import AnalysisError, digest


@dataclass(frozen=True)
class ResearchCalendarPolicy:
    incomplete_dates: tuple[date, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.incomplete_dates, tuple) or any(
            type(d) is not date for d in self.incomplete_dates
        ):
            raise AnalysisError("invalid_incomplete_trading_dates")
        object.__setattr__(self, "incomplete_dates", tuple(sorted(set(self.incomplete_dates))))

    def excludes(self, day: date) -> bool:
        return day in self.incomplete_dates

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "reason": "known_incomplete_trading_date",
            "action": "exclude_research_session",
            "incomplete_dates": [d.isoformat() for d in self.incomplete_dates],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResearchCalendarPolicy":
        try:
            if (
                set(value) != {"version", "reason", "action", "incomplete_dates"}
                or type(value["version"]) is not int
                or value["version"] != 1
                or value["reason"] != "known_incomplete_trading_date"
                or value["action"] != "exclude_research_session"
                or not isinstance(value["incomplete_dates"], list)
            ):
                raise ValueError
            return cls(tuple(date.fromisoformat(d) for d in value["incomplete_dates"]))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise AnalysisError("invalid_research_calendar_policy") from error

    @property
    def identifier(self) -> str:
        return digest(self.to_dict())


def calendar_policy(metadata: dict[str, Any]) -> ResearchCalendarPolicy | None:
    if "research_calendar" not in metadata:
        return None
    return ResearchCalendarPolicy.from_dict(metadata["research_calendar"])


def excluded_dates(metadata: dict[str, Any]) -> set[str]:
    policy = calendar_policy(metadata)
    return {d.isoformat() for d in policy.incomplete_dates} if policy else set()
