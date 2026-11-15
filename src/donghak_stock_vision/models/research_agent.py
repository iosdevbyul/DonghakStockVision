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
    selected = agent.propose(view)
    require(
        type(selected) is str and selected in {c["experiment_id"] for c in view["candidates"]},
        "research_agent_outside_plan",
    )
    return selected


class RuleBasedAgent:
    """Host pre-ranks using frozen rules and training-internal memory only."""

    def propose(self, view: dict[str, Any]) -> str:
        return str(view["candidates"][0]["experiment_id"])
