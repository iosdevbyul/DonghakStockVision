"""Preregistered order balancing and descriptive, dependence-aware campaign analysis."""

from __future__ import annotations

import itertools
import statistics as st
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.memory_reasoning import ARMS

ORDERS = tuple(itertools.permutations(ARMS))
POLICY: dict[str, Any] = dict(
    version="campaign_matched_internal_v1",
    minimum_evaluations=4,
    minimum_anchors=20,
    minimum_per_class=10,
    ap_margin=0.005,
    maximum_ba_regression=0.01,
    maximum_brier_regression=0.002,
    independent_generalization="NOT_ESTABLISHED",
)


def schedule(
    cells: list[dict[str, Any]], budget: int, arms: tuple[str, ...] = ARMS
) -> list[dict[str, Any]]:
    require(
        1 <= len(cells) <= 6 and type(budget) is int and 1 <= budget <= 8, "campaign_schedule_bound"
    )
    orders = (
        ORDERS
        if arms == ARMS
        else tuple(
            tuple(order[(i + j) % len(order)] for j in range(len(order)))
            for order in (arms, tuple(reversed(arms)))
            for i in range(len(arms))
        )
    )
    result = []
    for round_index in range(budget):
        for index, cell in enumerate(cells):
            for position, arm in enumerate(orders[(index + round_index) % len(orders)]):
                body = dict(cell=cell["cell_id"], arm=arm, round=round_index + 1, position=position)
                result.append(body | dict(step_id=digest(body)))
    return result


def distribution(values: list[float]) -> dict[str, Any]:
    return dict(
        n=len(values),
        mean=st.mean(values) if values else None,
        median=st.median(values) if values else None,
        minimum=min(values, default=None),
        maximum=max(values, default=None),
        stddev=st.pstdev(values) if values else None,
    )


def analyze(
    rows: list[dict[str, Any]], planned: int, arm_names: tuple[str, ...] = ARMS
) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm in arm_names:
        group = [r for r in rows if r["arm"] == arm]
        folds = [f for r in group for f in r["folds"]]
        arms[arm] = dict(
            evaluated=len(group),
            planned=planned,
            ap=distribution([r["mean_ap"] for r in group]),
            ba=distribution([f["metrics"]["balanced_accuracy"] for f in folds]),
            brier=distribution([f["metrics"]["brier_score"] for f in folds]),
            baseline_ap_gain=distribution([r["baseline_ap_gain"] for r in group]),
            worst_ba=min((r["worst_ba"] for r in group), default=None),
            models=[
                dict(
                    experimentId=r["experiment_id"],
                    family=r["family"],
                    mask=r["mask"],
                    meanAP=r["mean_ap"],
                    baselineAPGain=r["baseline_ap_gain"],
                    folds=r["folds"],
                )
                for r in group
            ],
        )
    comparisons = []
    for left, right in itertools.combinations(arm_names, 2):
        a, b = arms[left], arms[right]
        same = bool(a["evaluated"] and a["evaluated"] == b["evaluated"])
        populations = {r["population"] for r in rows if r["arm"] in (left, right)}
        same = same and len(populations) == 1
        enough = (
            same
            and a["evaluated"] >= POLICY["minimum_evaluations"]
            and all(
                f["anchors"] >= POLICY["minimum_anchors"]
                and min(f["counts"]["positive"], f["counts"]["negative"])
                >= POLICY["minimum_per_class"]
                for r in rows
                if r["arm"] in (left, right)
                for f in r["folds"]
            )
        )
        delta = b["ap"]["mean"] - a["ap"]["mean"] if same else None
        if not enough:
            status = "INSUFFICIENT_EVIDENCE"
        elif (
            delta is not None
            and delta > POLICY["ap_margin"]
            and b["worst_ba"] >= a["worst_ba"] - POLICY["maximum_ba_regression"]
            and b["brier"]["mean"] <= a["brier"]["mean"] + POLICY["maximum_brier_regression"]
        ):
            status = "INTERNAL_IMPROVEMENT_OBSERVED"
        elif delta is not None and delta < -POLICY["ap_margin"]:
            status = "INTERNAL_REGRESSION_OBSERVED"
        else:
            status = "INTERNAL_NO_CLEAR_ADVANTAGE"
        comparisons.append(
            dict(
                left=left,
                right=right,
                matched=same,
                status=status,
                meanAPDelta=delta,
                independent=False,
                uncertaintyInterval=None,
                uncertaintyReason="correlated_candidates_shared_calendar_dates",
            )
        )
    return dict(
        arms=arms,
        comparisons=comparisons,
        policy=POLICY,
        generalization="GENERALIZATION_NOT_ESTABLISHED",
    )


def stability(value: dict[str, Any] | None) -> dict[str, Any]:
    """Narrow descriptive ticker/period/regime projection; no source data or authority."""
    import math
    import re

    result = {}
    for section in ("periods", "input_regimes", "ticker_distribution"):
        rows = (value or {}).get(section, {})
        safe = {}
        for key, item in rows.items():
            require(
                type(key) is str and re.fullmatch("[A-Za-z0-9_.-]{1,100}", key) is not None,
                "campaign_stability_key",
            )
            metrics = {
                k: v
                for k, v in item.get("metrics", {}).items()
                if k
                in (
                    "n",
                    "positive",
                    "negative",
                    "pr_auc_ap",
                    "balanced_accuracy",
                    "brier_score",
                    "roc_auc",
                )
                and (v is None or type(v) in (int, float) and math.isfinite(v))
            }
            safe[key] = dict(
                metrics=metrics,
                status=item.get("status")
                if item.get("status") in ("internal_descriptive_sufficient", "insufficient")
                else "UNKNOWN",
                independent=False,
            )
        result[section] = safe
    return result
