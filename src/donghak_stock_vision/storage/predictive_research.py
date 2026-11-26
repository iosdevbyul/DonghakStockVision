"""Pinned, bounded R3 trials using the existing learner, memory and resource worker."""

import hashlib
import os
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.models import predictive_research as design
from donghak_stock_vision.models import research_v2
from donghak_stock_vision.models.autonomous import direction_baselines, masked
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.experiment import (
    Candidate,
    estimator_bytes,
    fit_candidate,
    predict_candidate,
)
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.models.stock_generalization import catalog
from donghak_stock_vision.signals.dataset import counts
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, LIMITS, clock, private_root
from donghak_stock_vision.storage.failure_feedback import report as feedback_report
from donghak_stock_vision.storage.failure_feedback_source import (
    RANGES,
    outcome_panels,
    samples,
    source_owner,
)
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.research_checkpoint import verified_estimator
from donghak_stock_vision.storage.research_resources import lock, probe, resource_reason

FEEDBACK_ID = "884c46b73ade394f7bf52f40c83bdc68911646dea4dbc472b457108973a7f887"
FLAGS = dict(
    pit_qualified=False,
    performance_approved=False,
    execution_authorized=False,
    independent_future_performance_established=False,
    protected_final_test_read=False,
    q3_outcomes_read=False,
)
RF = "v2_forest3_32"
LR = "v2_c1.0_lbfgs_balanced"
HIST = "v2_hist7_0.03"
DEVELOPMENT_DEADLINE = 210
FINAL_RESERVE = 55


def _feedback(request: dict[str, Any]) -> dict[str, Any]:
    root = Path(request["feedback_root"])
    ref = read_pin(Path(request["feedback_plan_pin_file"]))
    value = feedback_report(root, ref).to_dict()
    require(
        value["feedback_id"] == FEEDBACK_ID
        and value["result"]["decisions"]
        == {
            "calibration": "REJECTED",
            "mask": "INSUFFICIENT_EVIDENCE",
        },
        "predictive_research_feedback_binding",
    )
    return value


def _candidate_configuration(h: int, size: int, name: str, mask: str) -> dict[str, Any]:
    found = [
        c
        for c in catalog([h])
        if c["stock_policy"] == "heldout" and c["candidate"]["name"] == name and c["mask"] == mask
    ]
    require(len(found) == 1, "predictive_research_catalog")
    return found[0] | dict(training_size=size)


def _schedule(feedback: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    h5_original = feedback["result"]["origins"][0]["config"]
    fixed = [
        _candidate_configuration(h, 16, family, "v2_legacy")
        for h in design.HORIZONS
        for family in (RF, LR)
    ]
    for h in design.HORIZONS:
        fixed.extend(
            [
                _candidate_configuration(h, 16, HIST, "v2_legacy"),
                *(_candidate_configuration(h, n, RF, "v2_legacy") for n in (8, 24)),
                *(
                    _candidate_configuration(h, 16, RF, mask)
                    for mask in ("v2_daily", "v2_daily_weekly", "v2_daily_monthly", "v2_multi")
                ),
            ]
        )
    fixed.append(
        _candidate_configuration(5, 16, h5_original["candidate"]["name"], h5_original["mask"])
    )
    pool = [
        _candidate_configuration(h, 16, name, "v2_legacy")
        for h in design.HORIZONS
        for name in (
            "v2_c0.1_lbfgs_balanced",
            "v2_c10.0_lbfgs_balanced",
            "v2_forest3_96",
            "v2_forest6_32",
            "v2_hist7_0.1",
            "v2_hist15_0.03",
        )
    ]
    require(
        len({digest(c) for c in fixed + pool}) == len(fixed) + len(pool),
        "predictive_research_duplicate_candidate",
    )
    return fixed, pool


def register(request: dict[str, Any], root: Path, pins: Path) -> str:
    fields(request, "version feedback_root feedback_plan_pin_file")
    require(request["version"] == "predictive_research_request_v1", "predictive_research_request")
    feedback = _feedback(request)
    prior = feedback["preregistration"]
    source = [source_owner(s) for s in prior["sources"]]
    require(source == prior["sources"] and len(source) == 2, "predictive_research_source_binding")
    require(
        root.parent == Path(request["feedback_root"]).parent
        and all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "predictive_research_output_scope",
    )
    first, second = source
    seen1, seen2 = [sorted(set(s["tickers"]) - set(s["heldout_tickers"])) for s in source]
    require(
        not set(first["tickers"]) & set(second["tickers"]) and len(seen1) == len(seen2) == 16,
        "predictive_research_disjoint_cohorts",
    )
    fixed, pool = _schedule(feedback)
    plan = dict(
        version="predictive_model_research_plan_v1",
        registered_at=clock(),
        request=request,
        sources=source,
        feedback_id=FEEDBACK_ID,
        feedback_decisions=feedback["result"]["decisions"],
        feedback_use=dict(
            calibration="use_r2_fpr_and_recall_guard_in_h5_development_selection",
            mask="legacy_control_and_matched_daily_ablation_keep_both",
            h5_r1_control=feedback["result"]["origins"][0]["experiment_id"],
        ),
        training_tickers={"8": seen1[:8], "16": seen1, "24": seen1 + seen2[:8]},
        development_tickers=seen2[8:],
        evaluation_tickers=[s["heldout_tickers"] for s in source],
        windows=RANGES,
        horizons=list(design.HORIZONS),
        seed=42,
        fixed=fixed,
        adaptive_pool=pool,
        adaptive_per_horizon=2,
        pilot_trials=6,
        bounds=LIMITS,
        max_trials=len(fixed) + 2 * len(design.HORIZONS),
        selection=design.POLICY,
        development_deadline_seconds=DEVELOPMENT_DEADLINE,
        final_reserve_seconds=FINAL_RESERVE,
        code=provenance(),
        evaluation_classification="internal_previously_exposed",
        **FLAGS,
    )
    root.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    fd = os.open(root / DB, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(root / DB)) as store:
        pin = store.put("predictive_research_plan", plan)
    write_new(pins / "plan.pin", pin)
    return pin


def _plan(root: Path, pin: str) -> dict[str, Any]:
    private_root(root)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        plan = store.get("predictive_research_plan", pin)
    require(
        plan["version"] == "predictive_model_research_plan_v1"
        and plan["bounds"] == LIMITS
        and plan["windows"] == RANGES
        and plan["seed"] == 42
        and plan["selection"] == design.POLICY
        and plan["feedback_id"] == FEEDBACK_ID
        and plan["development_deadline_seconds"] == DEVELOPMENT_DEADLINE
        and plan["final_reserve_seconds"] == FINAL_RESERVE
        and all(plan[k] is False for k in FLAGS),
        "predictive_research_plan_binding",
    )
    return plan


def _train(plan: dict[str, Any], h: int) -> dict[int, list[Sample]]:
    first, second = plan["sources"]
    groups = plan["training_tickers"]
    left = samples(first, "train", horizon=h, tickers=groups["16"])
    right = samples(second, "train", horizon=h, tickers=groups["24"][16:])
    sets = {8: [s for s in left if s.ticker in groups["8"]], 16: left, 24: left + right}
    for size, rows in sets.items():
        c = counts(rows)
        require(
            c["tickers"] == size
            and c["dates"] >= 252
            and c["rows"] >= 1000
            and min(c["positive"], c["negative"]) >= 50,
            "predictive_research_training_minimum",
        )
    return sets


def _checkpoint(root: Path, name: str, body: bytes) -> dict[str, str]:
    require(
        len(body) <= LIMITS["model_maximum_mb"] * 1024**2, "predictive_research_checkpoint_bound"
    )
    path = root / "models" / (name + ".checkpoint")
    with path.open("xb") as out:
        out.write(body)
        out.flush()
        os.fsync(out.fileno())
    path.chmod(0o600)
    return dict(checkpoint=str(path.relative_to(root)), model_id=hashlib.sha256(body).hexdigest())


def _r2_cache(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    feedback = _feedback(plan["request"])
    if (
        feedback["preregistration"]["code"]["versions"] != plan["code"]["versions"]
        or feedback["preregistration"]["label_contract"] != research_v2.contract(5)
        or feedback["preregistration"]["ranges"]["train"] != RANGES["train"]
    ):
        return {}
    first = next(
        c for c in feedback["result"]["cases"] if c["cohort"] == plan["sources"][0]["cohort"]
    )
    calibration = feedback["calibrators"][first["calibrator_id"]]
    return {
        digest(dict(config=m["config"], population=calibration["training_population"])): m
        for m in first["models"].values()
        if m["seed"] == plan["seed"]
    }


def _fit(
    root: Path,
    plan: dict[str, Any],
    config: dict[str, Any],
    train: list[Sample],
    train_pin: str,
    cache: dict[str, dict[str, Any]],
    experiment_id: str,
) -> tuple[Any, dict[str, Any]]:
    core = {k: config[k] for k in ("candidate", "mask")}
    old = (
        cache.get(digest(dict(config=core, population=train_pin)))
        if config["goal"]["horizon"] == 5
        else None
    )
    if old is None:
        model = fit_candidate(
            Candidate(**config["candidate"]),
            masked(train, config["mask"]),
            feature_names=config["goal"]["features"],
        )
        body = estimator_bytes(model)
    else:
        path = Path(plan["request"]["feedback_root"]) / old["checkpoint"]
        model = verified_estimator(
            path, old["model_id"], config["candidate"]["family"], plan["code"]["versions"]
        )
        body = path.read_bytes()
        require(
            hashlib.sha256(body).hexdigest() == old["model_id"], "predictive_research_cache_changed"
        )
    retained = _checkpoint(root, experiment_id, body)
    return model, retained | dict(cache_source=old["experiment_id"] if old else None)


def _scored(
    observations: list[dict[str, Any]],
    scores: dict[str, list[float]],
    threshold: float,
) -> list[dict[str, Any]]:
    return [
        observation
        | dict(
            scores=score,
            predictions={k: int(v >= threshold) for k, v in score.items()},
            correct={k: int(v >= threshold) == observation["label"] for k, v in score.items()},
        )
        for observation, values in zip(
            observations, zip(*scores.values(), strict=True), strict=True
        )
        for score in [dict(zip(scores, values, strict=True))]
    ]


def _predict(config: dict[str, Any], model: Any, rows: list[Sample]) -> list[float]:
    return predict_candidate(Candidate(**config["candidate"]), model, masked(rows, config["mask"]))


def _trial(
    root: Path,
    plan: dict[str, Any],
    plan_id: str,
    config: dict[str, Any],
    train: list[Sample],
    development: tuple[list[Sample], list[dict[str, Any]], list[dict[str, Any]]],
    cache: dict[str, dict[str, Any]],
    parent: str | None,
    reason: str,
) -> dict[str, Any]:
    began = time.monotonic()
    train_pin = digest([s.to_dict() for s in train])
    experiment = digest(dict(plan_id=plan_id, config=config, train=train_pin))
    result: dict[str, Any] = dict(
        experiment_id=experiment,
        config=config,
        training_population=train_pin,
        training_counts=counts(train),
        parent_experiment=parent,
        feedback_id=FEEDBACK_ID,
        reason=reason,
        cache_source=None,
    )
    model_ready = False
    try:
        model, artifact = _fit(root, plan, config, train, train_pin, cache, experiment)
        model_ready = True
        result.update(artifact, fit_seconds=time.monotonic() - began)
        test, observation, _ = development
        require({s.label for s in test} == {0, 1}, "predictive_research_development_classes")
        values = _predict(config, model, test)
        rows = _scored(observation, {"model": values}, config["goal"]["threshold"])
        summary = design.summary(rows, "model")
        control_scores = direction_baselines(test, train)
        labels = [r["label"] for r in rows]
        train_labels = [int(s.label) for s in train if s.label is not None]
        train_ba = metrics(train_labels, _predict(config, model, train))["balanced_accuracy"]
        result.update(
            state="completed",
            summary=summary,
            evaluation_population=digest([s.to_dict() for s in test]),
            baselines={k: metrics(labels, v) for k, v in control_scores.items()},
            stability_ap=summary["metrics"]["pr_auc_ap"],
            largest_train_gap=train_ba - summary["metrics"]["balanced_accuracy"],
            wall_seconds=time.monotonic() - began,
        )
        with closing(AnalysisStore(root / DB)) as store:
            result["rows_id"] = store.put(
                "predictive_research_rows",
                dict(
                    experiment_id=experiment,
                    rows=rows,
                ),
            )
    except (ValueError, ArithmeticError, RuntimeError) as error:
        message = str(error)
        result.update(
            state="failed",
            failure=message if message.isidentifier() else type(error).__name__,
            model_ready=model_ready,
            wall_seconds=time.monotonic() - began,
        )
    with closing(AnalysisStore(root / DB)) as store:
        store.put("predictive_research_trial", result)
    return result


def _control(trials: list[dict[str, Any]], h: int) -> dict[str, Any]:
    found = [
        t
        for t in trials
        if t["state"] == "completed"
        and t["config"] == _candidate_configuration(h, 16, RF, "v2_legacy")
    ]
    require(len(found) == 1, "predictive_research_missing_control")
    return found[0]


def _final(
    root: Path,
    plan: dict[str, Any],
    panel: tuple[list[Sample], list[dict[str, Any]], list[dict[str, Any]]],
    selected: dict[str, dict[str, Any]],
    training: dict[int, list[Sample]],
    horizon: int,
) -> dict[str, Any]:
    test, observations, excluded = panel
    estimators: dict[str, Any] = {}
    scores: dict[str, list[float]] = {}
    thresholds = {research_v2.contract(horizon)["threshold"]}
    measures = []
    for arm, trial in selected.items():
        cfg = trial["config"]
        thresholds.add(cfg["goal"]["threshold"])
        name = trial["experiment_id"]
        if name not in estimators:
            estimators[name] = verified_estimator(
                root / trial["checkpoint"],
                trial["model_id"],
                cfg["candidate"]["family"],
                plan["code"]["versions"],
            )
        scores[arm] = _predict(cfg, estimators[name], test)
    require(len(thresholds) == 1, "predictive_research_threshold_binding")
    scores.update(direction_baselines(test, training[16]))
    rows = _scored(observations, scores, thresholds.pop())
    measured = {arm: design.summary(rows, arm) for arm in scores}
    for baseline in scores:
        if baseline != "challenger":
            measures.append(
                dict(
                    baseline=baseline,
                    **design.compare(rows, measured, baseline, "challenger", horizon),
                )
            )
    selected_size = selected["challenger"]["config"]["training_size"]
    candidate_baselines = direction_baselines(test, training[selected_size])
    return dict(
        horizon=horizon,
        population=digest([s.to_dict() for s in test]),
        measures=measured,
        paired=measures,
        trials={k: t["experiment_id"] for k, t in selected.items()},
        challenger_training_baselines={
            k: metrics([r["label"] for r in rows], v) for k, v in candidate_baselines.items()
        },
        rows=rows,
        exclusions=excluded,
    )


def worker(root: Path, pin: str, attempt: str) -> None:
    plan = _plan(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
        require(
            reader.get("predictive_research_attempt", attempt)["plan_id"] == pin
            and not reader.all("predictive_research_trial"),
            "predictive_research_attempt_binding",
        )
    require(plan["code"] == provenance(), "predictive_research_runtime_changed")
    began = time.monotonic()
    ls = [source_owner(s) for s in plan["sources"]]
    require(ls == plan["sources"], "predictive_research_sources_changed")
    train = {h: _train(plan, h) for h in design.HORIZONS}
    cache = _r2_cache(plan)
    develop = outcome_panels(ls[1], "calibration", plan["development_tickers"], design.HORIZONS)
    directory = root / "models"
    directory.mkdir(mode=0o700)
    trials: list[dict[str, Any]] = []

    def execute(config: dict[str, Any], parent: str | None, reason: str) -> None:
        h = config["goal"]["horizon"]
        require(
            len(trials) < plan["max_trials"] and all(t["config"] != config for t in trials),
            "predictive_research_repeat_or_budget",
        )
        trials.append(
            _trial(
                root,
                plan,
                pin,
                config,
                train[h][config["training_size"]],
                develop[h],
                cache,
                parent,
                reason,
            )
        )

    for config in plan["fixed"][: plan["pilot_trials"]]:
        execute(config, None, "pilot_baseline_and_family_control")
    pilot = dict(
        trials=len(trials),
        completed=sum(t["state"] == "completed" for t in trials),
        trial_seconds=[t["wall_seconds"] for t in trials],
        elapsed_seconds=time.monotonic() - began,
    )
    fastest_safe_bound = max(pilot["trial_seconds"])
    projected = (
        pilot["elapsed_seconds"]
        + float(fastest_safe_bound) * (plan["max_trials"] - plan["pilot_trials"])
        + plan["final_reserve_seconds"]
    )
    continue_after_pilot = (
        pilot["completed"] == plan["pilot_trials"] and projected <= LIMITS["timeout_seconds"]
    )
    with closing(AnalysisStore(root / DB)) as store:
        store.put(
            "predictive_research_pilot",
            pilot
            | dict(
                projected_seconds=projected,
                extended=continue_after_pilot,
            ),
        )
    selected = {}
    if continue_after_pilot:
        for config in plan["fixed"][plan["pilot_trials"] :]:
            if time.monotonic() - began >= plan["development_deadline_seconds"]:
                break
            h = config["goal"]["horizon"]
            reason = "fixed_scale_feature_or_family_ablation"
            if h == 5 and config["mask"] == "v2_multi" and config["candidate"]["name"] == LR:
                reason = "r1_profile_bound_by_r2_feedback"
            execute(config, _control(trials, h)["experiment_id"], reason)
    for h in design.HORIZONS:
        control = _control(trials, h)
        history = [t for t in trials if t["config"]["goal"]["horizon"] == h]
        options = [c for c in plan["adaptive_pool"] if c["goal"]["horizon"] == h]
        for _ in range(plan["adaptive_per_horizon"]):
            if not continue_after_pilot or time.monotonic() - began >= DEVELOPMENT_DEADLINE:
                break
            eligible = [t for t in history if t["state"] == "completed"]
            parent, _ = design.select(eligible, h, control)
            family = parent["config"]["candidate"]["family"]
            preferred = [c for c in options if c["candidate"]["family"] == family]
            pool = preferred or options
            selected_option = research_v2.rank(
                [dict(config=c, experiment_id=digest(c)) for c in pool], history
            )[0]["config"]
            execute(selected_option, parent["experiment_id"], "development_ranked_complexity")
            options.remove(selected_option)
            history.append(trials[-1])
        entries = [t for t in history if t["state"] == "completed"]
        choice, reason = design.select(entries, h, control)
        named = {"legacy_control": control, "challenger": choice}
        if h == 5:
            originals = [
                t
                for t in entries
                if t["config"]["mask"] == "v2_multi" and t["config"]["candidate"]["name"] == LR
            ]
            if originals:
                named["r1_profile_control"] = originals[0]
        selected[str(h)] = dict(
            reason=reason,
            trials=named,
            selection_panel_population=choice["evaluation_population"],
            development_population_sufficient=design.meets_population(choice["summary"]),
        )
    with closing(AnalysisStore(root / DB)) as store:
        freeze = store.put(
            "predictive_research_shortlist",
            dict(
                plan_id=pin,
                selection=selected,
                frozen_at=clock(),
            ),
        )
    # Evaluation samples, labels, and prices are first read after the shortlist is durable.
    evaluations = []
    for study, tickers in zip(ls, plan["evaluation_tickers"], strict=True):
        panels = outcome_panels(study, "evaluation", tickers, design.HORIZONS)
        for h in design.HORIZONS:
            evaluations.append(
                dict(cohort=study["cohort"])
                | _final(root, plan, panels[h], selected[str(h)]["trials"], train[h], h)
            )
        require(source_owner(study) == study, "predictive_research_source_changed")
    with closing(AnalysisStore(root / DB)) as store:
        store.put(
            "predictive_research_result",
            dict(
                plan_id=pin,
                shortlist_id=freeze,
                evaluations=evaluations,
                pilot=pilot | dict(projected_seconds=projected, extended=continue_after_pilot),
                counters=dict(
                    planned=plan["max_trials"],
                    attempted=len(trials),
                    completed=sum(t["state"] == "completed" for t in trials),
                    failed=sum(t["state"] == "failed" for t in trials),
                    new_fits=sum("model_id" in t and t["cache_source"] is None for t in trials),
                    exact_r2_reuse=sum(t["cache_source"] is not None for t in trials),
                    calibration_fits=0,
                    network_requests=0,
                    llm_calls=0,
                ),
                wall_seconds=time.monotonic() - began,
                **FLAGS,
            ),
        )


def run(root: Path, pin: str) -> dict[str, Any]:
    from donghak_stock_vision.storage.autonomous_runner import launch
    from donghak_stock_vision.storage.gemma_research import process_resources

    _plan(root, pin)
    with (
        lock(root, ".runner.lock"),
        lock(root.parent, ".autonomous-heavy-work.lock") as reservation,
    ):
        with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
            require(
                not reader.all("predictive_research_attempt"),
                "predictive_research_attempt_consumed",
            )
        reason = resource_reason(root, probe())
        resident = process_resources()
        if reason or not resident.get("verified") or resident["rss_kb"] >= 256 * 1024:
            return dict(state="BLOCKED", reason=reason or "ollama_release_resources_unverified")
        with closing(AnalysisStore(root / DB)) as store:
            attempt = store.put(
                "predictive_research_attempt", dict(plan_id=pin, nonce=uuid.uuid4().hex)
            )
        audit = launch(root, pin, attempt, predictive_research=True, reservation_fd=reservation)
        with closing(AnalysisStore(root / DB)) as store:
            store.put("predictive_research_resource_audit", audit | dict(attempt=attempt))
        return dict(
            state="COMPLETE" if audit["returncode"] == 0 and audit["reason"] is None else "BLOCKED",
            resource_audit=audit,
        )


def report(root: Path, pin: str) -> FrozenJSON:
    plan = _plan(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        (result,) = store.all("predictive_research_result")
        audits = [v for _, v in store.all("predictive_research_resource_audit")]
        require(
            result[1]["plan_id"] == pin
            and len(audits) == 1
            and audits[0]["returncode"] == 0
            and audits[0]["reason"] is None,
            "predictive_research_report_binding",
        )
        shortlist = store.get("predictive_research_shortlist", result[1]["shortlist_id"])
        trials = [v for _, v in store.all("predictive_research_trial")]
        trial_rows = {
            t["experiment_id"]: store.get("predictive_research_rows", t["rows_id"])
            for t in trials
            if "rows_id" in t
        }
    require(shortlist["plan_id"] == pin, "predictive_research_shortlist_binding")
    for t in trials:
        if "checkpoint" in t:
            path = root / t["checkpoint"]
            require(
                path.resolve() == path
                and path.is_file()
                and path.stat().st_size <= LIMITS["model_maximum_mb"] * 1024**2
                and hashlib.sha256(path.read_bytes()).hexdigest() == t["model_id"],
                "predictive_research_checkpoint_binding",
            )
    return FrozenJSON.freeze(
        dict(
            result_id=result[0],
            result=result[1],
            plan=plan,
            shortlist=shortlist,
            trials=trials,
            development_predictions=trial_rows,
            resource_audit=audits[0],
        )
    )
