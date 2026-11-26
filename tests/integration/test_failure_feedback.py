"""Offline checks for fixed H5 feedback metrics and evidence gates."""

from typing import Any

import pytest

from donghak_stock_vision.models.failure_feedback import calibrated, compare, prior_offset, readout


def _rows() -> list[dict[str, Any]]:
    return [
        dict(
            ticker=f"{i:06}",
            input_date="2025-10-01",
            target_date="2025-10-15",
            label=label,
            scores=dict(original=original, calibrated=adjusted, training_prior=0.4),
            correct=dict(
                original=(original >= 0.5) == label, calibrated=(adjusted >= 0.5) == label
            ),
        )
        for i, (label, original, adjusted) in enumerate(
            [(1, 0.9, 0.8), (0, 0.7, 0.4), (0, 0.6, 0.4), (0, 0.2, 0.1), (0, 0.1, 0.1)]
        )
    ]


def test_calibration_only_offset_matches_prior_and_preserves_order() -> None:
    scores = [0.0, 0.4, 0.5, 0.6, 1.0]
    offset = prior_offset([0, 0, 0, 0, 1], scores)
    adjusted = calibrated(scores, offset)
    assert adjusted == sorted(adjusted)
    assert sum(adjusted) / len(adjusted) == pytest.approx(0.2)
    assert offset < 0
    with pytest.raises(ValueError, match="feedback_calibration_labels"):
        prior_offset([0] * len(scores), scores)


def test_fpr_fdr_and_transitions_preserve_denominators() -> None:
    rows = _rows()
    m = readout(rows, "original")
    assert m["fpr"] == 0.5
    assert m["fdr"] == pytest.approx(2 / 3)
    assert m["correct"] == 3 and m["incorrect"] == 2
    result = compare(rows, "calibration")
    assert result["criteria_met"] == dict(fpr=True, recall=True, brier=True, predicts_up=True)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["transitions"] == dict(incorrect_to_correct=2, correct_to_incorrect=0)


def test_no_up_predictions_do_not_pass_calibration_hypothesis() -> None:
    rows = _rows()
    for row in rows:
        row["scores"]["calibrated"] = 0.25
        row["correct"]["calibrated"] = row["label"] == 0
    result = compare(rows, "calibration")
    assert result["measures"]["calibrated"]["fdr"] is None
    assert not result["criteria_met"]["predicts_up"]
    assert not result["criteria_met"]["recall"]
