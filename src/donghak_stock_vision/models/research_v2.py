"""Finite expanded study contracts and auditable training-internal adaptation."""

import statistics as st
from collections import Counter
from datetime import datetime, time
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import snapshot_bars
from donghak_stock_vision.features.engine import check_segment
from donghak_stock_vision.features.research import FEATURES_V2, GROUPS, VERSION, vector
from donghak_stock_vision.models.directional import contract as original_contract
from donghak_stock_vision.models.experiment import Candidate, Fold

SPACE = "adaptive_direction_v2"


def contract(h: int) -> dict[str, Any]:
    return original_contract(h) | dict(
        version="observed_delayed_direction_v2",
        label_version=f"delayed_close_direction_h{h}_v2",
        target="close_after_h_observations_over_next_observed_close",
        skipped_future_observations=1,
        feature_version=VERSION,
        features=list(FEATURES_V2),
        timeframes="closed_observed_periods_only_not_exchange_complete",
        origin="retrospective_research_actual_input_availability_retained_not_historical_PIT",
    )


def candidates() -> list[Candidate]:
    result = []
    for c in (0.1, 1.0, 10.0):
        for solver, weight in (("lbfgs", "balanced"), ("liblinear", None)):
            result.append(
                Candidate(
                    f"v2_c{c}_{solver}_{weight}",
                    "linear",
                    c,
                    dict(solver=solver, class_weight=weight),
                )
            )
    for depth in (3, 6):
        for trees, leaf, features in ((32, 30, "sqrt"), (96, 10, 0.75)):
            result.append(
                Candidate(
                    f"v2_forest{depth}_{trees}",
                    "random_forest",
                    depth,
                    dict(n_estimators=trees, min_samples_leaf=leaf, max_features=features),
                )
            )
    for leaves in (7, 15):
        for rate, iterations, reg in ((0.03, 80, 2.0), (0.1, 160, 0.5)):
            result.append(
                Candidate(
                    f"v2_hist{leaves}_{rate}",
                    "hist_gradient_boosting",
                    leaves,
                    dict(learning_rate=rate, max_iter=iterations, l2_regularization=reg),
                )
            )
    return result


def catalog(horizons: list[int]) -> list[dict[str, Any]]:
    result = [
        dict(goal=contract(h), candidate=c.to_dict(), mask="v2_" + group, seed=42)
        for h in horizons
        for group in GROUPS
        for c in candidates()
    ]
    require(len({digest(c) for c in result}) == len(result), "research_semantic_duplicate")
    return result


def prepare_features(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    require(sum(len(v) for v in snapshot["rows"].values()) <= 20000, "multi_feature_cache_bound")
    cache: dict[str, list[dict[str, Any]]] = {}
    for ticker in sorted(snapshot["rows"]):
        bars = snapshot_bars(snapshot, ticker)
        originals = [b.to_dict() for b in bars]
        cache[ticker] = []
        available = bars[0].collected_at if bars else None
        for i, b in enumerate(bars):
            assert available is not None
            available = max(available, b.collected_at)
            entry: dict[str, Any] = dict(
                date=b.trading_date.isoformat(), available_at=available.isoformat()
            )
            try:
                entry.update(
                    values=vector(bars[: i + 1], snapshot, available),
                    input_hash=digest(originals[max(0, i - 62) : i + 1]),
                )
            except ValueError as error:
                entry["error"] = str(error)
            cache[ticker].append(entry)
    return cache


def build(
    snapshot: dict[str, Any],
    h: int,
    *,
    feature_cache: dict[str, list[dict[str, Any]]] | None = None,
    exclusions_by_ticker: dict[str, dict[str, int]] | None = None,
) -> tuple[list[Sample], dict[str, float], dict[str, int]]:
    contract(h)
    rows, changes = [], {}
    excluded: Counter[str] = Counter()
    for ticker in sorted(snapshot["rows"]):
        bars = snapshot_bars(snapshot, ticker)
        original_rows = [b.to_dict() for b in bars]
        ticker_excluded: Counter[str] = Counter()
        for i, b in enumerate(bars):
            try:
                history = bars[: i + 1]
                available = max(r.collected_at for r in history)
                if feature_cache is None:
                    values = vector(history, snapshot, available)
                    input_hash = digest(original_rows[max(0, i - 62) : i + 1])
                else:
                    cached = feature_cache[ticker][i]
                    require("error" not in cached, cached.get("error", "multi_feature_cache_error"))
                    values = tuple(cached["values"])
                    input_hash = cached["input_hash"]
                require(i + h + 1 < len(bars), "research_immature_label")
                future = bars[i + 2 : i + h + 2]
                check_segment(bars[max(0, i - 35) : i + h + 2], snapshot)
                base = bars[i + 1].close
                change = future[-1].close / base - 1
                require(
                    abs(future[-1].close - base) * 10000 > 20 * base, "research_neutral_cost_band"
                )
                s = Sample(
                    ticker,
                    b.trading_date,
                    datetime.combine(bars[i + 1].trading_date, time(19), SEOUL),
                    values,
                    "up",
                    int(change > 0),
                    future[0].trading_date,
                    future[-1].trading_date,
                    max(r.collected_at for r in bars[i : i + h + 2]),
                    "labeled",
                    input_hash,
                    tuple(r.trading_date for r in future),
                )
                s = Sample.from_dict(s.to_dict())
                rows.append(s)
                changes[digest(s.to_dict())] = change
            except ValueError as error:
                excluded[str(error)] += 1
                ticker_excluded[str(error)] += 1
        if exclusions_by_ticker is not None:
            exclusions_by_ticker[ticker] = dict(ticker_excluded)
    return sorted(rows, key=lambda s: (s.anchor_at, s.ticker)), changes, dict(excluded)


def folds(snapshot: dict[str, Any], split: ExplicitResearchSplit, h: int) -> tuple[Fold, ...]:
    from datetime import date

    days = sorted(
        {
            r["trading_date"]
            for rows in snapshot["rows"].values()
            for r in rows
            if split.training.start.isoformat()
            <= r["trading_date"]
            <= split.training.end.isoformat()
        }
    )
    require(len(days) >= 420, "research_training_dates")
    boundaries = (
        [len(days) - 160, len(days) - 80, len(days)]
        if h == 20 and len(days) >= 480
        else [len(days) - 100, len(days)]
        if h == 20
        else [len(days) - 120, len(days) - 60, len(days)]
    )
    return tuple(
        Fold(
            ResearchDateRange(split.training.start, date.fromisoformat(days[a - 1])),
            ResearchDateRange(date.fromisoformat(days[a]), date.fromisoformat(days[b - 1])),
        )
        for a, b in zip(boundaries, boundaries[1:], strict=False)
    )


def rank(choices: list[dict[str, Any]], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only verified internal metrics: deterministic evidence, never LLM prose."""
    require(bool(choices), "research_search_exhausted")
    evaluated = [r for r in history if r["state"] == "completed"]

    def priority(c: dict[str, Any]) -> tuple[float, str]:
        cfg = c["config"]
        matching = [
            r
            for r in evaluated
            if r["config"]["candidate"]["family"] == cfg["candidate"]["family"]
            or r["config"]["mask"] == cfg["mask"]
        ]
        unseen_family = not any(
            r["config"]["candidate"]["family"] == cfg["candidate"]["family"] for r in evaluated
        )
        unseen_mask = not any(r["config"]["mask"] == cfg["mask"] for r in evaluated)
        performance = (
            st.mean(r["stability_ap"] - r["largest_train_gap"] for r in matching) if matching else 0
        )
        complex_penalty = (
            0.15
            if any(r["largest_train_gap"] > 0.15 for r in matching)
            and cfg["candidate"]["value"] > 3
            else 0
        )
        failed = sum(
            r["state"] != "completed" and r["config"]["mask"] == cfg["mask"] for r in history
        )
        return (
            -(
                float(unseen_family) * 2
                + float(unseen_mask)
                + performance
                - complex_penalty
                - failed * 0.25
            ),
            c["experiment_id"],
        )

    return sorted(choices, key=priority)


def selection(plan: dict[str, Any], done: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    owners = {c["experiment_id"]: c["config"] for c in plan["configurations"]}
    require(
        all(k in owners and v["config"] == owners[k] for k, v in done.items()),
        "research_memory_owner",
    )
    if len(done) >= plan["experiment_budget"]:
        return None
    remaining = [c for c in plan["configurations"] if c["experiment_id"] not in done]
    if not remaining:
        return None
    h = min(
        sorted({c["config"]["goal"]["horizon"] for c in remaining}),
        key=lambda h: sum(v["config"]["goal"]["horizon"] == h for v in done.values()),
    )
    choices = [c for c in remaining if c["config"]["goal"]["horizon"] == h]
    history = [
        v | dict(experiment_id=k) for k, v in done.items() if v["config"]["goal"]["horizon"] == h
    ]
    ranked = rank(choices, history)
    without = rank(choices, [])[0]["experiment_id"]
    parents = [v for v in history if v["state"] == "completed"]
    parent = max(parents, key=lambda v: v["stability_ap"] - v["largest_train_gap"], default=None)
    return ranked[0] | dict(
        selection_reason="verified_memory_adaptation_v2",
        parent_experiment=None if parent is None else parent["experiment_id"],
        adaptation=dict(
            version="memory_effect_v2",
            history_ids=sorted(done),
            selected_with_memory=ranked[0]["experiment_id"],
            selected_without_memory=without,
            choice_changed=without != ranked[0]["experiment_id"],
            alternatives=[c["experiment_id"] for c in ranked[1:4]],
            evidence=[
                dict(
                    experiment_id=v["experiment_id"],
                    state=v["state"],
                    stability_ap=v.get("stability_ap"),
                    overfit_gap=v.get("largest_train_gap"),
                )
                for v in history
            ],
        ),
    )
