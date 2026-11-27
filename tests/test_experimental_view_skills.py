from typing import Any

import pytest

from donghak_stock_vision.experimental_view_skills import build_view_skills


def sample_view() -> dict[str, Any]:
    return {
        "version": "research_agent_view_v1",
        "history": [
            {
                "experiment_id": "old",
                "state": "completed",
                "overfit_gap": 0.12,
                "baseline_ap_gain": -0.03,
                "worst_balanced_accuracy": 0.48,
            }
        ],
        "candidates": [{"experiment_id": "new"}],
    }


def test_read_only_view_skills() -> None:
    view = sample_view()
    registry = build_view_skills(view)
    view["history"][0]["overfit_gap"] = 99
    assert registry.invoke("memory", {"experiment_id": "old"})["overfit_gap"] == 0.12
    assert registry.invoke("diagnosis", {}) == {
        "observations": [
            {
                "experiment_id": "old",
                "overfit_gap": 0.12,
                "baseline_ap_gain": -0.03,
                "worst_balanced_accuracy": 0.48,
            }
        ]
    }
    assert registry.invoke("candidates", {}) == {"candidates": [{"experiment_id": "new"}]}
    with pytest.raises(ValueError, match="unknown skill"):
        registry.invoke("experiment", {})


def test_memory_requires_known_id() -> None:
    registry = build_view_skills(sample_view())
    with pytest.raises(ValueError, match="unknown history"):
        registry.invoke("memory", {"experiment_id": "missing"})
    with pytest.raises(ValueError, match="expected"):
        registry.invoke("memory", {"path": "/tmp"})


def test_invalid_view_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        build_view_skills({})
