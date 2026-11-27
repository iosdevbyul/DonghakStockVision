import json
from typing import Any

import pytest

from donghak_stock_vision.storage.experimental_skill_agent import (
    hypothesis_feedback,
    select_with_skills,
)


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


def test_invalid_choice_corrected_within_budget() -> None:
    events: list[dict[str, Any]] = []
    answers = iter(
        [
            '{"action":"experiment","index":9,"reason":"bad"}',
            '{"action":"dataset","index":0,"reason":"inspect"}',
            '{"action":"experiment","index":0,"reason":"valid"}',
        ]
    )

    def generate(prompt: str, schema: dict[str, Any]) -> str:
        if events and len(events) == 2:
            assert "skill_candidate_index" in prompt
        return next(answers)

    selected, trace = select_with_skills(view(), generate, events=events)
    assert selected["experimentId"] == "allowed"
    assert [e["state"] for e in events] == ["rejected", "observed", "selected"]
    assert len(trace) == 1


def test_rejected_trace_retained() -> None:
    events: list[dict[str, Any]] = []

    def generate(prompt: str, schema: dict[str, Any]) -> str:
        return '{"action":"dataset","index":0,"reason":"repeat"}'

    with pytest.raises(ValueError, match="duplicate"):
        select_with_skills(view(), generate, events=events)
    assert [e["state"] for e in events] == ["observed", "rejected", "rejected"]
    assert "result" in events[0]


def test_grounded_selection_and_feedback() -> None:
    current = view()
    current["history"] = [
        {
            "experiment_id": "prior",
            "state": "completed",
            "config": {"candidate": {"family": "forest", "value": 1}, "mask": "all"},
            "mean_ap": 0.4,
        }
    ]
    responses = iter(
        [
            dict(
                action="memory",
                index=0,
                reason="inspect",
                evidenceIndex=-1,
                metric="none",
                change="none",
            ),
            dict(
                action="experiment",
                index=0,
                reason="test linear",
                evidenceIndex=0,
                metric="mean_ap",
                change="family",
            ),
        ]
    )

    def generate(prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(next(responses))

    selected, _ = select_with_skills(current, generate, grounded=True)
    assert selected["evidence"] == [{"experimentId": "prior", "metric": "mean_ap"}]
    assert selected["changes"] == {"family": ["forest", "linear"]}
    result = hypothesis_feedback(selected, [{"experiment_id": "allowed", "mean_ap": 0.5}])
    assert result["outcome"] == "supported"
    assert result["causal"] is False
    assert hypothesis_feedback(selected, [])["outcome"] == "unmeasured"
