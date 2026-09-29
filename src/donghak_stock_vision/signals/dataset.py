"""Conditional labels and one common chronological panel split."""

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, time
from typing import Any

from donghak_stock_vision.data.learning import (
    HORIZON,
    MIN_BARRIER,
    VOLATILITY_MULTIPLIER,
    AnalysisError,
    Direction,
    Minimums,
    Sample,
    digest,
)
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, explicit_split
from donghak_stock_vision.data.schema import SEOUL, DailyBar
from donghak_stock_vision.data.snapshot import snapshot_bars
from donghak_stock_vision.features.engine import check_segment, feature_vector


def context(values: tuple[float, ...]) -> Direction | None:
    return "up" if values[2] < 0 else "down" if values[2] > 0 else None


def label_event(
    bars: list[DailyBar],
    index: int,
    anchor: datetime,
    values: tuple[float, ...],
    snapshot: dict[str, Any],
) -> tuple[int | None, date | None, date | None, datetime | None, str]:
    direction = context(values)
    if direction is None:
        return None, None, None, None, "no_context"
    future_indices = [
        j
        for j in range(index + 1, len(bars))
        if bars[j].trading_date > anchor.astimezone(SEOUL).date()
    ][:HORIZON]
    if len(future_indices) < HORIZON:
        return None, None, None, None, "immature_label"
    first, last = future_indices[0], future_indices[-1]
    future = [bars[j] for j in future_indices]
    available = max(b.collected_at for b in bars[index : last + 1])
    try:
        check_segment(bars[index : last + 1], snapshot)
    except AnalysisError as error:
        return None, future[0].trading_date, future[-1].trading_date, available, str(error)
    width = max(MIN_BARRIER, VOLATILITY_MULTIPLIER * values[4])
    if width >= 1 or values[4] <= 0:
        return None, None, None, None, "invalid_barrier"
    upper, lower = bars[first].open * (1 + width), bars[first].open * (1 - width)
    outcome = "none"
    for bar in future:
        up, down = bar.high >= upper, bar.low <= lower
        if up and down:
            return None, future[0].trading_date, future[-1].trading_date, available, "ambiguous"
        if up or down:
            outcome = "up" if up else "down"
            break
    return (
        int(outcome == direction),
        future[0].trading_date,
        future[-1].trading_date,
        available,
        "labeled",
    )


def build_dataset(snapshot: dict[str, Any]) -> tuple[list[Sample], dict[str, int]]:
    samples = []
    exclusions: Counter[str] = Counter()
    pit = snapshot["mode"] == "point_in_time"
    for ticker in sorted(snapshot["rows"]):
        bars = snapshot_bars(snapshot, ticker)
        for i, bar in enumerate(bars):
            anchor = (
                bar.collected_at if pit else datetime.combine(bar.trading_date, time(16), SEOUL)
            )
            try:
                if pit and any(b.collected_at <= anchor for b in bars[i + 1 :]):
                    raise AnalysisError("insufficient_point_in_time_data")
                values = feature_vector(bars[: i + 1], snapshot, anchor if pit else None)
            except AnalysisError as error:
                exclusions[str(error)] += 1
                continue
            label, start, end, available, status = label_event(bars, i, anchor, values, snapshot)
            if status != "labeled":
                exclusions[status] += 1
            samples.append(
                Sample(
                    ticker,
                    bar.trading_date,
                    anchor,
                    values,
                    context(values),
                    label,
                    start,
                    end,
                    available,
                    status,
                    digest([b.to_dict() for b in bars[max(0, i - 11) : i + 1]]),
                    tuple(
                        b.trading_date
                        for b in bars
                        if start is not None and end is not None and start <= b.trading_date <= end
                    ),
                )
            )
    return sorted(samples, key=lambda s: (s.anchor_at, s.ticker)), dict(sorted(exclusions.items()))


def split_dataset(
    samples: list[Sample], mode: str, configuration: ExplicitResearchSplit | None = None
) -> dict[str, Any]:
    if configuration is not None:
        if mode != "historical_research":
            raise AnalysisError("explicit_split_requires_historical_research")
        return explicit_split(samples, configuration)
    days = sorted({s.anchor_at.astimezone(SEOUL).date() for s in samples})
    if len(days) < 5:
        raise AnalysisError("insufficient_split_dates")
    validation_day, test_day = days[int(len(days) * 0.6)], days[int(len(days) * 0.8)]
    validation_time = min(
        s.anchor_at for s in samples if s.anchor_at.astimezone(SEOUL).date() == validation_day
    )
    test_time = min(
        s.anchor_at for s in samples if s.anchor_at.astimezone(SEOUL).date() == test_day
    )
    partitions: dict[str, list[Sample]] = {"train": [], "validation": [], "test": []}
    purged: list[dict[str, str]] = []
    for sample in samples:
        if sample.label is None or sample.direction is None:
            continue
        day = sample.anchor_at.astimezone(SEOUL).date()
        part = "train" if day < validation_day else "validation" if day < test_day else "test"
        boundary = validation_day if part == "train" else test_day
        boundary_time = validation_time if part == "train" else test_time
        if part != "test" and (
            sample.label_end is None
            or sample.label_end >= boundary
            or (
                mode == "point_in_time"
                and (
                    sample.label_available_at is None or sample.label_available_at >= boundary_time
                )
            )
        ):
            purged.append(
                {
                    "ticker": sample.ticker,
                    "anchor_at": sample.anchor_at.isoformat(),
                    "partition": part,
                }
            )
            continue
        partitions[part].append(sample)
    return {
        "validation_start": validation_time.isoformat(),
        "test_start": test_time.isoformat(),
        "dates": len(days),
        "purged": purged,
        "partitions": {key: [s.to_dict() for s in rows] for key, rows in partitions.items()},
    }


def population(split: dict[str, Any], direction: Direction, part: str) -> list[Sample]:
    return [Sample.from_dict(s) for s in split["partitions"][part] if s["direction"] == direction]


def counts(samples: list[Sample]) -> dict[str, int]:
    return {
        "rows": len(samples),
        "dates": len({s.anchor_at.astimezone(SEOUL).date() for s in samples}),
        "positive": sum(s.label == 1 for s in samples),
        "negative": sum(s.label == 0 for s in samples),
        "tickers": len({s.ticker for s in samples}),
    }


def requirements(
    samples: list[Sample],
    split: dict[str, Any],
    direction: Direction,
    minimums: Minimums,
) -> dict[str, Any]:
    actual = {
        part: counts(population(split, direction, part)) for part in ("train", "validation", "test")
    }
    failures = []
    if len({s.ticker for s in samples}) < minimums.tickers or split["dates"] < minimums.dates:
        failures.append("global_tickers_or_dates")
    for part, count in actual.items():
        rows = minimums.train_rows if part == "train" else minimums.holdout_rows
        days = minimums.train_dates if part == "train" else minimums.holdout_dates
        classes = minimums.train_class if part == "train" else minimums.holdout_class
        for key, required in (
            ("rows", rows),
            ("dates", days),
            ("positive", classes),
            ("negative", classes),
        ):
            if count[key] < required:
                failures.append(f"{part}.{key}")
    return {"required": asdict(minimums), "actual": actual, "failures": failures}


def sample_weights(samples: list[Sample]) -> list[float]:
    """Mean uniqueness on actual label sessions, not calendar days or endpoints."""
    date_counts = Counter(s.anchor_at.astimezone(SEOUL).date() for s in samples)
    concurrency = Counter((s.ticker, d) for s in samples for d in s.label_sessions)
    weights = []
    for sample in samples:
        if not sample.label_sessions:
            raise AnalysisError("missing_label_sessions")
        unique = sum(1 / concurrency[(sample.ticker, d)] for d in sample.label_sessions)
        unique /= len(sample.label_sessions)
        weights.append(unique / date_counts[sample.anchor_at.astimezone(SEOUL).date()])
    total = sum(weights)
    return [w * len(weights) / total for w in weights]
