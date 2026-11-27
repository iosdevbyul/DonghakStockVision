"""Offline R1–R6 publication from original read-only evidence owners."""

import hashlib
import json
import os
import subprocess
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage import autonomous_strategy, failure_feedback, predictive_research
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.research_lab_views import Metrics, Visibility


def _file(path: Path, maximum: int = 32 * 1024**2) -> bytes:
    require(
        path.is_absolute()
        and path.resolve() == path
        and path.is_file()
        and path.stat().st_mode & 0o077 == 0
        and 0 < path.stat().st_size <= maximum,
        "private_research_evidence_file",
    )
    with path.open("rb") as reader:
        body = reader.read(maximum + 1)
    require(0 < len(body) <= maximum, "research_evidence_file_bound")
    return body


def _hash(path: Path, maximum: int = 128 * 1024**2) -> str:
    require(path.resolve() == path and path.is_file(), "research_evidence_hash_scope")
    require(0 < path.stat().st_size <= maximum, "research_evidence_hash_bound")
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def _pinned(path: str, pin_file: str) -> dict[str, Any]:
    document = FrozenJSON(_file(Path(path)).decode())
    require(document.identifier == read_pin(Path(pin_file)), "research_evidence_pin_mismatch")
    return document.to_dict()


def _study(
    ref: dict[str, Any], owner: Callable[[Path, str], FrozenJSON]
) -> tuple[Path, dict[str, Any]]:
    fields(ref, "report plan_pin_file")
    report = Path(ref["report"])
    pin = read_pin(Path(ref["plan_pin_file"]))
    retained = FrozenJSON(_file(report).decode())
    require(retained == owner(report.parent, pin), "research_evidence_owner_mismatch")
    return report.parent, retained.to_dict()


def _record(root: Path, kind: str, key: str) -> str:
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        return store.created_at(kind, key).isoformat()


def _metrics(d: dict[str, Any]) -> dict[str, Any]:
    out = {k: d[k] for k in Metrics.model_fields if k in d}
    if cm := out.get("confusion_matrix"):
        tn, fp, fn, tp = (cm[k] for k in ("tn", "fp", "fn", "tp"))
        out.update(
            correct=tp + tn,
            incorrect=fp + fn,
            fpr=fp / (tn + fp) if tn + fp else None,
            fdr=fp / (tp + fp) if tp + fp else None,
            down_precision=tn / (tn + fn) if tn + fn else None,
            down_recall=tn / (tn + fp) if tn + fp else None,
        )
    return out


def _comparison(
    evaluation: dict[str, Any],
    baseline: str,
    reference: dict[str, Any],
    *,
    paired: list[tuple[bool, bool]] | None = None,
    experiment_id: str | None = None,
    status: str = "historical_same_population",
) -> dict[str, Any]:
    model = evaluation["metrics"]
    delta = {
        k: model[k] - reference[k]
        if model.get(k) is not None and reference.get(k) is not None
        else None
        for k in (
            "correct",
            "incorrect",
            "accuracy",
            "balanced_accuracy",
            "pr_auc_ap",
            "brier_score",
            "fpr",
            "recall",
        )
    }
    transitions = None
    if paired is not None:
        require(len(paired) == evaluation["evaluatedRows"], "research_pair_denominator")
        values = Counter(paired)
        transitions = dict(
            correct_to_correct=values[True, True],
            correct_to_incorrect=values[True, False],
            incorrect_to_correct=values[False, True],
            incorrect_to_incorrect=values[False, False],
        )
    require(reference.get("n") == evaluation["evaluatedRows"], "research_baseline_denominator")
    return dict(
        evaluationId=evaluation["evaluationId"],
        baseline=baseline,
        baselineExperimentId=experiment_id,
        populationId=evaluation["populationId"],
        comparable=True,
        status=status,
        baselineMetrics=reference,
        delta=delta,
        transitions=transitions,
    )


class _Projection:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.experiments: dict[tuple[str, str], dict[str, Any]] = {}
        self.evaluations: list[dict[str, Any]] = []
        self.predictions: list[dict[str, Any]] = []
        self.comparisons: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []
        self.controls: dict[int, tuple[dict[str, Any], list[dict[str, Any]]]] = {}

    def experiment(
        self,
        run: dict[str, Any],
        trial: dict[str, Any],
        h: int,
        *,
        choice: dict[str, Any] | None = None,
        evidence: str | None = None,
        reference: bool = False,
    ) -> None:
        cfg = trial.get("config", trial)
        cand = cfg.get("candidate", {})
        counts = trial.get("training_counts", {})
        n = counts.get("rows")
        row = dict(
            runId=run["runId"],
            stage=run["stage"],
            experimentId=trial["experiment_id"],
            modelId=trial.get("model_id"),
            parentExperimentId=trial.get("parent_experiment", trial.get("parent_experiment_id")),
            horizon=h,
            candidate=cand.get("name"),
            family=cand.get("family"),
            mask=cfg.get("mask"),
            parameters=cand.get("parameters", {}),
            candidateValue=cand.get("value"),
            trainingPopulationId=digest(dict(purpose="training_population", original=pop))
            if (pop := trial.get("training_population"))
            else None,
            trainingRows=n,
            trainingTickers=counts.get("tickers"),
            trainingPrior=counts["positive"] / n if n else trial.get("training_prior"),
            fitSeconds=trial.get("fit_seconds", trial.get("wall_seconds")),
            status=trial.get("state", "retained"),
            evidenceStatus=evidence,
            referenceOnly=reference,
            metrics=_metrics(trial["summary"]["metrics"]) if "summary" in trial else None,
        )
        if choice:
            row["candidateId"] = choice["candidate"]["experiment_id"]
            row["order"] = choice["index"]
        key = (row["runId"], row["experimentId"])
        if key in self.experiments:
            require(
                self.experiments[key]["modelId"] == row["modelId"], "research_model_id_conflict"
            )
        else:
            self.experiments[key] = row

    def evaluate(
        self,
        run: dict[str, Any],
        trial: dict[str, Any],
        h: int,
        rows: list[dict[str, Any]],
        summary: dict[str, Any],
        *,
        role: str,
        exposure: str,
        score: str,
        tag: Any,
        baselines: dict[str, Any] | None = None,
        exclusions: list[dict[str, Any]] | None = None,
        status: str | None = None,
        order: int | None = None,
        transform: str | None = None,
    ) -> dict[str, Any]:
        metrics = _metrics(summary.get("metrics", summary))
        require(metrics["n"] == len(rows), "research_evaluation_denominator")
        evidence = summary.get("evidence", {})
        sample_ids = [(r["sample_id"], r.get("label", r.get("actual_label"))) for r in rows]
        sample_key = digest(dict(horizon=h, population=sorted(sample_ids)))
        contract = f"delayed_close_direction_h{h}_v2"
        population = digest(dict(contract=contract, exposure=exposure, sample=sample_key))
        key = digest(
            dict(
                run=run["runId"],
                experiment=trial["experiment_id"],
                role=role,
                population=population,
                tag=tag,
            )
        )
        row = dict(
            evaluationId=key,
            runId=run["runId"],
            stage=run["stage"],
            experimentId=trial["experiment_id"],
            modelId=trial["model_id"],
            horizon=h,
            exposure=exposure,
            populationId=population,
            role=role,
            contract=contract,
            transformId=transform,
            order=order,
            metrics=metrics,
            baselines={k: _metrics(v) for k, v in (baselines or {}).items()},
            evaluatedRows=len(rows),
            tickers=len({r["ticker"] for r in rows}),
            tradingDates=len({r["input_date"] for r in rows}),
            anchors=evidence.get("nonoverlapping_calendar_anchors"),
            evidenceStatus=status or evidence.get("status", "retained"),
        )
        if exclusions is not None:
            neutral = sum(x.get("reason") == "research_neutral_cost_band" for x in exclusions)
            row.update(
                candidateRows=len(rows) + len(exclusions),
                neutralRows=neutral,
                excludedRows=len(exclusions) - neutral,
            )
        self.evaluations.append(row)
        for source in rows:
            actual = source.get("label", source.get("actual_label"))
            probability = source["scores"][score] if score else source["prediction_score"]
            correct = source["correct"][score] if score else source["outcome"] == "correct"
            self.predictions.append(
                dict(
                    evaluationId=key,
                    predictionId=source["sample_id"],
                    ticker=source["ticker"],
                    inputDate=source["input_date"],
                    baseDate=source["reference_date"],
                    targetDate=source["target_date"],
                    baseClose=source["reference_price"],
                    targetClose=source["actual_price"],
                    actualReturn=source["actual_return"],
                    probability=probability,
                    predictedClass=int(probability >= 0.5),
                    actualClass=actual,
                    correct=correct,
                    state="evaluated",
                )
            )
        for omitted in exclusions or []:
            reason = omitted["reason"]
            self.predictions.append(
                dict(
                    evaluationId=key,
                    predictionId=digest(omitted),
                    ticker=omitted["ticker"],
                    inputDate=omitted["input_date"],
                    reason=reason,
                    state="neutral" if reason == "research_neutral_cost_band" else "excluded",
                )
            )
        return row

    def dev(
        self,
        run: dict[str, Any],
        trial: dict[str, Any],
        h: int,
        rows: list[dict[str, Any]],
        *,
        decision: dict[str, Any] | None = None,
        stability: dict[str, Any] | None = None,
    ) -> None:
        selected = "ABSTAIN" if stability is not None and not stability["passed"] else None
        evidence = (
            "INSUFFICIENT_EVIDENCE" if stability and not stability["evidence_sufficient"] else None
        )
        self.experiment(run, trial, h, choice=decision, evidence=evidence)
        view = self.evaluate(
            run,
            trial,
            h,
            rows,
            trial["summary"],
            role="model",
            exposure="development",
            score="model",
            tag="development",
            baselines=trial["baselines"],
            order=decision["index"] if decision else None,
            status=evidence,
        )
        view["selection"] = selected
        if stability:
            view["blockAnchors"] = {k: v["anchors"] for k, v in stability["panels"].items()}
            view["blockBaDelta"] = {k: v["ba_delta"] for k, v in stability["panels"].items()}
        for key, metric in trial["baselines"].items():
            self.comparisons.append(_comparison(view, key, _metrics(metric)))
        control, control_rows = self.controls[h]
        if control["experiment_id"] == trial["experiment_id"]:
            return
        left = {r["sample_id"]: r for r in control_rows}
        matched = len(left) == len(rows) and all(
            r["sample_id"] in left and r["label"] == left[r["sample_id"]]["label"] for r in rows
        )
        if matched and control["evaluation_population"] == trial["evaluation_population"]:
            self.comparisons.append(
                _comparison(
                    view,
                    "legacy_control",
                    _metrics(control["summary"]["metrics"]),
                    paired=[
                        (left[r["sample_id"]]["correct"]["model"], r["correct"]["model"])
                        for r in rows
                    ],
                    experiment_id=control["experiment_id"],
                    status=evidence or "historical_same_population",
                )
            )
        else:
            self.comparisons.append(
                dict(
                    evaluationId=view["evaluationId"],
                    baseline="legacy_control",
                    baselineExperimentId=control["experiment_id"],
                    populationId=view["populationId"],
                    comparable=False,
                    status="NOT_COMPARABLE",
                    delta={},
                )
            )

    def fixed(
        self,
        run: dict[str, Any],
        panels: list[dict[str, Any]],
        candidates: dict[str, dict[str, Any]],
    ) -> None:
        for position, panel in enumerate(panels):
            h = panel["horizon"]
            rows = panel["rows"]
            measures = panel["measures"]
            baselines = {k: v["metrics"] for k, v in measures.items() if k not in panel["trials"]}
            for role, identifier in panel["trials"].items():
                trial = candidates[identifier]
                self.experiment(run, trial, h, reference=True)
                view = self.evaluate(
                    run,
                    trial,
                    h,
                    rows,
                    measures[role],
                    role=role,
                    exposure="exposed_fixed",
                    score=role,
                    tag=position,
                    baselines=baselines,
                    exclusions=panel["exclusions"],
                )
                if role != "challenger":
                    continue
                for paired in panel["paired"]:
                    key = paired["baseline"]
                    self.comparisons.append(
                        _comparison(
                            view,
                            key,
                            _metrics(measures[key]["metrics"]),
                            paired=[(r["correct"][key], r["correct"][role]) for r in rows],
                            experiment_id=panel["trials"].get(key),
                            status=paired["status"],
                        )
                    )


def _run(stage: str, key: str, role: str, **values: Any) -> dict[str, Any]:
    return (
        dict(
            runId=key,
            stage=stage,
            role=role,
            status="COMPLETED",
            evidenceStatus="PARTIAL",
            horizons=[1, 5, 20],
            fits=0,
        )
        | values
    )


def _ranges(plan: dict[str, Any], *, evaluation: str = "calibration") -> dict[str, Any]:
    return dict(
        trainingStart=plan["train"]["start"],
        trainingEnd=plan["train"]["end"],
        developmentStart=plan[evaluation]["start"],
        developmentEnd=plan[evaluation]["end"],
    )


def _r1(p: _Projection, source: dict[str, Any]) -> None:
    fields(source, "report pin_file")
    report = _pinned(source["report"], source["pin_file"])
    run = _run(
        "R1",
        report["readout_id"],
        "retained_prediction_audit",
        evaluations=report["readout_model_cases"],
        status="PARTIAL",
    )
    p.runs.append(run)
    for t in report["development"]:
        p.experiment(run, t, t["horizon"], evidence="original_fold_rows_unavailable")
    for group in report["readout"]:
        horizon = group["horizon"]
        for role, model in group["models"].items():
            p.experiment(run, model, horizon)
            rows = model["rows"]
            denom = group["denominators"]
            evaluation = p.evaluate(
                run,
                model,
                horizon,
                rows,
                dict(metrics=model["metrics"], evidence=group["calendar"]),
                role=role,
                score="",
                exposure="historical_readout",
                tag=(group["policy"], group["stock_policy"], group["cohort"]),
                baselines=model["baselines"],
                status=group["evidence_status"],
            )
            evaluation.update(
                sourceEvaluationId=rows[0]["evaluation_id"] if rows else None,
                candidateRows=denom["candidate_ticker_sessions"],
                missingRows=denom["missing_input_observations"],
            )
            for key, metric in model["baselines"].items():
                p.comparisons.append(_comparison(evaluation, key, _metrics(metric)))
    require(
        sum(v["evaluatedRows"] for v in p.evaluations if v["stage"] == "R1")
        == report["readout_prediction_records"],
        "r1_prediction_evidence_total",
    )


def _r2(p: _Projection, root: Path, report: dict[str, Any]) -> None:
    result = report["result"]
    count = result["counters"]
    run = _run(
        "R2",
        report["feedback_id"],
        "failure_feedback",
        horizons=[5],
        fits=count["base_fits"] + count.get("recovered_base_fits", 0),
        fitsRecovered=count.get("recovered_base_fits", 0),
        calibrationFits=count["calibration_fits"] + count.get("recovered_calibration_fits", 0),
        calibrationFitsRecovered=count.get("recovered_calibration_fits", 0),
        exactCache=count["exact_in_run_reuse"],
        evaluations=len(result["cases"]),
        endedAt=_record(root, "failure_feedback", report["feedback_id"]),
        resources=dict(
            wallSeconds=report["resource_audit"]["wall_seconds"],
            peakRssKiB=report["resource_audit"]["maximum_rss_kb"],
        ),
        **_ranges(report["preregistration"]["ranges"]),
    )
    p.runs.append(run)
    for origin in result["origins"]:
        failures = origin["failure_rows"]
        dates = sorted(row["input_date"] for row in failures)
        p.feedback.append(
            dict(
                feedbackId=report["feedback_id"],
                originExperimentId=origin["experiment_id"],
                originModelId=origin["model_id"],
                cohort=origin["cohort"],
                failureRows=len(failures),
                tickers=sorted({row["ticker"] for row in failures}),
                start=dates[0] if dates else None,
                end=dates[-1] if dates else None,
                metrics=_metrics(origin["metrics"]),
                followupExperimentIds=sorted(
                    {
                        m["experiment_id"]
                        for c in result["cases"]
                        if c["cohort"] == origin["cohort"]
                        for m in c["models"].values()
                    }
                ),
                hypotheses=result["decisions"],
            )
        )
    for case in result["cases"]:
        calibrator = report["calibrators"][case["calibrator_id"]]
        for role, model in (case["models"] | {"calibrated": case["models"]["original"]}).items():
            details = case["calibration"] if role in ("original", "calibrated") else case["mask"]
            p.experiment(run, model, 5)
            summary = dict(metrics=details["measures"][role], evidence=details["evidence"])
            view = p.evaluate(
                run,
                model,
                5,
                case["rows"],
                summary,
                role=role,
                exposure="exposed_fixed",
                score=role,
                tag=case["cohort"],
                status=details["verdict"],
                baselines={
                    key: metrics for key, metrics in details["measures"].items() if key != role
                },
                transform=case["calibrator_id"] if role == "calibrated" else None,
            )
            excluded = case["exclusions"]
            view.update(
                candidateRows=len(case["rows"]) + sum(excluded.values()),
                excludedRows=excluded["excluded"],
                neutralRows=excluded["neutral"],
            )
            if role in ("original", "calibrated"):
                p.experiments[run["runId"], model["experiment_id"]]["trainingPrior"] = calibrator[
                    "training_prior"
                ]
            for key, metric in details["measures"].items():
                if key != role:
                    p.comparisons.append(
                        _comparison(
                            view,
                            key,
                            _metrics(metric),
                            status=details["verdict"],
                            experiment_id=case["models"].get(key, {}).get("experiment_id"),
                            paired=[(r["correct"][key], r["correct"][role]) for r in case["rows"]],
                        )
                    )


def _resource(audit: dict[str, Any]) -> dict[str, Any]:
    return dict(wallSeconds=audit["wall_seconds"], peakRssKiB=audit["maximum_rss_kb"])


def _r3(p: _Projection, root: Path, report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = report["result"]
    counts = result["counters"]
    run = _run(
        "R3",
        report["result_id"],
        "predictive_model_research",
        fits=counts["new_fits"],
        evaluations=counts["completed"],
        exactCache=counts["exact_r2_reuse"],
        failures=counts["failed"],
        endedAt=_record(root, "predictive_research_result", report["result_id"]),
        resources=_resource(report["resource_audit"]),
        evaluationTickers=len(report["plan"]["development_tickers"]),
        **_ranges(report["plan"]["windows"]),
    )
    p.runs.append(run)
    trials = {t["experiment_id"]: t for t in report["trials"]}
    rows = report["development_predictions"]
    for h in (1, 5, 20):
        control = predictive_research._control(list(trials.values()), h)
        p.controls[h] = (control, rows[control["experiment_id"]]["rows"])
    for key, trial in trials.items():
        h = trial["config"]["goal"]["horizon"]
        if key in rows:
            p.dev(run, trial, h, rows[key]["rows"])
        else:
            p.experiment(run, trial, h)
    p.fixed(run, result["evaluations"], trials)
    return trials


def _r4(p: _Projection, root: Path, report: dict[str, Any], trials: dict[str, Any]) -> None:
    result = report["result"]
    owners = result["unique_trials"]
    arms = report["shortlist"]["arms"]
    arm_rows = [
        dict(
            horizon=int(h),
            arm=name,
            selections=a["candidate_evaluations"],
            actualFits=a["actual_fits"],
            cacheUses=a["cache_uses"],
            selectedExperimentId=a["selected"]["experiment_id"] if a["selected"] else None,
            reason=a["reason"],
        )
        for h, group in arms.items()
        for name, a in group.items()
    ]
    run = _run(
        "R4",
        report["result_id"],
        "autonomous_strategy",
        horizons=[1, 5],
        fits=sum(t["state"] == "completed" and t["cache_source"] is None for t in owners),
        failures=sum(t["state"] != "completed" for t in owners),
        evaluations=len(owners),
        exactCache=sum(
            step["cache"] and not step.get("recovered_plan")
            for group in arms.values()
            for a in group.values()
            for step in a["steps"]
        ),
        fitsRecovered=len(
            {
                step["experiment_id"]
                for group in arms.values()
                for a in group.values()
                for step in a["steps"]
                if step.get("recovered_plan")
            }
        ),
        qualifyingImprovements=len(
            {
                eid
                for group in arms.values()
                for a in group.values()
                for eid, check in a.get("stability", {}).items()
                if check["passed"]
            }
        ),
        strategyArms=arm_rows,
        gemmaStatus=report["gemma"]["status"],
        gemmaReason=report["gemma"]["reason"],
        endedAt=_record(root, "autonomous_strategy_result", report["result_id"]),
        resources=_resource(report["resource_audit"]),
    )
    rows = report["development_predictions"]
    p.runs.append(run)
    for trial in owners:
        eid = trial["experiment_id"]
        h = trial["config"]["goal"]["horizon"]
        checks = next(
            (a["stability"][eid] for a in arms[str(h)].values() if eid in a.get("stability", {})),
            None,
        )
        if eid in rows:
            p.dev(run, trial, h, rows[eid]["rows"], stability=checks)
        else:
            p.experiment(run, trial, h)
    for h, group in arms.items():
        for arm in group.values():
            for step in arm["steps"]:
                key = step["decision_id"]
                entry = report["decisions"][key]
                event = dict(
                    decisionId=key,
                    runId=run["runId"],
                    stage="R4",
                    horizon=int(h),
                    order=entry["round"],
                    arm=entry["arm"],
                    candidateId=entry["candidate"]["experiment_id"],
                    parentExperimentId=entry["parent_experiment"],
                    experimentId=step["experiment_id"],
                    feedbackId=entry["r2_feedback_id"],
                    hypothesis=entry["hypothesis"],
                    sourceExperimentIds=entry["rationale"]["referenced"],
                    withoutLatestCandidateId=entry["rationale"]["counterfactual"],
                    reason=arm["reason"],
                )
                if event not in p.decisions:
                    p.decisions.append(event)
    run["retainedLegacy"] = sum(
        arm.get("reason", "").startswith("ABSTAIN")
        for group in arms.values()
        for arm in group.values()
    )
    panels = [panel for f in result["fixed_evaluation"] for panel in f["comparisons"]]
    p.fixed(run, panels, trials | {t["experiment_id"]: t for t in owners})


def _r6(p: _Projection, source: dict[str, Any], role: str) -> None:
    fields(source, "report plan_pin_file manifest_sha256")
    path = Path(source["report"])
    root = path.parent
    manifest = root / "execution-bundle/manifest.json"
    require(_hash(manifest) == sha(source["manifest_sha256"]), "r6_bundle_pin")
    proof = json.loads(_file(manifest))
    report_pin = (
        proof["reports"][path.name] if "reports" in proof else proof["execution_report_sha256"]
    )
    require(_hash(path) == report_pin, "r6_report_pin")
    report = FrozenJSON(_file(path).decode()).to_dict()
    pin = read_pin(Path(source["plan_pin_file"]))
    plan = report["plan"]
    require(report["plan_id"] == pin == digest(plan), "r6_plan_pin")
    require(_hash(root / DB) == proof["database_sha256"], "r6_store_pin")
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        require(store.get("supervised_plan", pin) == plan, "r6_original_plan")
        historical = report["historical"]
        for key, value in store.all("supervised_window"):
            require(historical["windows"][key] == value, "r6_window_binding")
        source_trials = {t["experiment_id"]: t for _, t in store.all("predictive_research_trial")}
        for item in historical["results"].values():
            trial = item["trial"]
            base = source_trials[trial["experiment_id"]]
            require(
                all(trial[k] == v for k, v in base.items())
                and historical["predictions"][trial["experiment_id"]]
                == store.get("predictive_research_rows", base["rows_id"]),
                "r6_trial_rows_binding",
            )
    checkpoint_bytes: dict[str, int] = {}
    for key, checkpoint in historical["checkpoints"].items():
        f = root / checkpoint["checkpoint"]
        require(
            f.parent == root / "models"
            and _hash(f)
            == checkpoint["model_id"]
            == proof["checkpoints"][checkpoint["checkpoint"]],
            "r6_model_binding",
        )
        checkpoint_bytes[key] = f.stat().st_size
    for window, start in historical["windows"].items():
        close = historical["closures"].get(window)
        attempts = {k for k, v in historical["attempts"].items() if v["window"] == window}
        reads = [
            (key, value)
            for key, value in historical["results"].items()
            if value["attempt"] in attempts
        ]
        pauses = [v for v in historical["checkpoint_pauses"].values() if v["attempt"] in attempts]
        fitted = {k for k, v in reads if not v["exact_checkpoint_reuse"]} | {
            v["decision"] for v in pauses
        }
        audits = [v for k, v in historical["audits"].items() if k in attempts]
        completed = [v["trial"] for _, v in reads if v["trial"]["state"] == "completed"]
        run = _run(
            "R6",
            window,
            role,
            status="RUNNING"
            if close is None
            else ("COMPLETED" if close["reason"] == "requested_steps_complete" else "PARTIAL"),
            horizons=sorted(
                {historical["decisions"][k]["horizon"] for k, v in reads}
                | {historical["decisions"][k]["horizon"] for k in fitted}
            ),
            startedAt=start["started_at"],
            endedAt=close["ended_at"] if close else None,
            stopReason=close["reason"] if close else None,
            fits=len(fitted),
            evaluations=len(completed),
            exactCache=sum(v["exact_checkpoint_reuse"] for _, v in reads),
            failures=sum(bool(v["reason"] or v["returncode"]) for v in audits),
            qualifyingImprovements=sum(
                bool(v.get("stability", {}).get("passed")) for _, v in reads
            ),
            retainedLegacy=sum(not v.get("stability", {}).get("passed") for _, v in reads),
            evaluationTickers=max(
                (
                    len(
                        {
                            row["ticker"]
                            for row in historical["predictions"][t["experiment_id"]]["rows"]
                        }
                    )
                    for t in completed
                ),
                default=None,
            ),
            trainingTickers=max((t["training_counts"]["tickers"] for t in completed), default=None),
            resources=dict(
                wallSeconds=close.get("elapsed_seconds") if close else None,
                cpuSeconds=sum(v["cpu_seconds"] for _, v in reads)
                + sum(v["cpu_seconds"] for v in pauses),
                peakRssKiB=max(
                    (v["maximum_rss_kb"] for v in audits if v["maximum_rss_kb"] is not None),
                    default=None,
                ),
                checkpointBytes=sum(checkpoint_bytes[k] for k in fitted if k in checkpoint_bytes),
                storageBytes=historical["size_bytes"] if role == "approved" else None,
                remainingSeconds=historical["remaining_seconds"] if role == "approved" else None,
            ),
            **_ranges(plan["windows"]),
        )
        p.runs.append(run)
        for key, applied in reads:
            d = historical["decisions"][key]
            trial = applied["trial"]
            h = d["horizon"]
            if trial["state"] == "completed":
                p.dev(
                    run,
                    trial,
                    h,
                    historical["predictions"][trial["experiment_id"]]["rows"],
                    decision=d,
                    stability=applied["stability"],
                )
            else:
                p.experiment(run, trial, h, choice=d)
            p.decisions.append(
                dict(
                    decisionId=key,
                    runId=window,
                    stage="R6",
                    horizon=h,
                    order=d["index"],
                    candidateId=d["candidate"]["experiment_id"],
                    parentExperimentId=d["parent"],
                    experimentId=trial["experiment_id"],
                    feedbackId=d["r2_feedback_id"],
                    sourceExperimentIds=sorted(
                        set(d["r1_origin_ids"] + d["rationale"]["referenced"])
                    ),
                    withoutLatestCandidateId=d["without_latest"],
                    changedFromLatest=d["latest_changed_choice"]
                    if d["previous_result"] is not None
                    else None,
                    result="ABSTAIN"
                    if applied["stability"] and not applied["stability"]["passed"]
                    else trial["state"],
                )
            )


def _r5(ref: dict[str, Any], latest_date: str) -> dict[str, Any]:
    from donghak_stock_vision.models.direction_evaluation import publication
    from donghak_stock_vision.storage.direction_shadow import verify

    fields(
        ref,
        "request request_pin_file journal head_pin_file audit audit_pin_file "
        "readback readback_pin_file inventory inventory_pin_file legacy_readiness",
    )
    request = _pinned(ref["request"], ref["request_pin_file"])
    pinned, events = verify(request, Path(ref["journal"]), read_pin(Path(ref["head_pin_file"])))
    registration = pinned.to_dict()
    audit = _pinned(ref["audit"], ref["audit_pin_file"])
    readback = _pinned(ref["readback"], ref["readback_pin_file"])
    inventory = _pinned(ref["inventory"], ref["inventory_pin_file"])
    release = publication(registration, events)
    require(
        audit["registration_id"] == readback["registration_id"] == pinned.identifier
        and readback["latest"] == request["input_trading_date"]
        and len(release["predictions"]) == len(audit["forecasts"]),
        "research_prospective_binding",
    )
    legacy = ref["legacy_readiness"]
    complete = sum(
        _pinned(item["report"], item["pin_file"])["verified_outcome_windows"] for item in legacy
    )
    history = [
        count
        for prior in inventory["previous_journals"]
        for count in prior["counts"]
        if count["mode"] == "historical_replay"
    ]
    current = request["input_trading_date"] == latest_date
    return dict(
        checkedAt=readback["checked_at"],
        inputDate=request["input_trading_date"],
        inputCurrentAtSnapshot=current,
        status=readback["r5_status"] if current else "STALE_INPUT_SNAPSHOT",
        observations=min(readback["registered_ticker_observations"].values()),
        requiredObservations=readback["minimum_feature_observations"],
        legacyPending=sum(len(group["active"]) for group in readback["current_legacy"]),
        legacyPriceWindowsComplete=complete,
        historicalReplay=sum(c["n"] for c in history),
        historicalReplayEvaluated=sum(c["n"] for c in history if c["state"] == "evaluated"),
        models=[
            dict(
                experimentId=model["modelId"],
                modelId=model["modelSha256"],
                horizon=model["horizon"],
                descriptiveOnly=registration["models"][str(model["horizon"])]["lineage"][
                    "descriptive_only"
                ],
                recorded=model["total"],
                pending=model["pending"],
                evaluated=model["evaluated"],
                qualifiedAnchors=model["qualifiedAnchors"],
                missingAnchors=model["missingAnchors"],
                baseDate=readback["horizons"][str(model["horizon"])]["base_session"],
                targetDate=readback["horizons"][str(model["horizon"])]["target_session"],
                cutoff=readback["horizons"][str(model["horizon"])]["cutoff"],
                blockers=readback["horizons"][str(model["horizon"])]["blocked_by"],
            )
            for model in release["models"]
            if model["mode"] == "research_forward_candidate"
        ],
    )


def _operations(ref: dict[str, Any], prospect: dict[str, Any], at: datetime) -> dict[str, Any]:
    from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
    from donghak_stock_vision.storage.forward_capture import _recurring_state
    from donghak_stock_vision.storage.krx_scheduler import _policy, control, load_config
    from donghak_stock_vision.storage.operational_capture import verify_stored_capture
    from donghak_stock_vision.storage.operational_market import OperationalMarketStore
    from donghak_stock_vision.storage.shadow_prediction import _calendar

    fields(
        ref,
        "receipt receipt_pin_file scheduler scheduler_pin recovery_scheduler "
        "recovery_scheduler_pin_file recovery_plan recovery_plan_pin_file "
        "capture_attempt capture_pin_file batch_pin_file launchagent",
    )
    receipt = _pinned(ref["receipt"], ref["receipt_pin_file"])
    plan_body = _pinned(ref["recovery_plan"], ref["recovery_plan_pin_file"])
    cfg = load_config(Path(ref["scheduler"]), sha(ref["scheduler_pin"]))
    renewed = load_config(
        Path(ref["recovery_scheduler"]), read_pin(Path(ref["recovery_scheduler_pin_file"]))
    )
    capture_pin = read_pin(Path(ref["capture_pin_file"]))
    batch_pin = read_pin(Path(ref["batch_pin_file"]))
    require(
        receipt["day"] == plan_body["day"]
        and receipt["capture_pin"] == capture_pin
        and receipt["batch_pin"] == batch_pin
        and cfg.path("operational_root") == renewed.path("operational_root"),
        "research_operational_binding",
    )
    plan = ForwardCapturePlan.from_dict(plan_body)
    capture = verify_stored_capture(
        renewed.path("operational_root"),
        renewed.path("capture_root"),
        plan,
        read_pin(Path(ref["recovery_plan_pin_file"])),
        ref["capture_attempt"],
        capture_pin,
        batch_pin,
        authorization=renewed.reference,
        authorization_policy="operator_full_market_recurring_v1",
    ).to_dict()
    require(capture["batch_id"] == receipt["batch_id"], "research_krx_batch_binding")
    operational = cfg.path("operational_root")
    policy = _policy(cfg)
    quota = _recurring_state(cfg.path("capture_root"), plan, at)
    now_seoul = at.astimezone(SEOUL)
    policy_valid = (
        datetime.fromisoformat(policy["valid_from"])
        <= at
        < datetime.fromisoformat(policy["valid_until"])
    )
    calendar, _ = _calendar(
        _pinned(prospect["request"], prospect["request_pin_file"]), maximum_months=6
    )
    with closing(OperationalMarketStore(operational)) as store:
        store.connection.execute("PRAGMA query_only=ON")
        store.connection.execute("BEGIN")
        dates = {
            d
            for (d,) in store.connection.execute(
                "SELECT DISTINCT trading_date FROM market_observations WHERE market='KOSPI'"
            )
        }
        total, tickers = store.connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT ticker) FROM market_observations WHERE market='KOSPI'"
        ).fetchone()
        duplicates = store.connection.execute(
            "SELECT COUNT(*) FROM (SELECT ticker,trading_date FROM market_observations "
            "WHERE market='KOSPI' GROUP BY ticker,trading_date HAVING COUNT(*)>1)"
        ).fetchone()[0]
    agent = "unknown"
    label = ref["launchagent"]
    require(label == "com.donghakstockvision.krx-daily", "research_agent_identity")
    try:
        tool = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
        lines = {line.strip() for line in tool.stdout.splitlines()}
        if "state = not running" in lines:
            agent = "not_running"
        elif "state = running" in lines:
            agent = "running"
    except (OSError, subprocess.SubprocessError):
        pass
    blockers = [] if policy_valid else ["provider_permission_expired_or_not_yet_valid"]
    if now_seoul.date().isoformat() not in policy["full_market_scope"]["permitted_days"]:
        blockers.append("today_not_permitted_collection_day")
    shape = cfg.document.to_dict()
    earliest, latest = min(dates), max(dates)
    return dict(
        checkedAt=at.isoformat(),
        lastCaptureTradingDate=receipt["day"],
        latestTradingDate=latest,
        requestedAt=receipt["request_started_at"],
        httpStatus=receipt["http_status"],
        capturedRows=receipt["new_observations"],
        totalRows=total,
        sessions=len(dates),
        distinctTickers=tickers,
        duplicateRows=duplicates,
        calendarEnd=max(calendar),
        missingTradingDates=sorted(
            k
            for k, v in calendar.items()
            if v == "session" and earliest <= k < now_seoul.date().isoformat() and k not in dates
        ),
        scheduledTimeKst=f"{shape['hour']:02d}:{shape['minute']:02d}",
        agentState=agent,
        enabled=control(cfg)["enabled"],
        policyValid=policy_valid,
        blockers=blockers,
        localUsed=quota["locally_used_requests"],
        localRemaining=max(0, policy["daily_limit"] - quota["locally_used_requests"]),
    )


def visibility(request: dict[str, Any]) -> Visibility:
    fields(request, "version r1 r2 r3 r4 r5 r6_pilot r6_approved operations")
    require(
        request["version"] == "research_lab_visibility_request_v1", "research_visibility_request"
    )
    projection = _Projection()
    _r1(projection, request["r1"])
    _r2(projection, *_study(request["r2"], failure_feedback.report))
    trials = _r3(projection, *_study(request["r3"], predictive_research.report))
    root, report = _study(request["r4"], autonomous_strategy.report)
    _r4(projection, root, report, trials)
    _r6(projection, request["r6_pilot"], "pilot")
    _r6(projection, request["r6_approved"], "approved")
    at = datetime.now(UTC)
    operations = _operations(request["operations"], request["r5"], at)
    prospective = _r5(request["r5"], operations["latestTradingDate"])
    projection.runs.append(
        _run(
            "R5",
            digest(dict(registration=prospective["models"])),
            "prospective_registration",
            status=prospective["status"],
        )
    )
    raw = dict(
        version="research_lab_visibility_v1",
        publishedAt=at.isoformat(),
        runs=projection.runs,
        experiments=list(projection.experiments.values()),
        evaluations=projection.evaluations,
        predictions=projection.predictions,
        comparisons=projection.comparisons,
        decisions=projection.decisions,
        feedback=projection.feedback,
        prospective=prospective,
        operations=operations,
    )
    return Visibility.model_validate_json(json.dumps(raw, allow_nan=False))
