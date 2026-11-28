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


def next_mode(
    initial: dict[str, Any],
    feedback: list[dict[str, Any]],
    step: int,
) -> dict[str, Any]:
    """Revise the frozen schedule from completed outcomes, not untested hypotheses."""
    if not 0 <= step < initial["total"]:
        raise ValueError("planner_step")
    measured = [
        row
        for row in feedback
        if row.get("predictiveQuality") in ("improvement", "stable", "regression")
    ]
    planned = initial["schedule"][step]
    mode, reason = planned, "initial_schedule"
    if measured:
        latest = measured[-1]
        quality = latest["predictiveQuality"]
        if quality == "regression":
            mode, reason = "exploration", "previous_regression"
        elif quality == "stable":
            mode, reason = "exploration", "previous_stagnation"
        elif step % 3 != 0:
            mode, reason = "exploitation", "previous_improvement"
        else:
            mode, reason = "exploration", "mandatory_exploration"
    return dict(
        mode=mode,
        original_mode=planned,
        reason=reason,
        step=step,
        evidence_experiment_ids=[row["experimentId"] for row in measured],
    )
