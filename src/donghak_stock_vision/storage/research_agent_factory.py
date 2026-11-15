"""Compatibility factories; research orchestration consumes an audited agent port."""

from pathlib import Path
from typing import Any, Protocol

from donghak_stock_vision.models.research_agent import ResearchAgent


class AuditedAgent(ResearchAgent, Protocol):
    audit: dict[str, Any]
    unloaded: bool


def legacy_gemma(root: Path, plan: dict[str, Any]) -> AuditedAgent:
    from donghak_stock_vision.storage.gemma_research import Gemma4ResearchAgent

    return Gemma4ResearchAgent(root, plan["agent_model_digest"])
