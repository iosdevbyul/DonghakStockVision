"""Bounded, deterministic scheduling from verified internal research performance."""

from typing import Any


def research_plan(report: dict[str, Any], budget: int) -> dict[str, Any]:
    if type(budget) is not int or not 0 <= budget <= 4:
        raise ValueError("planner_budget")
    total = min(budget, report["suggested_budget"]["total"])
    stats = report["strategy"]
    explore, exploit = stats["exploration"], stats["exploitation"]

    def rate(row: dict[str, Any]) -> float:
        return float((row["improvements"] - row["regressions"]) / (row["observations"] + 2))

    if report["consecutive_non_improving_novel_sessions"] >= 2:
        count, reason = total, "stagnation"
    elif explore["observations"] >= 2 and rate(explore) > rate(exploit):
        count, reason = (3 * total + 3) // 4, "exploration_evidence"
    else:
        count, reason = (total + 1) // 2, "balanced"
    schedule = ["exploration"] * count + ["exploitation"] * (total - count)
    return dict(
        policy="performance_planner_v1",
        schedule=schedule,
        total=total,
        reason=reason if total else "exhausted",
        independent_generalization="NOT_ESTABLISHED",
        execution_authorized=False,
    )
