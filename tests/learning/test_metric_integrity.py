"""Reject corrupted evaluation inputs instead of reporting plausible statistics."""

import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from donghak_stock_vision.data.learning import Sample
from donghak_stock_vision.models.metrics import evaluate_metrics, report


@pytest.mark.parametrize("label", [-1, 2, 0.5, 1.0, True, False, None, "1", math.nan])
def test_invalid_labels_fail_closed(label: Any) -> None:
    with pytest.raises(ValueError, match="binary integers"):
        evaluate_metrics([0, label], [0.2, 0.8])


@pytest.mark.parametrize(
    "score", [math.nan, math.inf, -math.inf, -0.01, 1.01, 10**400, True, False, None, "0.8"]
)
def test_invalid_scores_fail_closed(score: Any) -> None:
    with pytest.raises(ValueError, match="finite numbers"):
        evaluate_metrics([0, 1], [0.2, score])


def test_alignment_and_probability_boundaries() -> None:
    with pytest.raises(ValueError, match="align"):
        evaluate_metrics([0, 1], [0.2])
    measured = evaluate_metrics([0, 1, 0, 1], [0, 1, 0.5, 0.5])
    assert measured["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 0, "tp": 2}
    assert measured["brier_score"] == 0.125
    assert sum(b["count"] for b in measured["calibration_bins"]) == 4


@pytest.mark.parametrize("labels,scores", [([], []), ([0, 0], [0.1, 0.2]), ([1], [1.0])])
def test_undefined_ap_preserves_missing_value(labels: list[int], scores: list[float]) -> None:
    measured = evaluate_metrics(labels, scores)
    assert measured["pr_auc_ap"] is None
    assert measured["undefined_reasons"]["pr_auc_ap"] == "empty_or_single_class"
    if not labels:
        assert measured["brier_score"] is None
        assert measured["prevalence"] is None


@pytest.mark.parametrize("ties", [False, True])
def test_synthetic_probabilities_match_independent_metrics(ties: bool) -> None:
    from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix

    rng = random.Random(42)
    labels = [rng.randrange(2) for _ in range(1000)]
    scores = [rng.randrange(11) / 10 if ties else rng.random() for _ in labels]
    measured = evaluate_metrics(labels, scores)
    assert measured["pr_auc_ap"] == pytest.approx(average_precision_score(labels, scores))
    assert measured["brier_score"] == pytest.approx(brier_score_loss(labels, scores))
    matrix = confusion_matrix(labels, [p >= 0.5 for p in scores], labels=[0, 1])
    tn, fp, fn, tp = (int(n) for n in matrix.ravel())
    assert measured["confusion_matrix"] == {"tn": tn, "fp": fp, "fn": fn, "tp": tp}
    assert measured["n"] == sum(b["count"] for b in measured["calibration_bins"])


def sample() -> Sample:
    return Sample(
        ticker="005930",
        trading_date=date(2026, 1, 2),
        anchor_at=datetime(2026, 1, 2, 7, tzinfo=UTC),
        values=(0.0,) * 8,
        direction="up",
        label=1,
        label_start=date(2026, 1, 5),
        label_end=date(2026, 1, 9),
        label_available_at=datetime(2026, 1, 12, tzinfo=UTC),
        label_status="labeled",
        input_hash="synthetic-unit-test",
    )


@pytest.mark.parametrize("status", ["immature_label", "ambiguous", "missing_verified_session"])
def test_unavailable_samples_cannot_be_scored_as_observed_outcomes(status: str) -> None:
    with pytest.raises(ValueError, match="explicitly labeled"):
        report([replace(sample(), label=None, label_status=status)], [0.7])


def test_report_rejects_missing_labels_without_silently_dropping_rows() -> None:
    with pytest.raises(ValueError, match="explicitly labeled"):
        report([sample(), replace(sample(), label=None)], [0.7, 0.2])
    with pytest.raises(ValueError, match="align"):
        report([sample()], [])


def test_report_never_coerces_invalid_labels_to_integers() -> None:
    with pytest.raises(ValueError, match="explicitly labeled"):
        report([replace(sample(), label=True)], [0.8])


def test_valid_report_preserves_ticker_and_date_populations() -> None:
    first = sample()
    second = replace(first, ticker="000001", label=0)
    rows = [first, second]
    scores = [0.8, 0.7]
    measured = report(rows, scores)
    assert measured["by_ticker"]["005930"] == evaluate_metrics([1], [0.8])
    assert measured["by_ticker"]["000001"] == evaluate_metrics([0], [0.7])
    assert measured["by_date_count"] == {"2026-01-02": 2}
    assert rows == [first, second] and scores == [0.8, 0.7]
