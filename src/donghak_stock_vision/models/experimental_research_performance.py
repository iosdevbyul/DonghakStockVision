"""Descriptive internal research progress, not independent model validation."""

import math
from typing import Any

from donghak_stock_vision.data.learning import digest

EPSILON = 0.002
MODES = ("exploration", "exploitation", "control", "unknown")


def measured(row: dict[str, Any]) -> bool:
    return row["state"] == "completed" and all(
        type(row.get(k)) in (int, float) and math.isfinite(row[k])
        for k in ("mean_ap", "worst_balanced_accuracy")
    )


def identity(row: dict[str, Any]) -> str:
    return digest(row["config"])


def leaders(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if measured(row)]
    result: dict[str, Any] = {}
    for metric in ("mean_ap", "worst_balanced_accuracy"):
        best = max(valid, key=lambda row: (row[metric], row["experiment_id"]), default=None)
        result[metric] = (
            None
            if best is None
            else dict(
                value=best[metric],
                experiment_id=best["experiment_id"],
                config=best["config"],
            )
        )
    return result


def gain(before: dict[str, Any], after: dict[str, Any], metric: str) -> float | None:
    left, right = before[metric], after[metric]
    if left is None or right is None:
        return None
    return float(right["value"] - left["value"])


def strategy_summary(
    observations: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for mode in MODES:
        rows = [row for row in observations if row.get("strategyMode", "unknown") == mode]
        ap = [float(row["observedValue"]) for row in rows if row["metric"] == "mean_ap"]
        # AP is independently attached from the verified outcome for every hypothesis metric.
        ap = [float(row["actual_ap"]) for row in rows if "actual_ap" in row] or ap
        costs = sum(float(row["proposal_wall_seconds"]) for row in rows)
        incumbent = baseline["mean_ap"]
        above = (
            None
            if incumbent is None
            else sum(value > incumbent["value"] + EPSILON for value in ap)
        )
        result[mode] = dict(
            observations=len(rows),
            unique_configurations=len({row["semanticId"] for row in rows}),
            improvements=sum(row["predictiveQuality"] == "improvement" for row in rows),
            regressions=sum(row["predictiveQuality"] == "regression" for row in rows),
            above_initial_ap=above,
            best_ap=max(ap, default=None),
            proposal_wall_seconds=costs,
            observations_per_hour=len(rows) * 3600 / costs if costs > 0 else None,
        )
    return result


def performance(
    historical: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    budget: int,
) -> dict[str, Any]:
    """Use session order, count novel configurations, and keep separate metric leaders."""
    if type(budget) is not int or not 1 <= budget <= 4:
        raise ValueError("performance_budget")
    known = {identity(row): row for row in historical if measured(row)}
    initial = leaders(list(known.values()))
    observations: list[dict[str, Any]] = []
    timeline = []
    stagnant = 0
    for session in sessions:
        before = leaders(list(known.values()))
        completed = [row for row in session["outcomes"] if measured(row)]
        novel = 0
        for row in completed:
            key = identity(row)
            if key not in known:
                known[key] = row
                novel += 1
        by_id = {row["experiment_id"]: row for row in completed}
        for item in session["observations"]:
            row = by_id[item["experimentId"]]
            observations.append(item | {"actual_ap": row["mean_ap"]})
        after = leaders(list(known.values()))
        ap_gain = gain(before, after, "mean_ap")
        ba_gain = gain(before, after, "worst_balanced_accuracy")
        if novel:
            if ap_gain is None or ba_gain is None or max(ap_gain, ba_gain) > EPSILON:
                stagnant = 0
            else:
                stagnant += 1
        timeline.append(
            dict(
                session_pin=session["session_pin"],
                completed=len(completed),
                new_configurations=novel,
                duplicate_configurations=len(completed) - novel,
                failed=sum(row["state"] == "failed" for row in session["outcomes"]),
                rejected_proposals=session["rejected_proposals"],
                proposal_wall_seconds=session["proposal_wall_seconds"],
                session_leaders=leaders(completed),
                cumulative_leaders=after,
                ap_leader_gain=ap_gain,
                ba_leader_gain=ba_gain,
            )
        )
    remaining = len({identity(row) for row in candidates} - known.keys())
    next_budget = min(budget, remaining)
    stats = strategy_summary(observations, initial)
    # Advisory only: require new evidence, not repeated fits or automatic expansion.
    exploration = next_budget if stagnant >= 2 else (next_budget + 1) // 2
    return dict(
        initial_leaders=initial,
        cumulative_leaders=leaders(list(known.values())),
        timeline=timeline,
        strategy=stats,
        unique_completed_configurations=len(known),
        remaining_configurations=remaining,
        consecutive_non_improving_novel_sessions=stagnant,
        suggested_budget=dict(
            total=next_budget,
            exploration=exploration,
            exploitation=next_budget - exploration,
            automatic=False,
            reason="exhausted" if not remaining else "stagnation" if stagnant >= 2 else "balanced",
        ),
        independent_generalization="NOT_ESTABLISHED",
        performance_approved=False,
        execution_authorized=False,
        pit_qualified=False,
    )
