"""Bounded R4 comparison using the R3 trial runner and Phase D proposal owner."""

import hashlib
import json
import os
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import autonomous_strategy as policy
from donghak_stock_vision.models import predictive_research as r3_policy
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.storage import predictive_research as r3
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, LIMITS, clock, private_root
from donghak_stock_vision.storage.failure_feedback_source import outcome_panels, source_owner
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.research_campaign import validate_publication
from donghak_stock_vision.storage.research_resources import lock, probe, resource_reason
from donghak_stock_vision.storage.research_studies import _file_sha256

R3_ID = "45422c14bfc4bb7e263820d83ac3a21f4c9d3c73838a841f040569ba9b7d251f"
MAX_CALLS = 1
CANDIDATES = (
    ("v2_c0.1_lbfgs_balanced", "v2_multi"),
    ("v2_c10.0_lbfgs_balanced", "v2_daily"),
    ("v2_forest6_32", "v2_daily"),
    ("v2_forest3_96", "v2_daily_weekly"),
    ("v2_hist7_0.1", "v2_legacy"),
    ("v2_hist15_0.03", "v2_daily_weekly"),
)


def _prior(root: Path, plan_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    private_root(root)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        plan = store.get("predictive_research_plan", plan_id)
        # Validate the identity without loading the prior fixed evaluation into selection memory.
        result = store.connection.execute(
            "SELECT json_extract(payload,'$.plan_id'),json_extract(payload,'$.shortlist_id') "
            "FROM analysis_artifacts WHERE kind='predictive_research_result' AND id=?",
            (R3_ID,),
        ).fetchone()
        require(result is not None, "strategy_prior_result")
        owner, shortlist_id = result
        shortlist = store.get("predictive_research_shortlist", shortlist_id)
        trials = [v for _, v in store.all("predictive_research_trial")]
    require(
        owner == plan_id == shortlist["plan_id"]
        and len(trials) == 34
        and plan["feedback_id"] == r3.FEEDBACK_ID
        and not plan["performance_approved"],
        "strategy_prior_binding",
    )
    return plan, trials, shortlist


def register(request: dict[str, Any], root: Path, pins: Path) -> str:
    fields(
        {key: value for key, value in request.items() if key != "recovery"},
        "version r3_root r3_plan_pin_file phase_d_publication phase_d_hash_file local_model_digest",
    )
    require(request["version"] == "autonomous_strategy_request_v1", "strategy_request_version")
    previous = Path(request["r3_root"])
    require(
        root.parent == previous.parent
        and all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "strategy_output_scope",
    )
    prior_pin = read_pin(Path(request["r3_plan_pin_file"]))
    before = _file_sha256(previous / DB)
    source_plan, trials, shortlist = _prior(previous, prior_pin)
    sources = [source_owner(s) for s in source_plan["sources"]]
    feedback = r3._feedback(source_plan["request"])
    phase_d = Path(request["phase_d_publication"])
    require(
        phase_d.is_absolute()
        and phase_d.resolve() == phase_d
        and phase_d.stat().st_size <= 8 * 1024**2
        and _file_sha256(phase_d) == read_pin(Path(request["phase_d_hash_file"])),
        "strategy_phase_d_binding",
    )
    publication = validate_publication(json.loads(phase_d.read_text()))
    require(
        publication["version"] == "research_campaign_publication_v3"
        and publication["progress"]["remainingSlots"] == 108,
        "strategy_phase_d_scope",
    )
    provider = ModelConfig(
        key="r4_local_compact",
        provider_id="ollama",
        model_id="gemma4:e2b",
        revision=sha(request["local_model_digest"]),
        display_name="Gemma 4",
        endpoint="http://127.0.0.1:11434",
        timeout=55,
        context_window=4096,
        max_output_tokens=128,
        thinking_supported=True,
        structured_output=True,
        json_schema=True,
        temperature=0.0,
    )
    used = {digest(t["config"]) for t in trials}
    pools = {
        str(h): [
            dict(config=c, experiment_id=digest(c))
            for name, mask in CANDIDATES
            if digest(c := r3._candidate_configuration(h, 16, name, mask)) not in used
        ]
        for h in policy.HORIZONS
    }
    require(
        all(len(choices) >= 4 for choices in pools.values())
        and sources == source_plan["sources"]
        and before == _file_sha256(previous / DB),
        "strategy_source_or_pool",
    )
    from dataclasses import asdict

    development_tickers = sorted(source_plan["development_tickers"])
    require(len(development_tickers) == 8, "strategy_development_group_size")
    plan = dict(
        version="autonomous_strategy_plan_v1",
        registered_at=clock(),
        request=request,
        code=provenance(),
        source_plan_id=prior_pin,
        source_database_hash=before,
        r3_result_id=R3_ID,
        r2_feedback_id=feedback["feedback_id"],
        r1_origin_ids=[x["experiment_id"] for x in feedback["result"]["origins"]],
        r1_readout_id=feedback["preregistration"]["r1_readout_id"],
        phase_d_campaign_id=publication["campaignId"],
        phase_d_publication_hash=_file_sha256(phase_d),
        groups=[development_tickers[::2], development_tickers[1::2]],
        blocks=[list(v) for v in policy.BLOCKS],
        candidate_slots=policy.ROUNDS,
        seed_count=2,
        arm_seconds=policy.ARM_SECONDS,
        max_gemma_calls=MAX_CALLS,
        local_provider=asdict(provider),
        horizons=list(policy.HORIZONS),
        gemma_horizon=1,
        candidate_pools=pools,
        bounds=LIMITS,
        selection=r3_policy.POLICY,
        trial_scope="development_only",
        retained_r3_challengers={
            h: shortlist["selection"][h]["trials"]["challenger"]["experiment_id"] for h in pools
        },
        **r3.FLAGS,
    )
    if "recovery" in request:
        plan["recovery"] = _recovery_binding(root, plan, request["recovery"])
    root.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    fd = os.open(root / DB, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(root / DB)) as store:
        pin = store.put("autonomous_strategy_plan", plan)
    write_new(pins / "plan.pin", pin)
    return pin


def _plan(root: Path, pin: str) -> dict[str, Any]:
    private_root(root)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        plan = store.get("autonomous_strategy_plan", pin)
    require(
        plan["version"] == "autonomous_strategy_plan_v1"
        and plan["bounds"] == LIMITS
        and plan["r3_result_id"] == R3_ID
        and plan["r2_feedback_id"] == r3.FEEDBACK_ID
        and plan["max_gemma_calls"] == MAX_CALLS
        and plan["arm_seconds"] == policy.ARM_SECONDS
        and plan["candidate_slots"] == policy.ROUNDS
        and plan["selection"] == r3_policy.POLICY
        and plan["horizons"] == list(policy.HORIZONS)
        and plan["gemma_horizon"] == 1
        and plan["blocks"] == [list(v) for v in policy.BLOCKS]
        and all(plan[k] is False for k in r3.FLAGS),
        "strategy_plan_binding",
    )
    return plan


def _recovery_binding(root: Path, plan: dict[str, Any], request: Any) -> dict[str, Any]:
    reference = fields(request, "root plan_pin_file")
    prior = Path(reference["root"])
    require(prior != root and prior.parent == root.parent, "strategy_recovery_scope")
    pin = read_pin(Path(reference["plan_pin_file"]))
    previous = _plan(prior, pin)
    design_fields = set(plan) - {"registered_at", "request", "code"}
    require(
        set(previous) == set(plan)
        and all(previous[key] == plan[key] for key in design_fields)
        and previous["request"] == {k: v for k, v in plan["request"].items() if k != "recovery"}
        and previous["code"]["versions"] == plan["code"]["versions"],
        "strategy_recovery_design_changed",
    )
    database_hash = _file_sha256(prior / DB)
    with closing(ReadOnlyAnalysisStore(prior / DB)) as store:
        ((attempt_id, attempt),) = store.all("autonomous_strategy_attempt")
        ((audit_id, audit),) = store.all("autonomous_strategy_resource_audit")
        gemma = store.get("autonomous_strategy_gemma", attempt["gemma_id"])
        require(
            attempt["plan_id"] == pin
            and audit["attempt"] == attempt_id
            and audit["returncode"] != 0
            and gemma["status"] in ("REJECTED", "UNAVAILABLE")
            and not store.all("strategy_shortlist")
            and not store.all("autonomous_strategy_result"),
            "strategy_recovery_unfinished_worker_required",
        )
    require(_file_sha256(prior / DB) == database_hash, "strategy_recovery_source_changed")
    return dict(
        root=str(prior),
        plan_id=pin,
        code=previous["code"],
        database_hash=database_hash,
        gemma_id=attempt["gemma_id"],
        resource_audit_id=audit_id,
        resource_audit=audit,
    )


def _recovery_root(plan: dict[str, Any]) -> Path:
    reference = plan["recovery"]
    prior = Path(reference["root"])
    private_root(prior)
    require(
        _file_sha256(prior / DB) == reference["database_hash"],
        "strategy_recovery_source_changed",
    )
    return prior


def _source(plan: dict[str, Any]) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    prior = Path(plan["request"]["r3_root"])
    require(
        read_pin(Path(plan["request"]["r3_plan_pin_file"])) == plan["source_plan_id"]
        and _file_sha256(prior / DB) == plan["source_database_hash"],
        "strategy_source_integrity",
    )
    source_plan, trials, _ = _prior(prior, plan["source_plan_id"])
    return prior, source_plan, trials


def _blocks(
    store: ReadOnlyAnalysisStore,
    trial: dict[str, Any],
    groups: list[list[str]],
) -> dict[str, Any]:
    if trial["state"] != "completed":
        return trial
    retained = store.get("predictive_research_rows", trial["rows_id"])
    require(retained["experiment_id"] == trial["experiment_id"], "strategy_trial_rows_binding")
    return trial | dict(blocks=policy.partitions(retained["rows"], groups))


def _memory(
    prior: Path,
    plan: dict[str, Any],
    trials: list[dict[str, Any]],
    h: int,
) -> list[dict[str, Any]]:
    with closing(ReadOnlyAnalysisStore(prior / DB)) as store:
        history = [
            _blocks(store, trial, plan["groups"])
            for trial in trials
            if trial["config"]["goal"]["horizon"] == h and trial["config"]["training_size"] == 16
        ]
    controls = {x["evaluation_population"] for x in history if x["state"] == "completed"}
    require(len(controls) == 1, "strategy_unmatched_memory")
    return history


def _seeds(trials: list[dict[str, Any]], plan: dict[str, Any], h: int) -> list[dict[str, Any]]:
    baseline = r3._control(trials, h)
    challenger = plan["retained_r3_challengers"][str(h)]
    other = next((t for t in trials if t["experiment_id"] == challenger), None)
    if other is None or other["experiment_id"] == baseline["experiment_id"]:
        other = next(
            t
            for t in trials
            if t["config"]
            == r3._candidate_configuration(h, 16, r3.LR, "v2_multi" if h == 5 else "v2_legacy")
        )
    require(
        baseline["evaluation_population"] == other["evaluation_population"]
        and baseline["experiment_id"] != other["experiment_id"],
        "strategy_seed_binding",
    )
    return [baseline, other]


def _gemma(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    from donghak_stock_vision.storage.gemma_research import Ollama
    from donghak_stock_vision.storage.grounded_evidence import GroundedAgent
    from donghak_stock_vision.storage.llm_providers import ProviderFactory, local_resource_ready

    began = time.monotonic()
    config = ModelConfig(**plan["local_provider"])
    require(
        config.provider_id == "ollama" and config.model_id == "gemma4:e2b", "strategy_local_only"
    )
    offline = dict(status="UNAVAILABLE", calls=0, elapsed_seconds=0.0)
    if not local_resource_ready(config):
        return offline | dict(reason="gemma_resources_unavailable")
    backend = Ollama()
    try:
        if not backend.idle():
            return offline | dict(reason="gemma_preexisting_model_active")
    except Exception:
        return offline | dict(reason="gemma_loopback_unavailable")
    finally:
        backend.close()
    prior, _, trials = _source(plan)
    h = plan["gemma_horizon"]
    memory = _memory(prior, plan, trials, h)
    scope = dict(cohort="r3_development", horizon=h, result_id=R3_ID)
    history = []
    for t in memory:
        if t["state"] != "completed":
            continue
        m, b = t["summary"]["metrics"], t["baselines"]
        row = dict(
            scope=scope,
            experiment_id=t["experiment_id"],
            config=t["config"],
            state=t["state"],
            mean_ap=m["pr_auc_ap"],
            mean_ba=m["balanced_accuracy"],
            mean_brier=m["brier_score"],
            overfit_gap=t["largest_train_gap"],
            worst_balanced_accuracy=min(
                x["metrics"]["balanced_accuracy"]
                for x in t["blocks"].values()
                if x["metrics"]["balanced_accuracy"] is not None
            ),
            baseline_ap_gain=m["pr_auc_ap"] - max(x["pr_auc_ap"] for x in b.values()),
            calibration_delta=m["brier_score"] - b["training_prior"]["brier_score"],
        )
        history.append(row | dict(content_hash=digest(row)))
    options = plan["candidate_pools"][str(h)]
    counterfactual, _ = policy.choose(
        options, _seeds(memory, plan, h), memory, r3._control(memory, h), h
    )
    view = dict(
        evidence_scope=scope,
        history=history,
        candidates=options,
        budget=policy.ROUNDS,
        goal=options[0]["config"]["goal"],
        cohort=scope["cohort"],
        counterfactual_required=True,
    )
    agent = GroundedAgent(root, ProviderFactory().create(config), "compact")
    try:
        choice = agent.propose(view)
        accepted = agent.unloaded and time.monotonic() - began < plan["arm_seconds"]
        status = "ACCEPTED" if accepted else "UNAVAILABLE"
        result = dict(status=status, chosen=choice if accepted else None)
    except ValueError as error:
        result = dict(status="REJECTED", reason=str(error))
    return result | dict(
        calls=agent.audit.get("metadata", {}).get("inference_calls", 0),
        elapsed_seconds=time.monotonic() - began,
        diagnostic=agent.diagnostic,
        python_counterfactual=counterfactual["experiment_id"],
    )


def _explore(
    root: Path,
    pin: str,
    plan: dict[str, Any],
    source: dict[str, Any],
    h: int,
    arm: str,
    initial: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    train: Any,
    development: Any,
    gemma: dict[str, Any],
) -> dict[str, Any]:
    began = time.monotonic()
    inference = gemma["elapsed_seconds"] if arm == "D" else 0.0
    own = initial.copy()
    steps = []
    options = plan["candidate_pools"][str(h)].copy()
    stop = "candidate_budget_exhausted"
    for i in range(plan["candidate_slots"]):
        selection_started = time.monotonic()
        if selection_started - began + inference >= plan["arm_seconds"]:
            stop = "arm_time_budget"
            break
        if not options:
            stop = "eligible_candidate_pool_exhausted"
            break
        choice, rationale = policy.choose(
            options, own, memory if arm in ("C", "D") else [], initial[0], h
        )
        parent = own[-1]["experiment_id"]
        if arm == "D" and i == 0:
            choice = next(c for c in options if c["experiment_id"] == gemma["chosen"])
            parent = gemma["diagnostic"]["original_experiment_id"]
        options.remove(choice)
        with closing(AnalysisStore(root / DB)) as store:
            decision_id = store.put(
                "strategy_decision",
                dict(
                    plan_id=pin,
                    arm=arm,
                    horizon=h,
                    round=i + 1,
                    candidate=choice,
                    rationale=rationale,
                    parent_experiment=parent,
                    eligible_options=[c["experiment_id"] for c in options],
                    prior_own_results=[x["experiment_id"] for x in own],
                    r2_feedback_id=plan["r2_feedback_id"],
                    r3_result_id=plan["r3_result_id"],
                    r1_origin_ids=plan["r1_origin_ids"],
                    hypothesis=(
                        "test_local_gemma_contrast"
                        if arm == "D" and i == 0
                        else "test_catalog_contrast_against_observed_failures"
                        if arm == "C" or arm == "D"
                        else "existing_ranked_catalog_contrast"
                    ),
                ),
            )
        selected_at = time.monotonic()
        key = choice["experiment_id"]
        cached = key in cache
        timings: dict[str, float] = {}
        if not cached:
            trial = r3._trial(
                root,
                source | dict(code=plan["code"]),
                pin,
                choice["config"],
                train,
                development,
                {},
                parent,
                "autonomous_strategy_trial",
                timings=timings,
            )
            with closing(ReadOnlyAnalysisStore(root / DB)) as store:
                cache[key] = _blocks(store, trial, plan["groups"])
        finished = time.monotonic()
        trial = cache[key]
        if trial["state"] == "completed":
            require(
                trial["evaluation_population"] == initial[0]["evaluation_population"],
                "strategy_trial_population_changed",
            )
        own.append(trial)
        steps.append(
            dict(
                decision_id=decision_id,
                candidate_id=key,
                experiment_id=trial["experiment_id"],
                state=trial["state"],
                parent_experiment=parent,
                cache=cached,
                recovered_plan=trial.get("recovered_plan"),
                selection_seconds=selected_at - selection_started,
                execution_seconds=finished - selected_at,
                total_elapsed_seconds=finished - began + inference,
                timings=timings,
                reference_execution_seconds=trial["wall_seconds"],
                without_memory=rationale["counterfactual"],
            )
        )
    return dict(
        initial=[t["experiment_id"] for t in initial],
        own=own,
        steps=steps,
        stop=stop,
        search_seconds=time.monotonic() - began + inference,
    )


def _select(arm: str, exploration: dict[str, Any], h: int) -> dict[str, Any]:
    began = time.monotonic()
    own = [t for t in exploration["own"] if t["state"] == "completed"]
    control = own[0]
    checks = {x["experiment_id"]: policy.stable(x, control, h) for x in own}
    if arm == "A":
        selected, reason = r3_policy.select(own, h, control)
    else:
        good = [
            x
            for x in own
            if x["experiment_id"] != control["experiment_id"]
            and checks[x["experiment_id"]]["passed"]
        ]
        selected = (
            min(
                good,
                key=lambda x: (-checks[x["experiment_id"]]["worst_ba_delta"], x["experiment_id"]),
            )
            if good
            else control
        )
        reason = (
            "stable_challenger"
            if good
            else (
                "ABSTAIN_INSUFFICIENT_BLOCK_EVIDENCE"
                if not all(x["evidence_sufficient"] for x in checks.values())
                else "ABSTAIN_NO_STABLE_EDGE"
            )
        )
    return dict(
        selected=selected,
        reason=reason,
        stability=checks,
        steps=exploration["steps"],
        initial=exploration["initial"],
        stop=exploration["stop"],
        search_seconds=exploration["search_seconds"],
        final_selection_seconds=time.monotonic() - began,
        candidate_evaluations=sum(s["state"] == "completed" for s in exploration["steps"]),
        actual_fits=sum(not s["cache"] and s["state"] == "completed" for s in exploration["steps"]),
        cache_uses=sum(s["cache"] for s in exploration["steps"]),
        failed=sum(s["state"] != "completed" for s in exploration["steps"]),
    )


def _copy_model(root: Path, prior: Path, trial: dict[str, Any]) -> None:
    target = root / trial["checkpoint"]
    if target.exists():
        require(_file_sha256(target) == trial["model_id"], "strategy_checkpoint_integrity")
        return
    source = prior / trial["checkpoint"]
    require(
        source.resolve() == source
        and source.is_file()
        and source.stat().st_size <= LIMITS["model_maximum_mb"] * 1024**2,
        "strategy_prior_checkpoint_scope",
    )
    body = source.read_bytes()
    require(hashlib.sha256(body).hexdigest() == trial["model_id"], "strategy_prior_checkpoint_pin")
    require(
        r3._checkpoint(root, trial["experiment_id"], body)["checkpoint"] == trial["checkpoint"],
        "strategy_checkpoint_identity",
    )


def _recovered_trials(
    root: Path, plan: dict[str, Any], train: Any, development: Any
) -> dict[str, dict[str, Any]]:
    if "recovery" not in plan:
        return {}
    prior = _recovery_root(plan)
    recovery = plan["recovery"]
    recovered = {}
    with (
        closing(ReadOnlyAnalysisStore(prior / DB)) as reader,
        closing(AnalysisStore(root / DB)) as writer,
    ):
        for trial_id, trial in reader.all("predictive_research_trial"):
            config = trial["config"]
            h = config["goal"]["horizon"]
            train_id = digest([sample.to_dict() for sample in train[h][16]])
            key = digest(config)
            require(
                any(option["config"] == config for option in plan["candidate_pools"][str(h)])
                and trial["experiment_id"]
                == digest(dict(plan_id=recovery["plan_id"], config=config, train=train_id))
                and trial["training_population"] == train_id
                and trial["reason"] == "autonomous_strategy_trial"
                and trial["feedback_id"] == plan["r2_feedback_id"]
                and trial["state"] == "completed"
                and trial["cache_source"] is None
                and key not in recovered
                and trial["evaluation_population"]
                == digest([sample.to_dict() for sample in development[h][0]]),
                "strategy_recovered_trial_binding",
            )
            rows = reader.get("predictive_research_rows", trial["rows_id"])
            require(rows["experiment_id"] == trial["experiment_id"], "strategy_trial_rows_binding")
            _copy_model(root, prior, trial)
            require(
                writer.put_many(
                    [("predictive_research_rows", rows), ("predictive_research_trial", trial)]
                )
                == [trial["rows_id"], trial_id],
                "strategy_recovered_artifact_identity",
            )
            recovered[key] = _blocks(reader, trial, plan["groups"]) | dict(
                recovered_plan=recovery["plan_id"], recovered_trial_id=trial_id
            )
    return recovered


def worker(root: Path, pin: str, attempt: str) -> None:
    began = time.monotonic()
    plan = _plan(root, pin)
    require(plan["code"] == provenance(), "strategy_code_changed")
    with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
        dispatch = reader.get("autonomous_strategy_attempt", attempt)
        require(dispatch["plan_id"] == pin, "strategy_attempt_binding")
        gemma = reader.get("autonomous_strategy_gemma", dispatch["gemma_id"])
    prior, source, trials = _source(plan)
    verified = [source_owner(s) for s in source["sources"]]
    require(verified == source["sources"], "strategy_source_changed")
    root.joinpath("models").mkdir(mode=0o700)
    dev = outcome_panels(verified[1], "calibration", source["development_tickers"], policy.HORIZONS)
    train = {h: r3._train(source, h) for h in policy.HORIZONS}
    arms: dict[str, Any] = {}
    evaluated = _recovered_trials(root, plan, train, dev)
    for h in policy.HORIZONS:
        memory = _memory(prior, plan, trials, h)
        initial = _seeds(memory, plan, h)
        require(
            digest([s.to_dict() for s in dev[h][0]]) == initial[0]["evaluation_population"],
            "strategy_development_population_changed",
        )
        exploration = {
            arm: _explore(
                root,
                pin,
                plan,
                source,
                h,
                arm,
                initial,
                memory,
                evaluated,
                train[h][16],
                dev[h],
                gemma,
            )
            for arm in ("A", "C")
        }
        if h == plan["gemma_horizon"] and gemma["status"] == "ACCEPTED":
            exploration["D"] = _explore(
                root,
                pin,
                plan,
                source,
                h,
                "D",
                initial,
                memory,
                evaluated,
                train[h][16],
                dev[h],
                gemma,
            )
        choices = {arm: _select(arm, result, h) for arm, result in exploration.items()}
        choices["B"] = _select("B", exploration["A"], h)
        choices["B"].update(
            shared_search="A", actual_fits=0, cache_uses=choices["A"]["candidate_evaluations"]
        )
        if "D" not in choices:
            choices["D"] = dict(
                reason=gemma.get("reason", gemma["status"])
                if h == plan["gemma_horizon"]
                else "outside_preregistered_gemma_horizon",
                selected=None,
                candidate_evaluations=0,
                actual_fits=0,
                cache_uses=0,
                steps=[],
            )
        for choice in choices.values():
            if choice["selected"] is not None:
                _copy_model(root, prior, choice["selected"])
        _copy_model(root, prior, initial[0])
        r3_comparator = next(
            t for t in trials if t["experiment_id"] == plan["retained_r3_challengers"][str(h)]
        )
        _copy_model(root, prior, r3_comparator)
        arms[str(h)] = choices

    with closing(AnalysisStore(root / DB)) as store:
        frozen_id = store.put("strategy_shortlist", dict(plan_id=pin, arms=arms, frozen_at=clock()))
    # No fixed-evaluation prices, labels, or trial comparisons have entered the policies above.
    result = []
    for study in verified:
        panels = outcome_panels(study, "evaluation", study["heldout_tickers"], policy.HORIZONS)
        for h in policy.HORIZONS:
            legacy = r3._control(trials, h)
            comparator = next(
                t for t in trials if t["experiment_id"] == plan["retained_r3_challengers"][str(h)]
            )
            matches: dict[str, tuple[dict[str, Any], list[str]]] = {}
            for arm, item in arms[str(h)].items():
                selected = item["selected"]
                if selected is not None:
                    matches.setdefault(selected["experiment_id"], (selected, []))[1].append(arm)
            panels_out = []
            for winner, represented in matches.values():
                value = r3._final(
                    root,
                    plan,
                    panels[h],
                    dict(legacy_control=legacy, challenger=winner, r3_challenger=comparator),
                    train[h],
                    h,
                )
                panels_out.append(value | dict(arms=represented))
            result.append(dict(cohort=study["cohort"], horizon=h, comparisons=panels_out))
        require(source_owner(study) == study, "strategy_original_source_changed")
    require(_file_sha256(prior / DB) == plan["source_database_hash"], "strategy_prior_changed")
    with closing(AnalysisStore(root / DB)) as store:
        store.put(
            "autonomous_strategy_result",
            dict(
                plan_id=pin,
                shortlist_id=frozen_id,
                fixed_evaluation=result,
                gemma_id=dispatch["gemma_id"],
                unique_trials=list(evaluated.values()),
                wall_seconds=time.monotonic() - began,
                **r3.FLAGS,
            ),
        )


def run(root: Path, pin: str) -> dict[str, Any]:
    from donghak_stock_vision.storage.autonomous_runner import launch
    from donghak_stock_vision.storage.gemma_research import process_resources

    plan = _plan(root, pin)
    with (
        lock(root, ".runner.lock"),
        lock(root.parent, ".autonomous-heavy-work.lock") as reservation,
    ):
        with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
            require(not reader.all("autonomous_strategy_attempt"), "strategy_already_consumed")
        unavailable = resource_reason(root, probe())
        if unavailable:
            return dict(state="BLOCKED", reason=unavailable)
        with closing(AnalysisStore(root / DB)) as store:
            require(not store.all("autonomous_strategy_reservation"), "strategy_already_reserved")
            store.put("autonomous_strategy_reservation", dict(plan_id=pin, started_at=clock()))
        if "recovery" in plan:
            with closing(ReadOnlyAnalysisStore(_recovery_root(plan) / DB)) as previous:
                gemma = previous.get("autonomous_strategy_gemma", plan["recovery"]["gemma_id"])
        else:
            gemma = _gemma(root, plan)
        resource = process_resources()
        health = resource_reason(root, probe())
        with closing(AnalysisStore(root / DB)) as store:
            gemma_id = store.put("autonomous_strategy_gemma", gemma)
            if health or not resource["verified"] or resource["rss_kb"] >= 256 * 1024:
                return dict(
                    state="BLOCKED",
                    reason=health or "strategy_worker_memory_guard",
                    gemma=gemma["status"],
                )
            attempt = store.put(
                "autonomous_strategy_attempt",
                dict(
                    plan_id=pin,
                    nonce=uuid.uuid4().hex,
                    gemma_id=gemma_id,
                ),
            )
        audit = launch(root, pin, attempt, autonomous_strategy=True, reservation_fd=reservation)
        with closing(AnalysisStore(root / DB)) as store:
            store.put("autonomous_strategy_resource_audit", audit | dict(attempt=attempt))
        return dict(
            state="COMPLETE" if audit["returncode"] == 0 and audit["reason"] is None else "BLOCKED",
            gemma=gemma["status"],
            resource_audit=audit,
        )


def report(root: Path, pin: str) -> FrozenJSON:
    plan = _plan(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        ((result_id, value),) = store.all("autonomous_strategy_result")
        ((audit_id, audit),) = store.all("autonomous_strategy_resource_audit")
        require(
            value["plan_id"] == pin and audit["returncode"] == 0 and audit["reason"] is None,
            "strategy_report_binding",
        )
        shortlist = store.get("strategy_shortlist", value["shortlist_id"])
        require(shortlist["plan_id"] == pin, "strategy_shortlist_binding")
        gemma = store.get("autonomous_strategy_gemma", value["gemma_id"])
        decisions = {key: d for key, d in store.all("strategy_decision")}
        rows = {
            t["experiment_id"]: store.get("predictive_research_rows", t["rows_id"])
            for t in value["unique_trials"]
            if "rows_id" in t
        }
    for trial in value["unique_trials"]:
        if "checkpoint" in trial:
            checkpoint = root / trial["checkpoint"]
            require(
                checkpoint.resolve() == checkpoint
                and checkpoint.is_file()
                and checkpoint.stat().st_size <= LIMITS["model_maximum_mb"] * 1024**2
                and _file_sha256(checkpoint) == trial["model_id"],
                "strategy_report_checkpoint",
            )
    return FrozenJSON.freeze(
        dict(
            result_id=result_id,
            result=value,
            shortlist=shortlist,
            gemma=gemma,
            plan=plan,
            decisions=decisions,
            development_predictions=rows,
            resource_audit=audit,
            resource_audit_id=audit_id,
        )
    )
