"""Development partition boundaries and abstention preserve the existing minima."""

from datetime import date, timedelta
from typing import Any

from donghak_stock_vision.models.autonomous_strategy import choose, partitions, stable
from donghak_stock_vision.models.predictive_research import summary
from donghak_stock_vision.storage import predictive_research as r3
from donghak_stock_vision.storage.autonomous_strategy import _seeds


def test_time_block_purges_cross_boundary_labels() -> None:
    rows = [
        dict(
            ticker="first",
            input_date="2023-10-13",
            target_date="2023-10-17",
            label=1,
            scores={"model": 0.8},
        ),
        dict(
            ticker="second",
            input_date="2023-10-16",
            target_date="2023-10-18",
            label=0,
            scores={"model": 0.25},
        ),
    ]
    panels = partitions(rows, [["first"], ["second"]])
    assert panels["ticker_group_1"]["metrics"]["n"] == 1
    assert panels["time_block_1"]["metrics"]["n"] == 0
    assert panels["time_block_2"]["metrics"]["n"] == 1


def test_stability_abstains_when_split_has_fewer_than_existing_minima() -> None:
    def rows(correct: bool) -> list[dict[str, Any]]:
        result = []
        for i in range(40):
            day = date(2023, 8, 1) + timedelta(days=i * 3)
            for j in range(8):
                label = j % 2
                result.append(
                    dict(
                        ticker=str(j),
                        input_date=day.isoformat(),
                        target_date=(day + timedelta(days=2)).isoformat(),
                        label=label,
                        regimes=["sideways"],
                        scores={"model": float(label if correct else not label)},
                    )
                )
        return result

    groups = [[str(i) for i in range(4)], [str(i) for i in range(4, 8)]]

    def measured(correct: bool) -> dict[str, Any]:
        data = rows(correct)
        return dict(summary=summary(data, "model"), blocks=partitions(data, groups))

    result = stable(measured(True), measured(False), 1)
    assert not result["passed"]
    assert not result["evidence_sufficient"]
    assert result["worst_ba_delta"] == 1


def test_failure_memory_can_change_choice_without_peer_results() -> None:
    def candidate(key: str, family: str, value: float) -> dict[str, Any]:
        return dict(
            experiment_id=key,
            config=dict(mask="v2_legacy", candidate=dict(family=family, value=value)),
        )

    control = candidate("control", "random_forest", 3) | dict(
        state="completed",
        stability_ap=0.5,
        largest_train_gap=0.0,
        summary=dict(metrics=dict(up_predictions=5, down_predictions=5)),
    )
    options = [candidate("a", "hist_gradient_boosting", 7), candidate("b", "linear", 1.0)]
    failed = candidate("prior_failure", "hist_gradient_boosting", 15) | dict(state="failed")
    selected, record = choose(options, [control], [failed], control, 1)
    assert record["counterfactual"] == "a"
    assert selected["experiment_id"] == "b"
    assert record["candidate_evidence"]["a"]["references"] == ["prior_failure"]


def test_larger_r3_challenger_uses_matched_development_seed() -> None:
    def trial(key: str, candidate: str, mask: str) -> dict[str, Any]:
        return dict(
            experiment_id=key,
            config=r3._candidate_configuration(5, 16, candidate, mask),
            evaluation_population="same",
            state="completed",
        )

    baseline = trial("control", r3.RF, "v2_legacy")
    matched = trial("matched", r3.LR, "v2_multi")
    plan = dict(retained_r3_challengers={"5": "retained_24_tickers"})
    assert _seeds([baseline, matched], plan, 5) == [baseline, matched]
