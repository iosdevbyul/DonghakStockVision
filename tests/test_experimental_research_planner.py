from typing import Any

from donghak_stock_vision.models.experimental_research_planner import research_plan


def report(stagnation: int = 0) -> dict[str, Any]:
    return dict(
        suggested_budget={"total": 4},
        consecutive_non_improving_novel_sessions=stagnation,
        strategy={
            "exploration": {"observations": 3, "improvements": 3, "regressions": 0},
            "exploitation": {"observations": 1, "improvements": 0, "regressions": 1},
        },
    )


def test_observed_exploration_advantage() -> None:
    plan = research_plan(report(), 4)
    assert plan["schedule"] == ["exploration"] * 3 + ["exploitation"]
    assert plan["execution_authorized"] is False


def test_stagnation_and_budget_cap() -> None:
    plan = research_plan(report(2), 2)
    assert plan["schedule"] == ["exploration"] * 2
    assert research_plan(report(), 0)["schedule"] == []


def test_sparse_evidence_keeps_balanced_plan() -> None:
    value = report()
    value["strategy"]["exploration"]["observations"] = 1
    assert research_plan(value, 4)["schedule"] == ["exploration"] * 2 + ["exploitation"] * 2
