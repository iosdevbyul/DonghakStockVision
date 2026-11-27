import json
from typing import Any

import pytest

from donghak_stock_vision.storage.experimental_skill_agent import select_with_skills


def view() -> dict[str, Any]:
    return {
        "version": "research_agent_view_v1",
        "goal": {"label_version": "H1"},
        "dataset_version": "test",
        "history": [],
        "candidates": [
            {
                "experiment_id": "allowed",
                "config": {"candidate": {"family": "linear", "value": 1}, "mask": "all"},
            }
        ],
    }


def test_tool_then_experiment() -> None:
    responses = iter(
        [
            {"action": "dataset", "index": 0, "reason": "inspect scope"},
            {"action": "experiment", "index": 0, "reason": "test candidate"},
        ]
    )

    def generate(prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(next(responses))

    selected, trace = select_with_skills(view(), generate)
    assert selected["experimentId"] == "allowed"
    assert trace[0]["action"] == "dataset"


def test_outside_candidate_rejected() -> None:
    def generate(prompt: str, schema: dict[str, Any]) -> str:
        return '{"action":"experiment","index":1,"reason":"bad"}'

    with pytest.raises(ValueError, match="candidate_index"):
        select_with_skills(view(), generate)


def test_duplicate_rejected() -> None:
    def generate(prompt: str, schema: dict[str, Any]) -> str:
        return '{"action":"dataset","index":0,"reason":"repeat"}'

    with pytest.raises(ValueError, match="duplicate"):
        select_with_skills(view(), generate)
