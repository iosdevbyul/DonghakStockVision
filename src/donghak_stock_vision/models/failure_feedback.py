"""Fixed one-parameter calibration and matched H5 research decisions."""

import math
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.evaluation_evidence import calendar_evidence
from donghak_stock_vision.models.linear import sigmoid

EPSILON = 1e-8
CRITERIA = {
    "calibration": dict(
        minimum_fpr_reduction=0.10, maximum_recall_loss=0.05, maximum_brier_increase=0.0
    ),
    "mask": dict(
        minimum_high_volatility_error_reduction=0.10,
        maximum_brier_increase=0.0,
        maximum_normal_volatility_ba_loss=0.0,
    ),
}


def _logit(score: float) -> float:
    bounded = min(1 - EPSILON, max(EPSILON, score))
    return math.log(bounded / (1 - bounded))


def prior_offset(labels: list[int], scores: list[float]) -> float:
    """Fit a fixed-slope log-odds intercept by calibration-only log loss."""
    require(len(labels) == len(scores) and set(labels) == {0, 1}, "feedback_calibration_labels")
    logits = [_logit(p) for p in scores]
    target = sum(labels)
    low, high = -60.0, 60.0
    for _ in range(64):
        middle = (low + high) / 2
        if sum(sigmoid(v + middle) for v in logits) > target:
            high = middle
        else:
            low = middle
    return (low + high) / 2


def calibrated(scores: list[float], offset: float) -> list[float]:
    return [sigmoid(_logit(p) + offset) for p in scores]


def readout(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    result = metrics([r["label"] for r in rows], [r["scores"][method] for r in rows])
    cm = result["confusion_matrix"]
    tp, fp, tn, fn = (cm[k] for k in ("tp", "fp", "tn", "fn"))
    up, down = tp + fp, tn + fn
    result.update(
        correct=tp + tn,
        incorrect=fp + fn,
        up_predictions=up,
        down_predictions=down,
        up_prediction_rate=up / len(rows) if rows else None,
        down_prediction_rate=down / len(rows) if rows else None,
        down_precision=tn / down if down else None,
        down_recall=tn / (tn + fp) if tn + fp else None,
        fpr=fp / (fp + tn) if fp + tn else None,
        fdr=fp / up if up else None,
        score_bins=[
            dict(
                lower=i / 10,
                upper=(i + 1) / 10,
                n=len(group),
                actual_up_rate=sum(r["label"] for r in group) / len(group) if group else None,
            )
            for i in range(10)
            for group in [[r for r in rows if min(9, int(r["scores"][method] * 10)) == i]]
        ],
    )
    return result


def evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return calendar_evidence(
        [
            dict(ticker=r["ticker"], day=r["input_date"], end=r["target_date"], label=r["label"])
            for r in rows
        ]
    )


def paired(rows: list[dict[str, Any]], before: str, after: str) -> dict[str, int]:
    fixed = sum(not r["correct"][before] and r["correct"][after] for r in rows)
    broken = sum(r["correct"][before] and not r["correct"][after] for r in rows)
    return dict(incorrect_to_correct=fixed, correct_to_incorrect=broken)


def compare(rows: list[dict[str, Any]], study: str) -> dict[str, Any]:
    primary = evidence(rows)
    enough = (
        primary["status"] == "READY_FOR_EXISTING_EVALUATOR"
        and len(rows) >= 200
        and len({r["input_date"] for r in rows}) >= 40
        and min(primary["positive_rows"], primary["negative_rows"]) >= 20
    )
    if study == "calibration":
        left, right = "original", "calibrated"
        a, b = readout(rows, left), readout(rows, right)
        delta = _delta(a, b, "fpr", "recall", "brier_score")
        checks = dict(
            fpr=_passes(delta["fpr"], upper=-CRITERIA[study]["minimum_fpr_reduction"]),
            recall=_passes(delta["recall"], lower=-CRITERIA[study]["maximum_recall_loss"]),
            brier=_passes(delta["brier_score"], upper=CRITERIA[study]["maximum_brier_increase"]),
            predicts_up=b["up_predictions"] > 0,
        )
        regressed = _passes(delta["recall"], upper=-0.05) or _passes(
            delta["brier_score"], lower=1e-12
        )
        groups: dict[str, Any] = {}
        measures = {left: a, right: b, "training_prior": readout(rows, "training_prior")}
    else:
        require(study == "mask", "feedback_study")
        left, right = "legacy", "daily"
        a, b = readout(rows, left), readout(rows, right)
        measures = {left: a, right: b}
        groups = {}
        for name in ("high_volatility", "normal_volatility"):
            group = [r for r in rows if name in r["regimes"]]
            groups[name] = dict(
                evidence=evidence(group), legacy=readout(group, left), daily=readout(group, right)
            )
        high = _delta(groups["high_volatility"][left], groups["high_volatility"][right], "accuracy")
        normal = _delta(
            groups["normal_volatility"][left],
            groups["normal_volatility"][right],
            "balanced_accuracy",
        )
        delta = _delta(a, b, "brier_score") | dict(
            high_volatility_accuracy=high["accuracy"],
            normal_volatility_ba=normal["balanced_accuracy"],
        )
        checks = dict(
            high_volatility=_passes(
                delta["high_volatility_accuracy"],
                lower=CRITERIA[study]["minimum_high_volatility_error_reduction"],
            ),
            brier=_passes(delta["brier_score"], upper=CRITERIA[study]["maximum_brier_increase"]),
            normal_volatility=_passes(
                delta["normal_volatility_ba"],
                lower=-CRITERIA[study]["maximum_normal_volatility_ba_loss"],
            ),
        )
        enough = enough and all(
            g["evidence"]["status"] == "READY_FOR_EXISTING_EVALUATOR" for g in groups.values()
        )
        regressed = _passes(delta["high_volatility_accuracy"], upper=-0.10)
    passed = all(checks.values())
    status = (
        "INSUFFICIENT_EVIDENCE"
        if not enough
        else "INTERNAL_IMPROVEMENT_OBSERVED"
        if passed
        else "INTERNAL_REGRESSION_OBSERVED"
        if regressed
        else "INTERNAL_NO_CLEAR_ADVANTAGE"
    )
    return dict(
        evidence=primary,
        measures=measures,
        groups=groups,
        delta=delta,
        transitions=paired(rows, left, right),
        criteria_met=checks,
        comparison=status,
        verdict="INSUFFICIENT_EVIDENCE" if not enough else "SUPPORTED" if passed else "REJECTED",
    )


def _delta(a: dict[str, Any], b: dict[str, Any], *keys: str) -> dict[str, float | None]:
    return {k: b[k] - a[k] if a[k] is not None and b[k] is not None else None for k in keys}


def _passes(value: float | None, *, lower: float = -math.inf, upper: float = math.inf) -> bool:
    return value is not None and lower - 1e-15 <= value <= upper + 1e-15
