"""R3 matched-panel decisions and inherited evidence minima."""

from datetime import date, timedelta
from typing import Any

from donghak_stock_vision.models.predictive_research import compare, select, summary


def panel(days: int) -> list[dict[str, Any]]:
    rows = []
    for i in range(days):
        day = date(2023, 8, 1) + timedelta(days=i * 3)
        for j in range(4):
            label = j % 2
            scores = {"legacy_control": float(not label), "challenger": float(label)}
            rows.append(
                dict(
                    ticker=f"{j:06}",
                    input_date=day.isoformat(),
                    target_date=(day + timedelta(days=2)).isoformat(),
                    label=label,
                    regimes=["sideways"],
                    scores=scores,
                    correct={key: int(score >= 0.5) == label for key, score in scores.items()},
                )
            )
    return rows


def test_matched_improvement_maintains_existing_population_gate() -> None:
    for days, status in [(19, "INSUFFICIENT_EVIDENCE"), (50, "INTERNAL_IMPROVEMENT_OBSERVED")]:
        rows = panel(days)
        measures = {k: summary(rows, k) for k in ("legacy_control", "challenger")}
        compared = compare(rows, measures, "legacy_control", "challenger", 5)
        assert compared["status"] == status
        assert compared["transitions"] == dict(
            incorrect_to_correct=days * 4, correct_to_incorrect=0
        )


def test_r2_h5_guard_rejects_one_way_and_excess_fpr() -> None:
    def trial(key: str, fpr: float, ba: float, down: int = 50) -> dict[str, Any]:
        return dict(
            experiment_id=key,
            summary=dict(
                metrics=dict(
                    balanced_accuracy=ba,
                    accuracy=ba,
                    brier_score=1 - ba,
                    pr_auc_ap=ba,
                    fpr=fpr,
                    recall=0.6,
                    up_predictions=50,
                    down_predictions=down,
                )
            ),
        )

    control = trial("control", 0.3, 0.5)
    winner, reason = select(
        [
            control,
            trial("bad_fpr", 0.33, 0.8),
            trial("one_way", 0.31, 0.9, 0),
            trial("guarded", 0.31, 0.6),
        ],
        5,
        control,
    )
    assert winner["experiment_id"] == "guarded"
    assert reason == "development_non_regression"
