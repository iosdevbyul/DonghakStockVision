"""Exercise the real agent and SQLite audit across sequential measured outcomes."""

import json
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB
from donghak_stock_vision.storage.experimental_skill_agent import SkillAgent


class ScriptedProvider:
    config: ModelConfig
    unloaded = True

    def __init__(self, answer: Callable[[str, dict[str, Any]], str]) -> None:
        self.answer = answer
        self.metadata: dict[str, Any] = {}

    def discover(self) -> dict[str, Any]:
        return {}

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        return self.answer(prompt, schema)

    def close(self) -> None:
        pass


def candidate(name: str, value: int) -> dict[str, Any]:
    return dict(
        experiment_id=name,
        config=dict(candidate=dict(family="linear", value=value), mask="all"),
    )


def measured(row: dict[str, Any], ap: float) -> dict[str, Any]:
    return row | dict(
        state="completed",
        mean_ap=ap,
        baseline_ap_gain=ap - 0.4,
        worst_balanced_accuracy=0.51,
        overfit_gap=0.03,
    )


def test_agent_revises_after_measured_failure_and_success(tmp_path: Path) -> None:
    with closing(AnalysisStore(tmp_path / DB)):
        pass
    reference = measured(candidate("reference", 1), 0.5)
    candidates = [candidate("a", 2), candidate("b", 3), candidate("c", 4)]
    prompts: list[dict[str, Any]] = []

    def answer(prompt: str, schema: dict[str, Any]) -> str:
        payload = json.loads(prompt)
        prompts.append(payload)
        choice: dict[str, Any] = dict(
            index=schema["properties"]["index"]["enum"][0], reason="test change"
        )
        if "metric" in schema["properties"]:
            choice["metric"] = "mean_ap"
        return json.dumps(choice)

    history = [reference]
    plan = dict(total=3, schedule=["exploration", "exploitation", "exploration"])
    decisions = []
    selected = []
    for step, ap in enumerate((0.45, 0.55, 0.56)):
        agent = SkillAgent(tmp_path, lambda: ScriptedProvider(answer))
        view = dict(
            version="research_agent_view_v1",
            goal={"label_version": "H1"},
            dataset_version="test",
            history=list(history),
            candidates=list(candidates),
            skill_initial_plan=plan,
            skill_plan_step=step,
        )
        chosen = agent.propose(view)
        decisions.append(agent.audit["adaptive_decision"])
        selected.append(chosen)
        row = next(row for row in candidates if row["experiment_id"] == chosen)
        history.append(measured(row, ap))
        candidates.remove(row)

    assert len(set(selected)) == 3
    assert [row["mode"] for row in decisions] == [
        "exploration",
        "exploration",
        "exploitation",
    ]
    assert decisions[1]["reason"] == "previous_regression"
    assert decisions[2]["reason"] == "previous_improvement"
    assert decisions[2]["evidence_experiment_ids"] == selected[:2]
    assert prompts[3]["feedback"][0]["quality"] == "regression"
    assert prompts[5]["feedback"][1]["quality"] == "improvement"
    with closing(AnalysisStore(tmp_path / DB)) as store:
        assert len(store.all("experimental_skill_proposal")) == 3
