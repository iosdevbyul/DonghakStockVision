"""Bounded proposal boundary for a future local research agent.

No LLM runtime, credentials, model loading, dataset access or execution authority.
The host owns the shared heavy-work reservation; proposals cannot modify a plan.
"""

from typing import Any, Protocol

from donghak_stock_vision.backtest.validation import require


class ResearchAgent(Protocol):
    def propose(self, view: dict[str, Any]) -> str:
        """Return one allowed experiment ID; never executable code or new parameters."""
        ...


def select_proposal(agent: ResearchAgent, view: dict[str, Any]) -> str:
    require(view["version"] == "research_agent_view_v1", "research_agent_view_version")
    require(
        len(view["candidates"]) <= 36 and len(view["history"]) <= 36, "research_agent_view_bound"
    )
    from copy import deepcopy

    allowed = {c["experiment_id"] for c in view["candidates"]}
    selected = agent.propose(deepcopy(view))
    require(
        type(selected) is str and selected in allowed,
        "research_agent_outside_plan",
    )
    return selected


class RuleBasedAgent:
    """Host pre-ranks using frozen rules and training-internal memory only."""

    def propose(self, view: dict[str, Any]) -> str:
        return str(view["candidates"][0]["experiment_id"])


def validate_gemma_proposal(value: dict[str, Any], view: dict[str, Any]) -> str:
    """Map untrusted text/JSON onto an exact frozen, unexecuted configuration."""
    from donghak_stock_vision.backtest.validation import fields

    fields(
        value,
        "proposalVersion experimentId hypothesis modelFamily featureSet hyperparameters "
        "researchGoal parentExperimentId expectedImprovement rationale",
    )
    require(value["proposalVersion"] == "gemma_research_proposal_v1", "gemma_proposal_version")
    for key in ("hypothesis", "expectedImprovement", "rationale"):
        require(type(value[key]) is str and 0 < len(value[key]) <= 600, "gemma_proposal_text_bound")
    require(type(value["experimentId"]) is str, "gemma_experiment_id")
    hp = fields(value["hyperparameters"], "value seed")
    require(type(hp["value"]) in (int, float) and type(hp["seed"]) is int, "gemma_parameter_types")
    require(
        value["parentExperimentId"] is None or type(value["parentExperimentId"]) is str,
        "gemma_parent_type",
    )
    choices = {c["experiment_id"]: c["config"] for c in view["candidates"]}
    require(value["experimentId"] in choices, "gemma_duplicate_or_outside_plan")
    c = choices[value["experimentId"]]
    require(
        value["modelFamily"] == c["candidate"]["family"]
        and value["featureSet"] == c["mask"]
        and value["hyperparameters"] == dict(value=c["candidate"]["value"], seed=42)
        and value["researchGoal"] == c["goal"]["label_version"],
        "gemma_configuration_substitution",
    )
    require(
        value["parentExperimentId"] is None
        or value["parentExperimentId"] in {r["experiment_id"] for r in view["history"]},
        "gemma_parent_owner",
    )
    return str(value["experimentId"])


def decode_gemma_proposal(text: str) -> dict[str, Any]:
    import json

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, "gemma_duplicate_json_key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError("gemma_nonfinite_json")

    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    require(isinstance(value, dict), "gemma_proposal_object")
    return dict(value)
