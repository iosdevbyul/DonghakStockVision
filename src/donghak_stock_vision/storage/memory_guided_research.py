"""Finite A/B/C sessions using the original runner, memory and Python workers."""

from __future__ import annotations

import hashlib
import json
import statistics
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import canonical, digest
from donghak_stock_vision.models import grounded_memory as grounded
from donghak_stock_vision.models import memory_reasoning as policy
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.models.research_agent import RuleBasedAgent
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import (
    DB,
    clock,
    context,
    private_root,
    terminals,
)
from donghak_stock_vision.storage.autonomous_runner import run as run_worker
from donghak_stock_vision.storage.gemma_research import prompt_view, proposal_schema
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.llm_providers import OllamaProvider
from donghak_stock_vision.storage.llm_research_agent import LLMResearchAgent
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.real_market_training import _quiescent
from donghak_stock_vision.storage.research_resources import lock

VERSION = "memory_guided_session_v1"
CAMPAIGN_VERSION = "memory_guided_session_v2"
GROUNDED_VERSION = "memory_guided_session_v3"
_REPLAY_OWNER = object()


def arms(version: str) -> tuple[str, ...]:
    return grounded.ARMS if version == GROUNDED_VERSION else policy.ARMS


def evidence(
    root: Path, pin: str, *, extended: bool = False, _verified: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    cache_key = digest(dict(root=str(root), pin=pin, extended=extended))
    before = file_sha(root / DB) if _verified is not None else None
    if _verified is not None and cache_key in _verified:
        cached = _verified[cache_key]
        private_root(root)
        require(
            cached.get("owner") is _REPLAY_OWNER and before == cached["database_sha256"],
            "memory_evidence_snapshot_changed",
        )
        plan, data = cached["plan"], cached["data"]
        require(
            digest(plan) == pin and digest(data) == plan["dataset_id"], "memory_evidence_retrieval"
        )
    else:
        plan, data = context(root, pin, code_check=False)
    require(plan["version"] == "autonomous_plan_v6", "memory_historical_owner")
    allowed = {c["experiment_id"]: c["config"] for c in plan["configurations"]}
    rows = []
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        for key, t in terminals(store).items():
            require(allowed.get(key) == t["config"], "memory_original_experiment_owner")
            item = dict(experiment_id=key, config=t["config"], state=t["state"])
            if t["state"] == "completed":
                r = store.get("autonomous_result", t["result_id"])
                require(r["dataset_id"] == plan["dataset_id"], "memory_result_dataset")
                fs = r["folds"]
                fold_defs = data["goals"][str(t["config"]["goal"]["horizon"])]["folds"]
                require(len(fs) == len(fold_defs), "memory_original_fold_owner")
                details = []
                for f, spec in zip(fs, fold_defs, strict=True):
                    e = f["evaluation"]
                    regimes = e.get("seen_robustness", {}).get("input_regimes", {})
                    usable = [
                        (name, row["metrics"]["balanced_accuracy"])
                        for name, row in regimes.items()
                        if row["metrics"].get("balanced_accuracy") is not None
                    ]
                    details.append(
                        dict(
                            period=spec["evaluation"],
                            training_end=spec["training"]["end"],
                            metrics={
                                k: e["metrics"][k]
                                for k in (
                                    "balanced_accuracy",
                                    "pr_auc_ap",
                                    "roc_auc",
                                    "brier_score",
                                )
                            },
                            baseline_ap=max(b["pr_auc_ap"] for b in e["baselines"].values()),
                            overfit_gap=f["train"]["balanced_accuracy"]
                            - e["metrics"]["balanced_accuracy"],
                            counts=e["counts"],
                            weakest_seen_regime=min(usable, key=lambda pair: pair[1], default=None),
                        )
                    )
                item["internal_folds"] = details[-3:]
                if extended:
                    item["calibration_delta"] = statistics.mean(
                        f["evaluation"]["metrics"]["brier_score"]
                        - f["evaluation"]["baselines"]["training_prior"]["brier_score"]
                        for f in fs
                    )
                item.update(
                    result_id=t["result_id"],
                    mean_ap=r["mean_ap"],
                    stability_ap=r["stability_ap"],
                    overfit_gap=r["largest_train_gap"],
                    worst_balanced_accuracy=r["worst_balanced_accuracy"],
                    baseline_ap_gain=statistics.mean(
                        f["evaluation"]["metrics"]["pr_auc_ap"]
                        - max(b["pr_auc_ap"] for b in f["evaluation"]["baselines"].values())
                        for f in fs
                    ),
                )
            else:
                item["failure_reason"] = t["reason"]
            rows.append(item)
    if _verified is not None:
        require(before == file_sha(root / DB), "memory_evidence_snapshot_changed")
        require(cache_key in _verified or len(_verified) < 13, "memory_source_bound")
        if _verified:
            shared = next(iter(_verified.values()))
            require(
                plan["dataset_id"] == shared["plan"]["dataset_id"] and data == shared["data"],
                "memory_cross_dataset",
            )
            data = shared["data"]
        _verified[cache_key] = dict(
            plan=plan, data=data, owner=_REPLAY_OWNER, database_sha256=before
        )
    return plan, data, rows


def prepare(
    request: dict[str, Any], root: Path, pins: Path, *, _verified: dict[str, Any] | None = None
) -> str:
    fields(request, "version source memory horizon stock_policy budget model")
    require(
        request["version"] in (VERSION, CAMPAIGN_VERSION, GROUNDED_VERSION)
        and type(request["budget"]) is int
        and 1 <= request["budget"] <= (4 if request["version"] == VERSION else 8)
        and type(request["horizon"]) is int
        and request["horizon"] in (1, 5, 20)
        and request["stock_policy"] in ("pooled", "heldout"),
        "memory_session_scope",
    )
    require(
        all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "memory_new_outputs",
    )
    fields(request["source"], "root plan_pin_file")
    if request["version"] == GROUNDED_VERSION:
        _verified = {} if _verified is None else _verified
    else:
        require(_verified is None, "memory_legacy_cache_not_supported")
    source = Path(request["source"]["root"])
    source_pin = read_pin(Path(request["source"]["plan_pin_file"]))
    plan, data, _ = evidence(
        source, source_pin, extended=request["version"] == GROUNDED_VERSION, _verified=_verified
    )
    require(
        root.parent == source.parent and not pins.is_relative_to(source),
        "memory_shared_resource_parent",
    )
    original = document(str(source / "request.json"))
    require(digest(original) == plan["request_pin"], "memory_original_request")
    _quiescent(Path(original["research"]["database"]), original["research"]["pin"])
    require(
        1 <= len(request["memory"]) <= (6 if request["version"] == VERSION else 12),
        "memory_source_bound",
    )
    memory, references = [], []
    seen = set()
    for ref in request["memory"]:
        fields(ref, "root plan_pin_file")
        p = Path(ref["root"])
        pin = read_pin(Path(ref["plan_pin_file"]))
        require(p not in seen, "memory_duplicate_owner")
        seen.add(p)
        prior, prior_data, rows = evidence(
            p, pin, extended=request["version"] != VERSION, _verified=_verified
        )
        # Only same-cohort internal folds can inform the frozen population.
        require(
            prior["dataset_id"] == plan["dataset_id"] and prior_data == data, "memory_cross_dataset"
        )
        if request["version"] == GROUNDED_VERSION:
            from donghak_stock_vision.storage.grounded_evidence import catalog

            _, rows = catalog(
                p, pin, request["horizon"], request["stock_policy"], _verified=_verified
            )
        memory.extend(rows)
        references.append(dict(root=str(p), plan=pin, database_sha256=file_sha(p / DB)))
    unique: dict[str, dict[str, Any]] = {}
    for row in memory:
        require(
            row["experiment_id"] not in unique
            or (
                {
                    k: v
                    for k, v in unique[row["experiment_id"]].items()
                    if k
                    not in (
                        "source_plan",
                        "terminal_hash",
                        "created_at",
                        "content_hash",
                        "parent_experiment_id",
                        "hypothesis_parent_experiment_id",
                        "execution_started_at",
                        "execution_finished_at",
                    )
                }
                == {
                    k: v
                    for k, v in row.items()
                    if k
                    not in (
                        "source_plan",
                        "terminal_hash",
                        "created_at",
                        "content_hash",
                        "parent_experiment_id",
                        "hypothesis_parent_experiment_id",
                        "execution_started_at",
                        "execution_finished_at",
                    )
                }
            )
            if request["version"] == GROUNDED_VERSION
            else row["experiment_id"] not in unique or unique[row["experiment_id"]] == row,
            "memory_conflicting_original",
        )
        unique.setdefault(row["experiment_id"], row)
    memory = sorted(unique.values(), key=lambda r: r["experiment_id"])
    config = ModelConfig(**request["model"])
    require(
        config.provider_id == "ollama" and config.model_id == "gemma4:e2b",
        "memory_local_gemma_required",
    )
    config.require_execution()
    require(
        config.timeout <= 240 and config.max_memory_mb <= 7168, "memory_existing_resource_limits"
    )
    code = provenance()
    configs = []
    for c in plan["configurations"]:
        cfg = c["config"]
        identity = cfg | dict(
            training_sample_pin=data["goals"][str(cfg["goal"]["horizon"])]["sample_pin"],
            fold_pin=digest(data["goals"][str(cfg["goal"]["horizon"])]["folds"]),
            implementation_hash=code["implementation_hash"],
            versions=code["versions"],
        )
        configs.append(dict(experiment_id=digest(identity), config=cfg))
    excluded = {digest(r["config"]) for r in memory}
    candidates = [
        c
        for c in configs
        if c["config"]["goal"]["horizon"] == request["horizon"]
        and c["config"]["stock_policy"] == request["stock_policy"]
        and digest(c["config"]) not in excluded
    ]
    require(
        len(candidates) >= (request["budget"] if request["version"] == VERSION else 1),
        "memory_unseen_candidates_insufficient",
    )
    new = {k: v for k, v in plan.items() if k != "policy_parent"} | dict(
        code=code, configurations=configs, selection_policy="predeclared"
    )
    frozen = dict(
        version=request["version"],
        registered_at=clock(),
        request=request,
        source_plan=source_pin,
        plan_id=digest(new),
        dataset_id=plan["dataset_id"],
        source_pin=plan["source_pin"],
        memory=memory,
        memory_sources=references,
        candidates=candidates,
        arms=list(arms(request["version"])),
        prompt_version=policy.PROMPT,
        comparison=dict(
            independent_generalization="NOT_ESTABLISHED",
            frozen_thresholds=dict(
                balanced_accuracy=0.5,
                baseline_ap_gain=0.0,
                maximum_overfit_gap=0.15,
                minimum_calendar_anchors=20,
                minimum_per_class=10,
            ),
            adaptive_internal_evaluation_only=True,
            random_seed=42,
            proposal_budget_per_arm=2 * request["budget"],
            training_budget_per_arm=request["budget"],
        ),
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    if request["version"] == GROUNDED_VERSION:
        from donghak_stock_vision.storage.grounded_evidence import rejection_catalog

        frozen["grounded_policy"] = grounded.POLICY
        rejections = [r for ref in references for r in rejection_catalog(Path(ref["root"]))]
        frozen["rejected_proposal_catalog"] = sorted(
            {r["proposal_artifact_id"]: r for r in rejections}.values(),
            key=lambda r: r["proposal_artifact_id"],
        )
    if request["version"] != VERSION:
        frozen["effective_budget"] = min(request["budget"], len(candidates))
        frozen["comparison"]["training_budget_per_arm"] = frozen["effective_budget"]
    require(
        all(not arm_root(root, a).exists() for a in arms(request["version"])), "memory_arm_exists"
    )
    require(
        not pins.is_relative_to(Path(original["research"]["database"]).parent)
        and all(not pins.is_relative_to(Path(r["root"])) for r in references),
        "memory_protected_pin_output",
    )
    root.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    with closing(AnalysisStore(root / DB)):
        pass
    (root / DB).chmod(0o600)
    with closing(ReadOnlyAnalysisStore(source / DB)) as old:
        records = [
            ("autonomous_snapshot", old.get("autonomous_snapshot", plan["snapshot_id"])),
            ("autonomous_dataset", data),
            ("autonomous_plan", new),
        ]
        records.extend(
            ("autonomous_source_verification", v)
            for _, v in old.all("autonomous_source_verification")
        )
    for arm in arms(request["version"]):
        target = root.parent / (root.name + "-" + arm)
        require(not target.exists(), "memory_arm_exists")
        target.mkdir(mode=0o700)
        with closing(AnalysisStore(target / DB)) as store:
            store.put_many(records)
        (target / DB).chmod(0o600)
        write_new(target / "request.json", json.dumps(original))
    write_new(root / "session.json", json.dumps(frozen))
    write_new(pins / "session.pin", digest(frozen))
    write_new(pins / "plan.pin", digest(new))
    return digest(frozen)


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(root: Path, pin: str) -> dict[str, Any]:
    frozen = document(str(root / "session.json"), maximum=8 * 1024**2)
    require(
        digest(frozen) == pin
        and frozen["version"] in (VERSION, CAMPAIGN_VERSION, GROUNDED_VERSION),
        "memory_session_pin",
    )
    require(
        frozen["arms"] == list(arms(frozen["version"]))
        and frozen["prompt_version"] == policy.PROMPT,
        "memory_session_policy",
    )
    if frozen["version"] == GROUNDED_VERSION:
        require(frozen["grounded_policy"] == grounded.POLICY, "memory_grounded_policy_changed")
    for ref in frozen["memory_sources"]:
        require(
            file_sha(Path(ref["root"]) / DB) == ref["database_sha256"], "memory_original_changed"
        )
    if frozen["version"] == GROUNDED_VERSION:
        # One full replay per load, not five. Exact canonical bytes bind every sibling
        # to the same verified immutable objects. This cache exists only in this call.
        plan, data = context(arm_root(root, "rule"), frozen["plan_id"])
        owners = (
            ("autonomous_plan", frozen["plan_id"], canonical(plan)),
            ("autonomous_dataset", plan["dataset_id"], canonical(data)),
        )
        for arm in frozen["arms"]:
            target = arm_root(root, arm)
            private_root(target)
            with closing(ReadOnlyAnalysisStore(target / DB)) as store:
                for kind, key, payload in owners:
                    row = store.connection.execute(
                        "SELECT payload FROM analysis_artifacts WHERE kind=? AND id=?", (kind, key)
                    ).fetchone()
                    require(row is not None and row[0] == payload, "memory_sibling_content_changed")
    else:
        for arm in frozen["arms"]:
            context(arm_root(root, arm), frozen["plan_id"])
    return frozen


def arm_root(root: Path, arm: str) -> Path:
    require(arm in (*policy.ARMS, *grounded.ARMS), "memory_arm")
    return root.parent / (root.name + "-" + arm)


class MemoryAgent(LLMResearchAgent):
    def proposal_input(self, view: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
        summary = prompt_view(view) | dict(cohort=view["cohort"], horizon=view["goal"]["horizon"])
        summary["internal_period_evidence"] = [
            dict(experimentId=h["experiment_id"], folds=h.get("internal_folds", [])[-2:])
            for h in view["history"]
        ]
        if view.get("campaign_reasoning"):
            summary["reasoningVersion"] = "campaign_counterfactual_v1"
            summary["previous_expectation"] = view.get("own_feedback", [])
            summary["diagnosis"] = policy.failure_patterns(view)
            # Cross-arm cache entries never enter this view. Only permitted memory/own results do.
            summary["counterfactual"] = (
                [
                    dict(
                        candidateId=c["experiment_id"],
                        parentId=view["history"][-1]["experiment_id"],
                        changed=[
                            k
                            for k in ("mask", "candidate")
                            if c["config"][k] != view["history"][-1]["config"][k]
                        ],
                    )
                    for c in view["candidates"][:3]
                ]
                if view["history"]
                else []
            )
        prompt = (
            "Propose one falsifiable internal-development hypothesis. JSON only. Copy exact "
            "candidate id, family, featureSet, value and seed 42. Fill fixed schema enums exactly. "
            "Use at most eight words per text. Cite exactly one supplied experiment ID and metric; "
            "empty evidence when history is empty. Correlation is not causation. Never code/tools. "
            "With counterfactual evidence, set parentExperimentId to your cited experiment. "
            "No independent or trading approval.\n" + json.dumps(summary, separators=(",", ":"))
        )
        return summary, prompt, policy.schema(view, proposal_schema(view))

    def validate(self, proposal: dict[str, Any], view: dict[str, Any]) -> str:
        expected = policy.schema(view, proposal_schema(view))["required"]
        fields(proposal, " ".join(expected))
        proposal.update(policy.FIXED, horizon=view["goal"]["horizon"], cohort=view["cohort"])
        return policy.validate(proposal, view)


class RuleAgent:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.audit: dict[str, Any] = {}
        self.unloaded = True

    def propose(self, view: dict[str, Any]) -> str:
        chosen = RuleBasedAgent().propose(view)
        c = next(c["config"] for c in view["candidates"] if c["experiment_id"] == chosen)
        # Same structured contract with a deterministic, explicitly rule-authored hypothesis.
        value = dict(
            proposalVersion="gemma_research_proposal_v1",
            experimentId=chosen,
            hypothesis="Frozen host-ranked candidate",
            modelFamily=c["candidate"]["family"],
            featureSet=c["mask"],
            hyperparameters=dict(value=c["candidate"]["value"], seed=42),
            researchGoal=c["goal"]["label_version"],
            parentExperimentId=None,
            expectedImprovement="Internal baseline improvement hypothesis",
            rationale="Existing frozen rule ranking",
            horizon=c["goal"]["horizon"],
            cohort=view["cohort"],
            problem="Generalization uncertainty",
            failureCondition="No internal baseline gain",
            evidence=[],
            **policy.FIXED,
        )
        completed = [h for h in view["history"] if "mean_ap" in h]
        if completed:
            value["evidence"] = [dict(experimentId=completed[0]["experiment_id"], metric="mean_ap")]
        policy.validate(value, view)
        request_id = digest(dict(at=clock(), proposal=value, provider_id="rule"))
        self.audit = dict(
            proposal=value,
            request_id=request_id,
            state="accepted",
            unloaded=True,
            provider_id="rule",
            wall_seconds=0.0,
            metadata={},
        )
        with closing(AnalysisStore(self.root / DB)) as store:
            store.put("llm_proposal", self.audit)
        return chosen


def run(root: Path, pin: str, arm: str) -> dict[str, Any]:
    frozen = load(root, pin)
    require(arm in frozen["arms"], "memory_arm")
    target = arm_root(root, arm)
    with lock(root, ".session.lock"):
        feedback(root, frozen, arm)
        with closing(ReadOnlyAnalysisStore(target / DB)) as store:
            proposal_count = max(
                len(store.all("llm_proposal")), len(store.all("autonomous_selection"))
            )
            completed = terminals(store)
        budget = frozen.get("effective_budget", frozen["request"]["budget"])
        if len(completed) >= budget or proposal_count >= 2 * budget:
            return report(root, pin)
        plan, data = context(target, frozen["plan_id"])
        _, _, own_history = evidence(
            target, frozen["plan_id"], extended=frozen["version"] != VERSION
        )
        scope = None
        if frozen["version"] == GROUNDED_VERSION:
            from donghak_stock_vision.storage.grounded_evidence import catalog

            scope, own_history = catalog(
                target,
                frozen["plan_id"],
                frozen["request"]["horizon"],
                frozen["request"]["stock_policy"],
            )
        allowed = {c["experiment_id"] for c in frozen["candidates"]}

        def project(view: dict[str, Any]) -> dict[str, Any]:
            histories = frozen["memory"] + own_history if arm != "gemma_blind" else []
            histories = [
                h
                for h in histories
                if h["config"]["goal"]["horizon"] == frozen["request"]["horizon"]
                and h["config"]["stock_policy"] == frozen["request"]["stock_policy"]
            ]
            histories = list({h["experiment_id"]: h for h in histories}.values())
            if arm not in ("verified", "compact", "value_ranked"):
                histories = histories[-2:]
            elif len(histories) > 36:
                representatives, _ = grounded.select(histories, view["candidates"])
                histories = (
                    representatives
                    + [h for h in histories if h not in representatives][
                        : 36 - len(representatives)
                    ]
                )
            own_feedback = []
            if frozen["version"] != VERSION and arm in ("gemma_memory", "phase_c"):
                with closing(ReadOnlyAnalysisStore(root / DB)) as feedback_store:
                    prior = [
                        f
                        for _, f in feedback_store.all("memory_hypothesis_feedback")
                        if f["arm"] == arm
                    ]
                for f in prior[-1:]:
                    proposal = f["proposal"] or {}
                    own_feedback.append(
                        dict(
                            experimentId=f["outcome"]["experiment_id"],
                            hypothesis=proposal.get("hypothesis"),
                            expected=proposal.get("expectedImprovement"),
                            observedAPGain=f["outcome"].get("baseline_ap_gain"),
                            observedWorstBA=f["outcome"].get("worst_balanced_accuracy"),
                            falsification=proposal.get("failureCondition"),
                            causal=False,
                        )
                    )
            return view | dict(
                candidates=[c for c in view["candidates"] if c["experiment_id"] in allowed],
                history=histories,
                leader=None,
                budget=budget - len(completed),
                cohort=data["generalization"]["cohort"],
                **(dict(evidence_scope=scope) if scope is not None else {}),
                **(
                    dict(
                        counterfactual_required=arm
                        in ("gemma_memory", "phase_c", "verified", "compact", "value_ranked"),
                        campaign_reasoning=True,
                        own_feedback=own_feedback,
                    )
                    if frozen["version"] != VERSION
                    else {}
                ),
            )

        agent: LLMResearchAgent | RuleAgent
        agent = (
            RuleAgent(target)
            if arm == "rule"
            else MemoryAgent(target, OllamaProvider(ModelConfig(**frozen["request"]["model"])))
        )
        if arm in ("verified", "compact", "value_ranked"):
            from donghak_stock_vision.storage.grounded_evidence import GroundedAgent

            config = frozen["request"]["model"] | dict(
                max_output_tokens=grounded.POLICY["output_tokens"]
            )
            agent = GroundedAgent(target, OllamaProvider(ModelConfig(**config)), arm)
        peers = tuple(
            (
                (arm_root(root, a), frozen["plan_id"])
                for a in frozen["arms"]
                if a != arm and terminals_count(arm_root(root, a)) > 0
            ),
        )
        result = run_worker(
            target,
            frozen["plan_id"],
            1,
            agent="managed",
            reuse_peers=peers,
            agent_factory=lambda r, p: agent,
            view_transform=project,
            strict_agent=True,
            allowed_experiments=frozenset(allowed),
        )
        with closing(AnalysisStore(root / DB)) as audit_store:
            audit_store.put(
                "memory_session_run", dict(arm=arm, at=clock(), result=result.to_dict())
            )
        feedback(root, frozen, arm)
        return report(root, pin)


def terminals_count(root: Path) -> int:
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        return len(terminals(store))


def feedback(root: Path, frozen: dict[str, Any], arm: str) -> None:
    target = arm_root(root, arm)
    _, _, rows = evidence(target, frozen["plan_id"])
    results = {r["experiment_id"]: r for r in rows}
    with (
        closing(ReadOnlyAnalysisStore(target / DB)) as source,
        closing(AnalysisStore(root / DB)) as out,
    ):
        for selection_id, s in source.all("autonomous_selection"):
            if s.get("state") not in ("accepted", "rule") or s.get("experiment_id") not in results:
                continue
            proposal = None
            if s.get("request_id"):
                matches = [
                    p for _, p in source.all("llm_proposal") if p["request_id"] == s["request_id"]
                ]
                require(
                    len(matches) == 1 and matches[0]["state"] == "accepted",
                    "memory_feedback_proposal",
                )
                proposal = matches[0]["proposal"]
            outcome = results[s["experiment_id"]]
            if outcome["state"] == "completed":
                original = source.get("autonomous_result", outcome["result_id"])
                outcome = outcome | dict(
                    internal_folds=[
                        dict(
                            training=f["train"],
                            evaluation=f["evaluation"]["metrics"],
                            baselines=f["evaluation"]["baselines"],
                            counts=f["evaluation"]["counts"],
                            seen_robustness=f["evaluation"].get("seen_robustness"),
                            nonoverlapping_calendar_dates=f["evaluation"].get(
                                "nonoverlapping_calendar_dates", []
                            ),
                            nonoverlapping_assessment=f["evaluation"].get(
                                "nonoverlapping_generalization"
                            ),
                        )
                        for f in original["folds"]
                    ]
                )
            out.put(
                "memory_hypothesis_feedback",
                dict(
                    session=digest(frozen),
                    arm=arm,
                    selection_id=selection_id,
                    hypothesis_id=policy.hypothesis_id(
                        proposal or dict(experimentId=s["experiment_id"]), digest(frozen), arm
                    ),
                    proposal=proposal,
                    outcome=outcome,
                    baseline_improvement_observed=outcome.get("baseline_ap_gain", -1) > 0,
                    preregistered_internal_pass=(
                        outcome.get("baseline_ap_gain", -1) > 0
                        and outcome.get("worst_balanced_accuracy", 0) >= 0.5
                        and outcome.get("overfit_gap", 1) <= 0.15
                    ),
                    causal_explanation_established=False,
                    independent_generalization="NOT_ESTABLISHED",
                    performance_approved=False,
                    pit_qualified=False,
                ),
            )


def report(root: Path, pin: str) -> dict[str, Any]:
    frozen = load(root, pin)
    arms = {}
    for arm in frozen["arms"]:
        target = arm_root(root, arm)
        with closing(ReadOnlyAnalysisStore(target / DB)) as store:
            done = terminals(store)
            selections = store.all("autonomous_selection")
            proposals = [v for _, v in store.all("llm_proposal")]
            costs = [v for _, v in store.all("autonomous_resource_audit")]
            results = [
                store.get("autonomous_result", t["result_id"])
                for t in done.values()
                if t["state"] == "completed"
            ]
        arms[arm] = dict(
            memory_enabled=arm != "gemma_blind",
            generated=len(proposals),
            attempted=max(len(proposals), len(selections)),
            accepted=sum(v.get("state") == "accepted" for _, v in selections),
            rejected=sum(v.get("state") in ("rejected", "blocked_unload") for _, v in selections),
            completed=len(results),
            failed=sum(t["state"] == "failed" for t in done.values()),
            actual_python_trainings=len(costs)
            if frozen["version"] == VERSION
            else sum(t["state"] == "completed" and "cache_source" not in t for t in done.values()),
            reused=sum("cache_source" in t for t in done.values()),
            gemma_calls=sum(v.get("metadata", {}).get("inference_calls", 0) for v in proposals),
            inference_seconds=sum(v["wall_seconds"] for v in proposals),
            python_seconds=sum(c["wall_seconds"] for c in costs),
            peak_python_rss_kb=max((c["maximum_rss_kb"] for c in costs), default=0),
            peak_ollama_rss_kb=max(
                ((p.get("metadata", {}).get("maximum_rss_kb") or 0) for p in proposals), default=0
            ),
            experiment_ids=list(done),
            schema_passed=sum("proposal" in p for p in proposals),
            failure_reasons=[
                p.get("reason", "UNKNOWN") for p in proposals if p["state"] != "accepted"
            ],
            training_budget=frozen.get("effective_budget", frozen["request"]["budget"]),
            unreached_budget=max(
                0, frozen.get("effective_budget", frozen["request"]["budget"]) - len(done)
            ),
            configuration_diversity=len({digest(t["config"]) for t in done.values()}),
            evidence_references=sum(
                len(p.get("proposal", {}).get("evidence", [])) for p in proposals
            ),
            internal_mean_ap=[r["mean_ap"] for r in results],
            internal_worst_ba=[r["worst_balanced_accuracy"] for r in results],
            observations="Internal adaptive development only; no fresh independent evaluation",
        )
        if frozen["version"] == GROUNDED_VERSION:
            from donghak_stock_vision.storage.grounded_evidence import diagnostics

            arms[arm]["grounded"] = diagnostics(target)
        if frozen["version"] != VERSION:
            with closing(ReadOnlyAnalysisStore(target / DB)) as attempts_store:
                arms[arm]["python_worker_attempts"] = len(attempts_store.all("autonomous_attempt"))
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        feedbacks = [v for _, v in store.all("memory_hypothesis_feedback")]
    result = dict(
        version="memory_guided_report_v1"
        if frozen["version"] == VERSION
        else "memory_guided_report_v3"
        if frozen["version"] == GROUNDED_VERSION
        else "memory_guided_report_v2",
        session_id=pin,
        arms=arms,
        feedback=feedbacks,
        model=dict(
            providerId="ollama",
            modelId="gemma4:e2b",
            revision=frozen["request"]["model"]["revision"],
        ),
        independent_generalization="NOT_ESTABLISHED",
        performance_approved=False,
        pit_qualified=False,
        execution_authorized=False,
    )

    if frozen["version"] == GROUNDED_VERSION:
        counts = {
            name: sum(grounded.category(r) == name for r in frozen["memory"])
            for name in ("success", "failure", "neutral")
        }
        rejections = frozen["rejected_proposal_catalog"]
        result["evidenceCatalog"] = dict(
            originalExperiments=len(frozen["memory"]),
            categories=counts,
            rejectedProposals=len(rejections),
            rejectionsWithoutExperiment=len(rejections),
            failureClasses={
                name: sum(r["failure_class"] == name for r in rejections)
                for name in sorted(set(grounded.FAILURES.values()) | {"Resource Unavailable"})
            },
        )
    return result


def publication(value: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted finite report, never raw prompts, PINs, paths or model binaries."""
    import re

    def text(s: str) -> str:
        return s if re.fullmatch(r"[A-Za-z0-9 .,_()<>+=;-]{1,160}", s) else "REDACTED_UNSAFE_TEXT"

    hypotheses = []
    for f in value["feedback"]:
        p = f["proposal"]
        if p is None:
            continue
        hypotheses.append(
            dict(
                hypothesisId=f["hypothesis_id"],
                experimentId=f["outcome"]["experiment_id"],
                agent=f["arm"],
                hypothesis=text(p["hypothesis"]),
                problem=text(p["problem"]),
                failureCondition=text(p["failureCondition"]),
                parentExperimentId=p["parentExperimentId"],
                evidence=p["evidence"],
                internalAP=f["outcome"].get("mean_ap"),
                baselineImprovementObserved=f["baseline_improvement_observed"],
                independentlyQualified=False,
            )
        )
    result = dict(
        version="memory_guided_publication_v1"
        if value["version"] == "memory_guided_report_v1"
        else "memory_guided_publication_v3"
        if value["version"] == "memory_guided_report_v3"
        else "memory_guided_publication_v2",
        sessionId=value["session_id"],
        arms=value["arms"],
        model=value["model"],
        hypotheses=hypotheses,
        independentGeneralization="NOT_ESTABLISHED",
        performanceApproved=False,
        pitQualified=False,
        executionAuthorized=False,
    )
    if value["version"] == "memory_guided_report_v3":
        result["evidenceCatalog"] = value["evidenceCatalog"]
    return validate_publication(result)


def validate_publication(value: dict[str, Any]) -> dict[str, Any]:
    """Strict export boundary; approval of bytes is still required by the Lab index."""
    import math
    import re

    fields(
        value,
        "version sessionId arms model hypotheses independentGeneralization "
        "performanceApproved pitQualified executionAuthorized"
        + (" evidenceCatalog" if value.get("version") == "memory_guided_publication_v3" else ""),
    )
    require(
        value["version"]
        in (
            "memory_guided_publication_v1",
            "memory_guided_publication_v2",
            "memory_guided_publication_v3",
        )
        and value["independentGeneralization"] == "NOT_ESTABLISHED"
        and all(
            value[k] is False
            for k in ("performanceApproved", "pitQualified", "executionAuthorized")
        ),
        "memory_publication_approval",
    )

    if value["version"] == "memory_guided_publication_v3":
        c = fields(
            value["evidenceCatalog"],
            "originalExperiments categories rejectedProposals "
            "rejectionsWithoutExperiment failureClasses",
        )
        fields(c["categories"], "success failure neutral")
        require(
            set(c["failureClasses"]) == set(grounded.FAILURES.values()) | {"Resource Unavailable"},
            "memory_publication_catalog",
        )
        values = [
            c["originalExperiments"],
            c["rejectedProposals"],
            c["rejectionsWithoutExperiment"],
            *c["categories"].values(),
            *c["failureClasses"].values(),
        ]
        require(
            all(type(n) is int and 0 <= n <= 5000 for n in values)
            and c["originalExperiments"] == sum(c["categories"].values())
            and c["rejectedProposals"]
            == c["rejectionsWithoutExperiment"]
            == sum(c["failureClasses"].values()),
            "memory_publication_catalog",
        )

    def hex_id(s: Any) -> bool:
        return type(s) is str and re.fullmatch(r"[a-f0-9]{64}", s) is not None

    require(hex_id(value["sessionId"]), "memory_publication_id")
    fields(value["model"], "providerId modelId revision")
    require(
        value["model"]["providerId"] == "ollama"
        and value["model"]["modelId"] == "gemma4:e2b"
        and hex_id(value["model"]["revision"]),
        "memory_publication_model",
    )
    expected_arms = (
        grounded.ARMS if value["version"] == "memory_guided_publication_v3" else policy.ARMS
    )
    fields(value["arms"], " ".join(expected_arms))
    counts = (
        "generated attempted accepted rejected completed failed actual_python_trainings "
        "reused gemma_calls "
        "peak_python_rss_kb peak_ollama_rss_kb schema_passed training_budget unreached_budget "
        "configuration_diversity evidence_references"
    ).split()
    for arm, row in value["arms"].items():
        if value["version"] == "memory_guided_publication_v3":
            from donghak_stock_vision.storage.grounded_evidence import validate_diagnostics

            validate_diagnostics(row["grounded"])
        if value["version"] != "memory_guided_publication_v1":
            require(
                type(row["python_worker_attempts"]) is int
                and 0 <= row["python_worker_attempts"] <= 32,
                "memory_publication_worker_count",
            )
        fields(
            row,
            " ".join(
                counts
                + (
                    ["python_worker_attempts"]
                    if value["version"] != "memory_guided_publication_v1"
                    else []
                )
                + [
                    *(["grounded"] if value["version"] == "memory_guided_publication_v3" else []),
                    "memory_enabled",
                    "inference_seconds",
                    "python_seconds",
                    "experiment_ids",
                    "internal_mean_ap",
                    "internal_worst_ba",
                    "failure_reasons",
                    "observations",
                ]
            ),
        )
        require(
            row["memory_enabled"] is (arm != "gemma_blind")
            and all(type(row[k]) is int and 0 <= row[k] <= 10**8 for k in counts),
            "memory_publication_counts",
        )
        require(
            row["training_budget"]
            <= (4 if value["version"] == "memory_guided_publication_v1" else 8)
            and row["generated"]
            <= (8 if value["version"] == "memory_guided_publication_v1" else 16)
            and row["completed"] + row["failed"] <= row["training_budget"],
            "memory_publication_budget",
        )
        require(
            len(row["experiment_ids"]) == row["completed"] + row["failed"]
            and all(hex_id(i) for i in row["experiment_ids"]),
            "memory_publication_experiments",
        )
        require(
            row["observations"]
            == "Internal adaptive development only; no fresh independent evaluation",
            "memory_publication_interpretation",
        )
        require(
            all(
                type(r) is str and re.fullmatch(r"(?:memory|gemma|llm)_[a-z0-9_]{1,100}", r)
                for r in row["failure_reasons"]
            ),
            "memory_publication_failure",
        )
        numbers = [
            row["inference_seconds"],
            row["python_seconds"],
            *row["internal_mean_ap"],
            *row["internal_worst_ba"],
        ]
        require(
            all(type(n) in (int, float) and math.isfinite(n) and n >= 0 for n in numbers),
            "memory_publication_numbers",
        )
    require(
        type(value["hypotheses"]) is list
        and len(value["hypotheses"])
        <= (
            12
            if value["version"] == "memory_guided_publication_v1"
            else 40
            if value["version"] == "memory_guided_publication_v3"
            else 24
        ),
        "memory_publication_bound",
    )
    for h in value["hypotheses"]:
        fields(
            h,
            "hypothesisId experimentId agent hypothesis problem failureCondition "
            "parentExperimentId evidence internalAP baselineImprovementObserved "
            "independentlyQualified",
        )
        require(
            hex_id(h["hypothesisId"])
            and h["agent"] in expected_arms
            and h["experimentId"] in value["arms"][h["agent"]]["experiment_ids"]
            and h["independentlyQualified"] is False
            and type(h["baselineImprovementObserved"]) is bool,
            "memory_publication_hypothesis",
        )
        require(
            all(
                type(h[k]) is str and re.fullmatch(r"[A-Za-z0-9 .,_()<>+=;-]{1,160}", h[k])
                for k in ("hypothesis", "problem", "failureCondition")
            ),
            "memory_publication_text",
        )
        require(
            h["parentExperimentId"] is None or hex_id(h["parentExperimentId"]),
            "memory_publication_parent",
        )
        require(
            type(h["evidence"]) is list and len(h["evidence"]) <= 3, "memory_publication_evidence"
        )
        for e in h["evidence"]:
            fields(e, "experimentId metric")
            require(
                hex_id(e["experimentId"]) and e["metric"] in policy.METRICS,
                "memory_publication_evidence",
            )
        require(
            h["internalAP"] is None
            or type(h["internalAP"]) in (int, float)
            and math.isfinite(h["internalAP"])
            and 0 <= h["internalAP"] <= 1,
            "memory_publication_ap",
        )
    return value
