from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from typing import Any

import pytest

from donghak_stock_vision.data.learning import Minimums, Sample
from donghak_stock_vision.models.fit import fit_linear
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.metrics import evaluate_metrics
from donghak_stock_vision.signals.dataset import (
    build_dataset,
    population,
    requirements,
    sample_weights,
    split_dataset,
)


def test_common_dates_and_purge(snapshot: tuple[str, dict[str, Any]]) -> None:
    samples, _ = build_dataset(snapshot[1])
    split = split_dataset(samples, "historical_research")
    validation = datetime.fromisoformat(split["validation_start"]).date()
    test = datetime.fromisoformat(split["test_start"]).date()
    assert split["purged"]
    seen: dict[date, str] = {}
    for part, raw in split["partitions"].items():
        for value in raw:
            sample = Sample.from_dict(value)
            day = sample.anchor_at.date()
            assert day not in seen or seen[day] == part
            seen[day] = part
            if part == "train":
                assert sample.label_end is not None and sample.label_end < validation
            if part == "validation":
                assert sample.label_end is not None and sample.label_end < test
    # Boundary equality is purged, not labeled negative.
    boundary_sample = replace(samples[15], label=1, label_end=validation)
    new = split_dataset([boundary_sample, *samples], "historical_research")
    assert any(s["anchor_at"] == boundary_sample.anchor_at.isoformat() for s in new["purged"])


def test_pit_late_label_receipt_purged(snapshot: tuple[str, dict[str, Any]]) -> None:
    samples, _ = build_dataset(snapshot[1])
    research = split_dataset(samples, "historical_research")
    pit = split_dataset(samples, "point_in_time")
    assert research["partitions"]["train"]
    assert not pit["partitions"]["train"]  # bulk-received labels would be unavailable at boundary


def test_conditional_minimums_and_no_outside_negative(snapshot: tuple[str, dict[str, Any]]) -> None:
    samples, _ = build_dataset(snapshot[1])
    split = split_dataset(samples, "historical_research")
    for direction in ("up", "down"):
        train = population(split, direction, "train")
        assert all((s.values[2] < 0) == (direction == "up") for s in train)
        assert not requirements(samples, split, direction, Minimums.synthetic())["failures"]
        production = requirements(samples, split, direction, Minimums())
        assert production["failures"]
        assert production["required"]["train_rows"] == 1000
        assert production["required"]["train_class"] == 50
        assert production["required"]["holdout_class"] == 20
    altered = deepcopy(split)
    altered["partitions"]["train"] = [
        s for s in altered["partitions"]["train"] if s["direction"] == "up"
    ]
    assert population(altered, "up", "train") == population(split, "up", "train")
    assert requirements(samples, altered, "down", Minimums.synthetic())["failures"]


def test_scaler_and_fit_train_only_with_conditional_weights(
    snapshot: tuple[str, dict[str, Any]],
) -> None:
    import numpy as np

    samples, _ = build_dataset(snapshot[1])
    split = split_dataset(samples, "historical_research")
    train = population(split, "up", "train")
    params = fit_linear(train, 1.0)
    assert params["mean"] == pytest.approx(
        np.average(
            np.asarray([s.values for s in train]), axis=0, weights=sample_weights(train)
        ).tolist()
    )
    assert params == fit_linear(train, 1.0)
    changed = deepcopy(split)
    for part in ("validation", "test"):
        for row in changed["partitions"][part]:
            row["values"] = [1e6] * 8
    for row in changed["partitions"]["train"]:
        if row["direction"] == "down":
            row["values"] = [1e9] * 8
    assert fit_linear(population(changed, "up", "train"), 1.0) == params
    prediction, reasons = score(params, train[0].values)
    assert 0 <= prediction <= 1 and len(reasons["contributions"]) == 8


def test_ap_matches_sklearn_and_undefined_cases() -> None:
    from sklearn.metrics import average_precision_score

    labels, scores = [0, 1, 0, 1, 0, 1], [0.3, 0.9, 0.3, 0.5, 0.9, 0.5]
    assert evaluate_metrics(labels, scores)["pr_auc_ap"] == pytest.approx(
        average_precision_score(labels, scores)
    )
    no_positive = evaluate_metrics([0, 0], [0.1, 0.1])
    assert no_positive["precision"] is None and no_positive["recall"] is None
    assert no_positive["pr_auc_ap"] is None
    assert no_positive["undefined_reasons"]["pr_auc_ap"] == "empty_or_single_class"
