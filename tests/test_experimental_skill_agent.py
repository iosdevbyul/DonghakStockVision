import json
from typing import Any

import pytest

from donghak_stock_vision.storage.experimental_skill_agent import (
    historical_experiences,
    hypothesis_feedback,
    select_with_skills,
    strategy_options,
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
            dict(index=0, reason="inspect"),
            dict(index=0, reason="test linear", metric="mean_ap"),
        ]
    )

    def generate(prompt: str, schema: dict[str, Any]) -> str:
        assert "evidenceIndex" not in schema["properties"]
        return json.dumps(next(responses))

    selected, _ = select_with_skills(current, generate, grounded=True)
    assert selected["evidence"] == [{"experimentId": "prior", "metric": "mean_ap"}]
    assert selected["changes"] == {"family": ["forest", "linear"]}
    result = hypothesis_feedback(selected, [{"experiment_id": "allowed", "mean_ap": 0.5}])
    assert result["outcome"] == "supported"
    assert result["causal"] is False
    assert hypothesis_feedback(selected, [])["outcome"] == "unmeasured"


def test_grounded_stage_rejects_old_sentinel_and_recovers() -> None:
    current = view()
    current["history"] = [
        {
            "experiment_id": "prior",
            "state": "completed",
            "config": {"candidate": {"family": "forest", "value": 1}, "mask": "all"},
            "mean_ap": 0.4,
        }
    ]
    events: list[dict[str, Any]] = []
    responses = iter(
        [
            dict(index=0, reason="inspect"),
            dict(index=0, reason="invalid", metric="mean_ap", evidenceIndex=-1),
            dict(index=0, reason="test linear", metric="mean_ap"),
        ]
    )

    def generate(prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(next(responses))

    selected, _ = select_with_skills(current, generate, grounded=True, events=events)
    assert selected["evidence"] == [{"experimentId": "prior", "metric": "mean_ap"}]
    assert [e["state"] for e in events] == ["observed", "rejected", "selected"]


def test_predictive_regression_not_hidden_by_overfit_gain() -> None:
    proposal = {
        "experimentId": "allowed",
        "evidence": [{"metric": "overfit_gap"}],
        "referenceValue": 0.1,
        "referenceMetrics": {
            "mean_ap": 0.5,
            "worst_balanced_accuracy": 0.5,
            "baseline_ap_gain": 0.01,
            "overfit_gap": 0.1,
        },
        "expectedDirection": "decrease",
        "focusChange": "mask",
    }
    outcome = {
        "experiment_id": "allowed",
        "mean_ap": 0.48,
        "worst_balanced_accuracy": 0.5,
        "baseline_ap_gain": 0.01,
        "overfit_gap": 0.05,
    }
    result = hypothesis_feedback(proposal, [outcome])
    assert result["outcome"] == "supported"
    assert result["predictiveQuality"] == "regression"
    assert result["metricDeltas"]["mean_ap"] < 0


def test_adaptive_strategy_avoids_observed_regression() -> None:
    reference = view()["candidates"][0]
    alternative = {
        "experiment_id": "new",
        "config": {"candidate": {"family": "linear", "value": 2}, "mask": "all"},
    }
    regressed = {
        "experiment_id": "bad",
        "config": {"candidate": {"family": "forest", "value": 1}, "mask": "all"},
    }
    feedback = [
        {
            "referenceConfig": {"family": "linear", "value": 1, "mask": "all"},
            "candidateConfig": {"family": "forest", "value": 1, "mask": "all"},
            "predictiveQuality": "regression",
        }
    ]
    candidates = {0: regressed, 1: alternative}
    selected, decision = strategy_options(reference, candidates, feedback, adaptive=True)
    assert list(selected) == [1]
    assert decision["mode"] == "exploitation"
    selected, decision = strategy_options(reference, candidates, feedback, adaptive=False)
    assert selected == candidates
    assert decision["mode"] == "control"


def test_exploration_preserves_novel_transition() -> None:
    reference = view()["candidates"][0]
    known = {
        "experiment_id": "known",
        "config": {"candidate": {"family": "forest", "value": 1}, "mask": "all"},
    }
    novel = {
        "experiment_id": "novel",
        "config": {"candidate": {"family": "linear", "value": 2}, "mask": "all"},
    }
    experience = {
        "referenceConfig": {"family": "linear", "value": 1, "mask": "all"},
        "candidateConfig": {"family": "forest", "value": 1, "mask": "all"},
        "predictiveQuality": "improvement",
    }
    selected, decision = strategy_options(
        reference,
        {0: known, 1: novel},
        [experience | {"experimentId": "a"}, experience | {"experimentId": "b"}],
        adaptive=True,
    )
    assert list(selected) == [1]
    assert decision["mode"] == "exploration"


def test_partial_family_transition_has_support() -> None:
    reference = view()["candidates"][0]
    candidate = {
        "experiment_id": "new",
        "config": {"candidate": {"family": "forest", "value": 7}, "mask": "other"},
    }
    experience = {
        "experimentId": "old",
        "referenceConfig": {"family": "linear", "value": 1, "mask": "weekly"},
        "candidateConfig": {"family": "forest", "value": 3, "mask": "daily"},
        "predictiveQuality": "improvement",
    }
    _, decision = strategy_options(reference, {0: candidate}, [experience], adaptive=True)
    assert decision["scores"][0][1] > 0
    assert decision["supportCounts"] == [[0, 1]]


def test_parameter_only_candidate_is_visible() -> None:
    current = view()
    current["candidates"][0]["config"]["candidate"]["parameters"] = {"rate": 0.1}
    current["history"] = [
        {
            "experiment_id": "prior",
            "state": "completed",
            "config": {
                "candidate": {"family": "linear", "value": 1, "parameters": {"rate": 0.03}},
                "mask": "all",
            },
            "mean_ap": 0.4,
        }
    ]
    responses = iter(
        [
            dict(index=0, reason="inspect"),
            dict(index=0, reason="test rate", metric="mean_ap"),
        ]
    )

    def generate(prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(next(responses))

    selected, _ = select_with_skills(current, generate, grounded=True)
    assert selected["changes"] == {"parameters": [{"rate": 0.03}, {"rate": 0.1}]}


def test_historical_comparison_is_observational() -> None:
    metrics = dict(
        mean_ap=0.4,
        baseline_ap_gain=0.01,
        worst_balanced_accuracy=0.5,
        overfit_gap=0.05,
    )
    rows = [
        dict(
            experiment_id=str(i),
            state="completed",
            config={"candidate": {"family": "linear", "value": i}, "mask": "all"},
            **metrics,
        )
        for i in (1, 2)
    ]
    results = historical_experiences(rows)
    assert len(results) == 1
    assert results[0]["causal"] is False
    assert results[0]["source"] == "historical_matched_comparison"
