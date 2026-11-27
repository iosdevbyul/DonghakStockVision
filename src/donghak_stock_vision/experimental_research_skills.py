"""Isolated, opt-in research tool dispatch; no production integration."""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

Payload: TypeAlias = Mapping[str, object]
Handler: TypeAlias = Callable[[Payload], Payload]


@dataclass(frozen=True)
class ResearchSkill:
    name: str
    description: str
    handler: Handler


class SkillRegistry:
    """Explicit allowlist with a per-session invocation budget."""

    def __init__(self, skills: tuple[ResearchSkill, ...], budget: int) -> None:
        if budget < 0:
            raise ValueError("negative budget")
        entries = {skill.name: skill for skill in skills}
        if len(entries) != len(skills):
            raise ValueError("duplicate skill")
        self._skills = MappingProxyType(entries)
        self._remaining = budget

    @property
    def remaining(self) -> int:
        return self._remaining

    def catalog(self) -> dict[str, str]:
        return {name: skill.description for name, skill in self._skills.items()}

    def invoke(self, name: str, arguments: Payload) -> Payload:
        if name not in self._skills:
            raise ValueError("unknown skill")
        if self._remaining == 0:
            raise RuntimeError("research budget exhausted")
        self._remaining -= 1
        return self._skills[name].handler(arguments)
