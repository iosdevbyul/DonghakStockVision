"""Development partition boundaries and abstention preserve the existing minima."""

from datetime import date, timedelta
from typing import Any

from donghak_stock_vision.models.autonomous_strategy import partitions, stable
from donghak_stock_vision.models.predictive_research import summary


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
