"""Finite paired development study; explicitly prepared private session only."""
import argparse
import json
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.experimental_skill_agent import SkillAgent
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, terminals
from donghak_stock_vision.storage.autonomous_runner import run as run_worker
from donghak_stock_vision.storage.llm_providers import OllamaProvider
from donghak_stock_vision.storage.memory_guided_research import (
    VERSION,
    arm_root,
    load,
    prepare,
    report,
    run,
)
from donghak_stock_vision.storage.research_resources import lock


def compare(root: Path, pin: str) -> dict[str, Any]:
    frozen = load(root, pin)
    if frozen["version"] != VERSION or not root.name.startswith("skill-experiment-"):
        raise ValueError("skill_requires_dedicated_v1_session")
    budget = frozen["request"]["budget"]
    target = arm_root(root, "gemma_blind")
    plan, _ = context(target, frozen["plan_id"])
    # The existing runner opens confirmation only after full plan exhaustion.
    if len(plan["configurations"]) <= budget:
        raise ValueError("skill_confirmation_guard")
    allowed = frozenset(c["experiment_id"] for c in frozen["candidates"])
    baseline = arm_root(root, "gemma_memory")
    for _ in range(budget):
        with closing(ReadOnlyAnalysisStore(baseline / DB)) as store:
            if len(terminals(store)) >= budget:
                break
        run(root, pin, "gemma_memory")
    with lock(root, ".session.lock"):
        for _ in range(budget):
            with closing(ReadOnlyAnalysisStore(target / DB)) as store:
                done = terminals(store)
                count = len(store.all("llm_proposal"))
            if len(done) >= budget or count >= budget:
                break
            config = ModelConfig(**frozen["request"]["model"])
            agent = SkillAgent(target, lambda: OllamaProvider(config))

            def project(view: dict[str, Any]) -> dict[str, Any]:
                history = [
                    h for h in frozen["memory"] + view["history"]
                    if h["config"]["goal"]["horizon"] == frozen["request"]["horizon"]
                    and h["config"]["stock_policy"] == frozen["request"]["stock_policy"]
                ]
                unique = {h["experiment_id"]: h for h in history}
                return view | {
                    "history": list(unique.values())[-36:],
                    "candidates": [
                        c for c in view["candidates"] if c["experiment_id"] in allowed
                    ],
                    "leader": None,
                }

            result = run_worker(
                target, frozen["plan_id"], 1, agent="managed",
                agent_factory=lambda r, p: agent, view_transform=project,
                strict_agent=True, allowed_experiments=allowed,
            ).to_dict()
            if result["pause_reason"]:
                break
    value = report(root, pin)
    return {
        "baseline": value["arms"]["gemma_memory"],
        "experimental_skills": value["arms"]["gemma_blind"],
        "dataset_id": frozen["dataset_id"],
        "independent_generalization": "NOT_ESTABLISHED",
        "performance_approved": False,
        "execution_authorized": False,
        "pit_qualified": False,
        "note": "Internal adaptive development comparison; not final-test performance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    if not args.root.name.startswith("skill-experiment-"):
        parser.error("root must start with skill-experiment-")
    request = json.loads(args.request.read_text())
    if request["version"] != VERSION:
        parser.error("requires memory_guided_session_v1")
    pin = prepare(request, args.root, args.pins)
    print(json.dumps(compare(args.root, pin), indent=2))


if __name__ == "__main__":
    main()
