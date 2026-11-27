from collections.abc import Mapping

import pytest

from donghak_stock_vision.experimental_research_skills import ResearchSkill, SkillRegistry


def echo(arguments: Mapping[str, object]) -> Mapping[str, object]:
    return arguments


def test_allowlist_and_budget() -> None:
    registry = SkillRegistry((ResearchSkill("memory", "Read memory", echo),), 1)
    assert registry.catalog() == {"memory": "Read memory"}
    with pytest.raises(ValueError, match="unknown skill"):
        registry.invoke("shell", {})
    assert registry.remaining == 1
    assert registry.invoke("memory", {"query": "H1"}) == {"query": "H1"}
    assert registry.remaining == 0
    with pytest.raises(RuntimeError, match="budget"):
        registry.invoke("memory", {})


def test_duplicate_skills_rejected() -> None:
    skill = ResearchSkill("memory", "Read memory", echo)
    with pytest.raises(ValueError, match="duplicate"):
        SkillRegistry((skill, skill), 1)


def test_negative_budget_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        SkillRegistry((), -1)
