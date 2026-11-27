"""Read-only skills over the host's bounded, training-internal agent view."""

from copy import deepcopy
from typing import Any

from donghak_stock_vision.experimental_research_skills import (
    Payload,
    ResearchSkill,
    SkillRegistry,
)


def build_view_skills(view: dict[str, Any], budget: int = 4) -> SkillRegistry:
    if view.get("version") != "research_agent_view_v1":
        raise ValueError("unsupported research view")
    if len(view["history"]) > 36 or len(view["candidates"]) > 36:
        raise ValueError("research view exceeds bounds")
    snapshot = deepcopy(view)
    history = snapshot["history"]

    def memory(arguments: Payload) -> Payload:
        if set(arguments) != {"experiment_id"}:
            raise ValueError("expected experiment_id")
        for row in history:
            if row["experiment_id"] == arguments["experiment_id"]:
                return deepcopy(dict(row))
        raise ValueError("unknown history experiment")

    def diagnose(arguments: Payload) -> Payload:
        if arguments:
            raise ValueError("diagnosis takes no arguments")
        rows = []
        for row in history:
            if row["state"] != "completed":
                continue
            rows.append(
                {
                    "experiment_id": row["experiment_id"],
                    "overfit_gap": row["overfit_gap"],
                    "baseline_ap_gain": row["baseline_ap_gain"],
                    "worst_balanced_accuracy": row["worst_balanced_accuracy"],
                }
            )
        return {"observations": rows}

    def candidates(arguments: Payload) -> Payload:
        if arguments:
            raise ValueError("candidates takes no arguments")
        return {"candidates": deepcopy(snapshot["candidates"])}

    return SkillRegistry(
        (
            ResearchSkill("memory", "Read one training-internal experiment by ID", memory),
            ResearchSkill("diagnosis", "Inspect observed internal validation metrics", diagnose),
            ResearchSkill("candidates", "List frozen host-approved candidates", candidates),
        ),
        budget,
    )
