"""New retrospective direction labels; never a v1/v2 reversal or live forecast."""

from collections import Counter
from datetime import datetime, time
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import FEATURES, Sample, digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import snapshot_bars
from donghak_stock_vision.features.engine import check_segment, feature_vector
from donghak_stock_vision.models.experiment import CANDIDATES, Candidate, Fold, purged_fold
from donghak_stock_vision.signals.dataset import counts

VERSION = "daily_direction_observations_v1"
MASKS = {
    "all": tuple(range(8)),
    "momentum": (0, 1, 2, 3, 4),
    "flow": (0, 2, 4, 6, 7),
    "range": (0, 2, 4, 5),
}
RF = (
    Candidate("forest_depth3", "random_forest", 3),
    Candidate("forest_depth6", "random_forest", 6),
)


def contract(horizon: int) -> dict[str, Any]:
    require(type(horizon) is int and horizon in (1, 5, 20), "direction_horizon")
    return dict(
        version=VERSION,
        label_version=f"close_direction_h{horizon}_v1",
        horizon=horizon,
        target="future_close_over_input_close",
        neutral_band_bps=20,
        round_trip_cost_bps=20,
        costs="illustrative_scenario_not_actual_fees",
        adjustment="unadjusted",
        neutral="exclude_abs_return_at_most_20bps",
        session_basis="observed_not_exchange_authenticated",
        origin="nominal_close_historical_research_not_available_at_historical_anchor",
        feature_version="ohlcv_value_v1",
        features=list(FEATURES),
        threshold=0.5,
        pit_qualified=False,
        live_forward_compatible=False,
    )


def build(
    snapshot: dict[str, Any], horizon: int
) -> tuple[list[Sample], dict[str, float], dict[str, int]]:
    contract(horizon)
    rows: list[Sample] = []
    returns: dict[str, float] = {}
    excluded: Counter[str] = Counter()
    for ticker in sorted(snapshot["rows"]):
        bars = snapshot_bars(snapshot, ticker)
        for i, bar in enumerate(bars):
            try:
                values = feature_vector(bars[: i + 1], snapshot)
                if i + horizon >= len(bars):
                    excluded["immature_label"] += 1
                    continue
                future = bars[i + 1 : i + horizon + 1]
                check_segment(bars[max(0, i - 11) : i + horizon + 1], snapshot)
                if any(
                    bar.trading_date.isoformat() < day <= future[-1].trading_date.isoformat()
                    for day in snapshot["unavailable_dates"].get(ticker, [])
                ):
                    excluded["missing_event_segment"] += 1
                    continue
                change = future[-1].close / bar.close - 1
                if abs(future[-1].close - bar.close) * 10000 <= 20 * bar.close:
                    excluded["neutral_cost_band"] += 1
                    continue
                sample = Sample(
                    ticker,
                    bar.trading_date,
                    datetime.combine(bar.trading_date, time(16), SEOUL),
                    values,
                    "up",
                    int(change > 0),
                    future[0].trading_date,
                    future[-1].trading_date,
                    max(b.collected_at for b in bars[i : i + horizon + 1]),
                    "labeled",
                    digest([b.to_dict() for b in bars[max(0, i - 11) : i + 1]]),
                    tuple(b.trading_date for b in future),
                )
                # Use existing canonical UTC parsing before assigning retained sample IDs.
                sample = Sample.from_dict(sample.to_dict())
                rows.append(sample)
                returns[digest(sample.to_dict())] = change
            except ValueError as error:
                excluded[str(error)] += 1
    return (
        sorted(rows, key=lambda s: (s.anchor_at, s.ticker)),
        returns,
        dict(sorted(excluded.items())),
    )


def folds(snapshot: dict[str, Any], split: ExplicitResearchSplit) -> tuple[Fold, ...]:
    days = sorted(
        {
            b["trading_date"]
            for v in snapshot["rows"].values()
            for b in v
            if split.training.start.isoformat()
            <= b["trading_date"]
            <= split.training.end.isoformat()
        }
    )
    require(len(days) >= 420, "direction_training_dates")
    indices = [int(len(days) * r) for r in (0.60, 0.73, 0.86)] + [len(days)]
    from datetime import date

    return tuple(
        Fold(
            ResearchDateRange(split.training.start, date.fromisoformat(days[a - 1])),
            ResearchDateRange(date.fromisoformat(days[a]), date.fromisoformat(days[b - 1])),
        )
        for a, b in zip(indices, indices[1:], strict=False)
    )


def readiness(samples: list[Sample], windows: tuple[Fold, ...]) -> dict[str, Any]:
    checked = []
    failures = []
    for i, fold in enumerate(windows):
        train, test, purged = purged_fold(samples, fold, "up")
        c = {"train": counts(train), "validation": counts(test)}
        for name, min_dates, min_rows in (("train", 252, 1000), ("validation", 40, 200)):
            if (
                c[name]["dates"] < min_dates
                or c[name]["rows"] < min_rows
                or min(c[name]["positive"], c[name]["negative"]) < 20
            ):
                failures.append(f"fold_{i}_{name}_insufficient")
        checked.append(dict(fold=fold.to_dict(), counts=c, purged=purged))
    return dict(folds=checked, failures=failures)


def catalog(horizons: list[int]) -> list[dict[str, Any]]:
    # Finite, predeclared first search space; ordering is selected from memory later.
    choices = [(c, "all") for c in (*CANDIDATES, *RF)]
    choices += [
        (CANDIDATES[1], "momentum"),
        (CANDIDATES[0], "flow"),
        (RF[0], "range"),
        (CANDIDATES[3], "momentum"),
        (CANDIDATES[4], "flow"),
    ]
    return [
        dict(goal=contract(h), candidate=c.to_dict(), mask=mask, seed=42)
        for h in horizons
        for c, mask in choices
    ]
