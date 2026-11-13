"""Additional descriptive metrics never change existing report identifiers."""

import random
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    roc_auc_score,
)

from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.models.diagnostics import _bucket, metrics
from donghak_stock_vision.models.metrics import evaluate_metrics


def test_metrics_match_independent_library_with_ties() -> None:
    rng = random.Random(312)
    labels = [rng.randrange(2) for _ in range(200)]
    scores = [rng.randrange(6) / 5 for _ in labels]
    before = digest(evaluate_metrics(labels, scores))
    result = metrics(labels, scores)
    predictions = [int(x >= 0.5) for x in scores]
    for key, value in dict(
        accuracy=accuracy_score(labels, predictions),
        balanced_accuracy=balanced_accuracy_score(labels, predictions),
        f1=f1_score(labels, predictions),
        roc_auc=roc_auc_score(labels, scores),
        pr_auc_ap=average_precision_score(labels, scores),
    ).items():
        assert result[key] == pytest.approx(value)
    assert digest(evaluate_metrics(labels, scores)) == before


@pytest.mark.parametrize("labels,scores", [([], []), ([0, 0], [0.1, 0.8]), ([1, 1], [0.1, 0.8])])
def test_empty_and_single_class_are_explicit(labels: list[int], scores: list[float]) -> None:
    result = metrics(labels, scores)
    assert result["roc_auc"] is None and result["pr_auc_ap"] is None
    assert result["balanced_accuracy"] is None
    assert result["undefined_reasons"]["roc_auc"] in ("empty_sample", "single_class")


def test_fixed_majority_uses_training_not_eval_labels() -> None:
    sample = Sample(
        "005930",
        date(2025, 1, 2),
        datetime(2025, 1, 2, 7, tzinfo=UTC),
        (0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01),
        "down",
        1,
        date(2025, 1, 3),
        date(2025, 1, 10),
        datetime(2025, 1, 11, tzinfo=UTC),
        "labeled",
        "0" * 64,
    )
    parameters: dict[str, Any] = dict(
        features=[
            "return_1",
            "return_3",
            "return_5",
            "close_to_sma10",
            "volatility_10",
            "range_to_close",
            "volume_change_10",
            "trading_value_change_10",
        ],
        classes=[0, 1],
        mean=[0.0] * 8,
        scale=[1.0] * 8,
        coefficient=[0.0] * 8,
        intercept=0.0,
    )
    result = _bucket([sample], [sample], "down", parameters, 0.25)
    assert result["baselines"]["training_majority"]["accuracy"] == 0
    assert result["baselines"]["training_majority"]["confusion_matrix"]["fn"] == 1
    assert result["interpretation_limited"]
