from typing import Any

import pytest

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.experimental_research_performance import performance


def row(name: str, ap: float, ba: float) -> dict[str, Any]:
    return dict(
        experiment_id=name,
        config={"name": name},
        state="completed",
        mean_ap=ap,
        worst_balanced_accuracy=ba,
    )


def session(*rows: dict[str, Any]) -> dict[str, Any]:
    return dict(
        session_pin="test",
        outcomes=list(rows),
        observations=[],
        rejected_proposals=0,
        proposal_wall_seconds=0.0,
    )


def test_separate_metric_leaders_and_duplicate_configs() -> None:
    old = row("old", 0.5, 0.5)
    ap = row("ap", 0.53, 0.49)
    ba = row("ba", 0.48, 0.55)
    replay = ap | {"experiment_id": "new-code"}
    result = performance([old], [session(ap), session(ba, replay)], [ap, ba], 4)
    assert result["cumulative_leaders"]["mean_ap"]["experiment_id"] == "ap"
    assert result["cumulative_leaders"]["worst_balanced_accuracy"]["experiment_id"] == "ba"
    assert result["timeline"][1]["duplicate_configurations"] == 1
    assert result["remaining_configurations"] == 0
    assert result["suggested_budget"]["total"] == 0


def test_stagnation_counts_novel_sessions_not_rejections() -> None:
    old = row("old", 0.5, 0.5)
    a, b, c = (row(name, 0.48, 0.49) for name in ("a", "b", "c"))
    result = performance([old], [session(a), session(), session(b)], [a, b, c], 4)
    assert result["consecutive_non_improving_novel_sessions"] == 2
    assert result["suggested_budget"]["exploration"] == 1
    assert result["suggested_budget"]["automatic"] is False


def test_strategy_uses_actual_ap_not_hypothesis_metric() -> None:
    old, new = row("old", 0.5, 0.5), row("new", 0.48, 0.52)
    item = dict(
        experimentId="new",
        semanticId=digest(new["config"]),
        strategyMode="exploration",
        metric="overfit_gap",
        observedValue=0.01,
        predictiveQuality="regression",
        proposal_wall_seconds=10.0,
    )
    value = session(new) | {"observations": [item]}
    result = performance([old], [value], [new], 4)
    stats = result["strategy"]["exploration"]
    assert stats["best_ap"] == pytest.approx(0.48)
    assert stats["above_initial_ap"] == 0
    assert stats["regressions"] == 1
    assert result["performance_approved"] is False
