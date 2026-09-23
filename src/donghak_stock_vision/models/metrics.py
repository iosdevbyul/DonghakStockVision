"""Conditional metrics with explicit undefined cases, including noninterpolated AP."""

import statistics
from typing import Any

from donghak_stock_vision.data.learning import THRESHOLD, Direction, Sample
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.models.linear import sigmoid


def evaluate_metrics(labels: list[int], scores: list[float]) -> dict[str, Any]:
    if len(labels) != len(scores):
        raise ValueError("labels and scores must align")
    tp = sum(y == 1 and p >= THRESHOLD for y, p in zip(labels, scores, strict=True))
    fp = sum(y == 0 and p >= THRESHOLD for y, p in zip(labels, scores, strict=True))
    fn = sum(y == 1 and p < THRESHOLD for y, p in zip(labels, scores, strict=True))
    tn = len(labels) - tp - fp - fn
    reasons = {}
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None:
        reasons["precision"] = "no_predicted_positive"
    if recall is None:
        reasons["recall"] = "no_positive_class"
    ap = None
    if set(labels) == {0, 1}:
        # Equal scores enter together: matches average_precision_score (not trapezoidal AUC).
        groups: dict[float, list[int]] = {}
        for y, p in zip(labels, scores, strict=True):
            groups.setdefault(p, []).append(y)
        seen, positives, ap = 0, 0, 0.0
        for p in sorted(groups, reverse=True):
            added = sum(groups[p])
            seen += len(groups[p])
            positives += added
            ap += added / sum(labels) * positives / seen
    else:
        reasons["pr_auc_ap"] = "empty_or_single_class"
    bins = []
    for i in range(10):
        selected = [
            (y, p)
            for y, p in zip(labels, scores, strict=True)
            if i / 10 <= p < (i + 1) / 10 or (i == 9 and p == 1)
        ]
        bins.append(
            {
                "lower": i / 10,
                "upper": (i + 1) / 10,
                "count": len(selected),
                "mean_score": statistics.mean(p for _, p in selected) if selected else None,
                "positive_fraction": statistics.mean(y for y, _ in selected) if selected else None,
            }
        )
    return {
        "n": len(labels),
        "positive": sum(labels),
        "negative": len(labels) - sum(labels),
        "prevalence": statistics.mean(labels) if labels else None,
        "threshold": THRESHOLD,
        "precision": precision,
        "recall": recall,
        "pr_auc_ap": ap,
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "brier_score": statistics.mean((y - p) ** 2 for y, p in zip(labels, scores, strict=True))
        if labels
        else None,
        "undefined_reasons": reasons,
        "calibration": "none",
        "calibration_bins": bins,
    }


def baseline_scores(
    samples: list[Sample], direction: Direction, prior: float
) -> dict[str, list[float]]:
    sign = -1 if direction == "up" else 1
    return {
        "prior": [prior] * len(samples),
        "reversal_strength": [sigmoid(sign * s.values[2] / s.values[4]) for s in samples],
    }


def report(samples: list[Sample], scores: list[float]) -> dict[str, Any]:
    labels = [int(s.label) for s in samples if s.label is not None]
    result = evaluate_metrics(labels, scores)
    result["by_ticker"] = {
        ticker: evaluate_metrics(
            [labels[i] for i, s in enumerate(samples) if s.ticker == ticker],
            [scores[i] for i, s in enumerate(samples) if s.ticker == ticker],
        )
        for ticker in sorted({s.ticker for s in samples})
    }
    result["by_date_count"] = {
        d: sum(s.anchor_at.astimezone(SEOUL).date().isoformat() == d for s in samples)
        for d in sorted({s.anchor_at.astimezone(SEOUL).date().isoformat() for s in samples})
    }
    return result
