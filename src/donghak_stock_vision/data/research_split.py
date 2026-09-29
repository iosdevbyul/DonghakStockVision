"""Inclusive Seoul date ranges for research; no changes to source snapshots."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from donghak_stock_vision.data.learning import AnalysisError, Sample, digest, timestamp
from donghak_stock_vision.data.schema import SEOUL


@dataclass(frozen=True)
class ResearchDateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
            raise AnalysisError("invalid_research_date_range")

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class ExplicitResearchSplit:
    training: ResearchDateRange
    validation: ResearchDateRange
    final_test: ResearchDateRange

    def __post_init__(self) -> None:
        if not all(
            isinstance(r, ResearchDateRange)
            for r in (self.training, self.validation, self.final_test)
        ):
            raise AnalysisError("invalid_research_split_ranges")
        if not (
            self.training.end < self.validation.start
            and self.validation.end < self.final_test.start
        ):
            raise AnalysisError("overlapping_or_unordered_research_split")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "training": self.training.to_dict(),
            "validation": self.validation.to_dict(),
            "final_test": self.final_test.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExplicitResearchSplit":
        try:
            if set(value) != {"version", "training", "validation", "final_test"}:
                raise ValueError
            if type(value["version"]) is not int or value["version"] != 1:
                raise ValueError
            ranges = []
            for name in ("training", "validation", "final_test"):
                row = value[name]
                if set(row) != {"start", "end"}:
                    raise ValueError
                ranges.append(
                    ResearchDateRange(
                        date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
                    )
                )
            return cls(*ranges)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise AnalysisError("invalid_explicit_research_split") from error

    @property
    def identifier(self) -> str:
        return digest(self.to_dict())

    def validate_snapshot(self, snapshot: dict[str, Any]) -> None:
        if snapshot["mode"] != "historical_research":
            raise AnalysisError("explicit_split_requires_historical_research")
        if (
            date.fromisoformat(snapshot["start"]) > self.training.start
            or date.fromisoformat(snapshot["end"]) < self.final_test.end
        ):
            raise AnalysisError("split_outside_snapshot")


def explicit_split(samples: list[Sample], config: ExplicitResearchSplit) -> dict[str, Any]:
    partitions: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    purged = []
    outside = 0
    eligible_dates = set()
    for sample in sorted(samples, key=lambda s: (timestamp(s.anchor_at), s.ticker)):
        day = timestamp(sample.anchor_at).astimezone(SEOUL).date()
        if day != sample.trading_date:
            raise AnalysisError("research_anchor_date_mismatch")
        part = next(
            (
                name
                for name, span in (
                    ("train", config.training),
                    ("validation", config.validation),
                    ("test", config.final_test),
                )
                if span.start <= day <= span.end
            ),
            None,
        )
        if part is None:
            outside += 1
            continue
        eligible_dates.add(day)
        if sample.label is None or sample.direction is None:
            continue
        boundary = config.validation.start if part == "train" else config.final_test.start
        if sample.label_end is None or (
            sample.label_end > config.final_test.end
            if part == "test"
            else sample.label_end >= boundary
        ):
            purged.append(
                {
                    "ticker": sample.ticker,
                    "anchor_at": sample.anchor_at.isoformat(),
                    "partition": part,
                }
            )
            continue
        partitions[part].append(sample.to_dict())
    return {
        "configuration": config.to_dict(),
        "configuration_hash": config.identifier,
        "validation_start": datetime.combine(config.validation.start, time(), SEOUL).isoformat(),
        "test_start": datetime.combine(config.final_test.start, time(), SEOUL).isoformat(),
        "dates": len(eligible_dates),
        "outside_range": outside,
        "purged": purged,
        "partitions": partitions,
    }
