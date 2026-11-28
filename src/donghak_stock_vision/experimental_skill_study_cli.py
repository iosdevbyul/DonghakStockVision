"""Finite paired development study; explicitly prepared private session only."""

import argparse
import json
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.experimental_research_performance import performance
from donghak_stock_vision.models.experimental_research_planner import research_plan
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, terminals
from donghak_stock_vision.storage.autonomous_runner import run as run_worker
from donghak_stock_vision.storage.experimental_experience_memory import restore, scope
from donghak_stock_vision.storage.experimental_skill_agent import (
    SkillAgent,
    historical_experiences,
    hypothesis_feedback,
)
from donghak_stock_vision.storage.llm_providers import OllamaProvider
from donghak_stock_vision.storage.memory_guided_research import (
    VERSION,
    arm_root,
    evidence,
    load,
    prepare,
    report,
    run,
)
from donghak_stock_vision.storage.research_resources import lock


def compare(
    root: Path,
    pin: str,
    *,
    adaptive: bool = True,
    skip_legacy_baseline: bool = False,
    previous: list[tuple[Path, Path]] | None = None,
) -> dict[str, Any]:
    with lock(root, ".skill-study.lock"):
        return _compare(root, pin, adaptive, skip_legacy_baseline, previous or [])


def _compare(
    root: Path,
    pin: str,
    adaptive: bool,
    skip_legacy_baseline: bool,
    previous: list[tuple[Path, Path]],
) -> dict[str, Any]:
    frozen = load(root, pin)
    if frozen["version"] != VERSION or not root.name.startswith("skill-experiment-"):
        raise ValueError("skill_requires_dedicated_v1_session")
    budget = frozen["request"]["budget"]
    target = arm_root(root, "gemma_blind")
    plan, data = context(target, frozen["plan_id"])
    # The existing runner opens confirmation only after full plan exhaustion.
    if len(plan["configurations"]) <= budget:
        raise ValueError("skill_confirmation_guard")
    details: list[dict[str, Any]] = []
    prior_rows, persistent, lineage = restore(previous, frozen, data, session_details=details)
    historical_rows = [row for row in frozen["memory"] if scope(row, frozen["request"])]
    excluded = {
        digest(row["config"])
        for row in prior_rows + (historical_rows if previous and adaptive else [])
        if row["state"] == "completed"
    }
    allowed = frozenset(
        c["experiment_id"] for c in frozen["candidates"] if digest(c["config"]) not in excluded
    )
    planner = None
    if previous and adaptive:
        summary = performance(historical_rows, details, frozen["candidates"], budget)
        planner = research_plan(summary, min(budget, len(allowed)))
    selection_scope = dict(sources=lineage, allowed=sorted(allowed), adaptive=adaptive)
    if planner is not None:
        selection_scope["planner"] = planner
    # Freeze the external lineage and actual selection scope before any inference.
    from donghak_stock_vision.storage.krx_backfill import write_new

    write_new(
        root / "experience-scope.json",
        json.dumps(selection_scope),
    )
    effective_budget = min(budget, len(allowed)) if planner is None else planner["total"]
    baseline = arm_root(root, "gemma_memory")
    for arm in frozen["arms"]:
        with closing(ReadOnlyAnalysisStore(arm_root(root, arm) / DB)) as store:
            if terminals(store) or store.all("llm_proposal") or store.all("autonomous_attempt"):
                raise ValueError("skill_requires_unused_arms")
    for _ in range(0 if skip_legacy_baseline else budget):
        with closing(ReadOnlyAnalysisStore(baseline / DB)) as store:
            before = len(terminals(store))
            if before >= budget:
                break
        baseline_result = run(root, pin, "gemma_memory")
        baseline_counts = baseline_result["arms"]["gemma_memory"]
        if baseline_counts["completed"] + baseline_counts["failed"] <= before:
            break
    config = ModelConfig(**frozen["request"]["model"])
    historical = historical_experiences(
        [
            h
            for h in frozen["memory"]
            if h["config"]["goal"]["horizon"] == frozen["request"]["horizon"]
            and h["config"]["stock_policy"] == frozen["request"]["stock_policy"]
        ]
    )
    pause_reason = None
    with lock(root, ".session.lock"):
        for _ in range(effective_budget):
            with closing(ReadOnlyAnalysisStore(target / DB)) as store:
                done = terminals(store)
                count = len(store.all("llm_proposal"))
            if len(done) >= effective_budget or count >= effective_budget:
                break
            agent = SkillAgent(target, lambda: OllamaProvider(config), adaptive=adaptive)

            def agent_factory(
                _root: Path, _plan: dict[str, Any], current: SkillAgent = agent
            ) -> SkillAgent:
                return current

            def project(view: dict[str, Any]) -> dict[str, Any]:
                history = [
                    h
                    for h in frozen["memory"] + prior_rows + view["history"]
                    if h["config"]["goal"]["horizon"] == frozen["request"]["horizon"]
                    and h["config"]["stock_policy"] == frozen["request"]["stock_policy"]
                ]
                unique = {h["experiment_id"]: h for h in history}
                return view | {
                    "history": list(unique.values())[-36:],
                    "candidates": [c for c in view["candidates"] if c["experiment_id"] in allowed],
                    "leader": None,
                    "skill_historical": historical,
                    "skill_persistent": persistent,
                    "skill_learned_policy": bool(previous),
                    "skill_planned_mode": None if planner is None else planner["schedule"][count],
                }

            result = run_worker(
                target,
                frozen["plan_id"],
                1,
                agent="managed",
                agent_factory=agent_factory,
                view_transform=project,
                strict_agent=True,
                allowed_experiments=allowed,
            ).to_dict()
            if result["pause_reason"]:
                pause_reason = result["pause_reason"]
                break
    value = report(root, pin)
    with closing(ReadOnlyAnalysisStore(target / DB)) as store:
        audits = [
            v
            for _, v in sorted(
                store.all("experimental_skill_proposal"), key=lambda pair: pair[1]["started_at"]
            )
        ]
    _, _, outcomes = evidence(target, frozen["plan_id"])
    hypotheses = [a["proposal"] for a in audits if a["state"] == "accepted"]
    skills = value["arms"]["gemma_blind"] | dict(
        memory_enabled=True,
        research_mode="adaptive_skills" if adaptive else "grounded_control",
        strategy_decisions=[p["strategy"] for p in hypotheses],
        historical_experiences=historical,
        persistent_experiences=persistent,
        persistent_sources=lineage,
        experience_scope_pin=digest(selection_scope),
        research_planner=planner,
        duplicate_configurations_excluded=len(frozen["candidates"]) - len(allowed),
        effective_training_budget=effective_budget,
        pause_reason=pause_reason,
        provider_inference_seconds=sum(a["metadata"]["inference_seconds"] for a in audits),
        tool_events=[a["events"] for a in audits],
        hypotheses=hypotheses,
        hypothesis_feedback=[hypothesis_feedback(p, outcomes) for p in hypotheses],
        prior_feedback=[a.get("prior_feedback", []) for a in audits],
    )
    baseline_row = value["arms"]["gemma_memory"]
    return {
        "baseline": baseline_row,
        "legacy_baseline_skipped": skip_legacy_baseline,
        "paired_budget_completed": baseline_row["completed"] == skills["completed"] == budget,
        "experimental_skills": skills,
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
    parser.add_argument("--grounded-control", action="store_true")
    parser.add_argument("--skip-legacy-baseline", action="store_true")
    parser.add_argument("--previous", action="append", nargs=2, type=Path, default=[])
    args = parser.parse_args()
    if not args.root.name.startswith("skill-experiment-"):
        parser.error("root must start with skill-experiment-")
    request = json.loads(args.request.read_text())
    if request["version"] != VERSION:
        parser.error("requires memory_guided_session_v1")
    pin = prepare(request, args.root, args.pins)
    result = compare(
        args.root,
        pin,
        adaptive=not args.grounded_control,
        skip_legacy_baseline=args.skip_legacy_baseline,
        previous=[(root, pin) for root, pin in args.previous],
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
