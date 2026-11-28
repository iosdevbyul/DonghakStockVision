from typing import Any

import pytest

from donghak_stock_vision.storage.experimental_experience_memory import verified_feedback
from donghak_stock_vision.storage.experimental_skill_agent import configuration


def row(identity: str, ap: float, value: int) -> dict[str, Any]:
    return {
        "experiment_id": identity,
        "state": "completed",
        "config": {"candidate": {"family": "linear", "value": value}, "mask": "all"},
        "mean_ap": ap,
        "baseline_ap_gain": ap - 0.4,
        "worst_balanced_accuracy": 0.51,
        "overfit_gap": 0.03,
    }


def test_verified_feedback_recomputes_observations() -> None:
    reference, outcome = row("reference", 0.45, 1), row("outcome", 0.47, 2)
    proposal = {
        "experimentId": "outcome",
        "evidence": [{"experimentId": "reference", "metric": "mean_ap"}],
        "referenceValue": 0.45,
        "referenceConfig": configuration(reference),
        "candidateConfig": configuration(outcome),
        "referenceMetrics": {"mean_ap": 0.99},
        "expectedDirection": "increase",
        "focusChange": "value",
        "strategy": {"mode": "exploration"},
    }
    result = verified_feedback(proposal, reference, outcome)
    assert result["delta"] == pytest.approx(0.02)
    assert result["predictiveQuality"] == "improvement"
    assert result["strategyMode"] == "exploration"
    with pytest.raises(ValueError, match="experience_proposal_binding"):
        verified_feedback(proposal | {"referenceValue": 0.99}, reference, outcome)
