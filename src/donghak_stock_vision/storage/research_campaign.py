"""Finite campaign consumer of pinned Phase B sessions; no new learner or scheduler."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import research_campaign as policy
from donghak_stock_vision.models.memory_reasoning import ARMS
from donghak_stock_vision.storage import memory_guided_research as session
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, clock, context, terminals
from donghak_stock_vision.storage.autonomous_runner import _stop
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.research_resources import _command, lock

VERSION = "research_campaign_plan_v1"
GROUNDED_VERSION = "research_campaign_plan_v2"


def arms(d: dict[str, Any]) -> tuple[str, ...]:
    return session.arms(
        session.GROUNDED_VERSION if d["version"] == GROUNDED_VERSION else session.CAMPAIGN_VERSION
    )


def planned_cells(d: dict[str, Any]) -> int:
    return 6 * len(arms(d))


def prepare(d: dict[str, Any], root: Path, pins: Path) -> str:
    fields(
        d,
        "version studies horizons candidate_budget llm_budget wall_seconds step_seconds "
        "calibration_steps disk_mb model"
        + (" execution_steps" if d.get("version") == GROUNDED_VERSION else ""),
    )
    require(
        d["version"] in (VERSION, GROUNDED_VERSION)
        and type(d["candidate_budget"]) is int
        and 1 <= d["candidate_budget"] <= 8
        and d["horizons"] == [1, 5, 20]
        and all(type(h) is int for h in d["horizons"])
        and len(d["studies"]) == 2
        and type(d["llm_budget"]) is int
        and 1 <= d["llm_budget"] <= 96
        and type(d["wall_seconds"]) is int
        and 900 <= d["wall_seconds"] <= 14400
        and type(d["step_seconds"]) is int
        and 360 <= d["step_seconds"] <= 900
        and type(d["disk_mb"]) is int
        and 512 <= d["disk_mb"] <= 8192
        and type(d["calibration_steps"]) is int
        and 1 <= d["calibration_steps"] <= 18,
        "campaign_preregistered_bounds",
    )
    if d["version"] == GROUNDED_VERSION:
        require(
            type(d["execution_steps"]) is int
            and 1 <= d["execution_steps"] <= 30 * d["candidate_budget"],
            "campaign_execution_step_bound",
        )
    require(
        all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "campaign_new_outputs",
    )
    sources = []
    cohorts = set()
    for item in d["studies"]:
        fields(item, "source memory")
        ref = item["source"]
        fields(ref, "root plan_pin_file")
        p = Path(ref["root"])
        plan, data = context(p, read_pin(Path(ref["plan_pin_file"])), code_check=False)
        require(p.parent == root.parent and not pins.is_relative_to(p), "campaign_shared_parent")
        cohort = data["generalization"]["cohort"]
        require(cohort not in cohorts, "campaign_distinct_cohort_labels")
        cohorts.add(cohort)
        sources.append(
            dict(
                cohort=cohort,
                tickers=data["tickers"],
                first=data["first_date"],
                last=data["last_date"],
                dataset=plan["dataset_id"],
            )
        )
    root.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    # Freeze the complete request before any child preparation or execution.
    write_new(root / "preregistered-request.json", json.dumps(d))
    write_new(pins / "request.pin", digest(d))
    started_at = clock()
    cells = []
    for i, item in enumerate(d["studies"]):
        # At most thirteen original owners and one shared immutable dataset. Release at
        # the cohort boundary; no global/durable cache and no raw HTTP response bodies.
        verified: dict[str, Any] | None = {} if d["version"] == GROUNDED_VERSION else None
        for h in d["horizons"]:
            name = f"{root.name}-c{i + 1}-h{h}"
            child = root.parent / name
            childpins = pins / name
            cell_id = digest(
                dict(campaign_request=digest(d), cohort=sources[i]["cohort"], horizon=h)
            )
            req = dict(
                version=session.GROUNDED_VERSION
                if d["version"] == GROUNDED_VERSION
                else session.CAMPAIGN_VERSION,
                source=item["source"],
                memory=item["memory"],
                horizon=h,
                stock_policy="pooled",
                budget=d["candidate_budget"],
                model=d["model"],
            )
            try:
                pin = session.prepare(req, child, childpins, _verified=verified)
                frozen = session.load(child, pin)
                cells.append(
                    dict(
                        cell_id=cell_id,
                        cohort=sources[i]["cohort"],
                        horizon=h,
                        root=str(child),
                        pin=pin,
                        effective=frozen.get("effective_budget", d["candidate_budget"]),
                        status="READY",
                    )
                )
            except (ValueError, OSError, KeyError) as error:
                reason = str(error)
                import re

                reason = (
                    reason
                    if re.fullmatch("[a-z][a-z0-9_]{0,120}", reason)
                    else "campaign_source_unavailable"
                )
                cells.append(
                    dict(
                        cell_id=cell_id,
                        cohort=sources[i]["cohort"],
                        horizon=h,
                        root=str(child),
                        pin=None,
                        effective=0,
                        status="BLOCKED",
                        reason=reason,
                    )
                )
    shared = sorted(set(sources[0]["tickers"]) & set(sources[1]["tickers"]))
    frozen = dict(
        version=d["version"],
        request=d,
        cells=cells,
        schedule=policy.schedule(cells, d["candidate_budget"], arms(d)),
        registered_at=started_at,
        comparison_policy=policy.POLICY,
        cohort_overlap=dict(
            shared_tickers=len(shared),
            calendar_periods_overlap=not (
                sources[0]["last"] < sources[1]["first"] or sources[1]["last"] < sources[0]["first"]
            ),
            independent=False,
            reason="shared_market_dates_and_source_archive",
        ),
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    with closing(AnalysisStore(root / DB)) as store:
        store.put("research_campaign_plan", frozen)
    (root / DB).chmod(0o600)
    write_new(root / "campaign.json", json.dumps(frozen))
    write_new(pins / "campaign.pin", digest(frozen))
    return digest(frozen)


def load(root: Path, pin: str) -> dict[str, Any]:
    d = document(str(root / "campaign.json"), maximum=2 * 1024**2)
    require(
        digest(d) == pin
        and d["version"] in (VERSION, GROUNDED_VERSION)
        and d["comparison_policy"] == policy.POLICY,
        "campaign_plan_pin",
    )
    require(
        d["schedule"] == policy.schedule(d["cells"], d["request"]["candidate_budget"], arms(d)),
        "campaign_schedule_changed",
    )
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        require(store.get("research_campaign_plan", pin) == d, "campaign_plan_owner")
    return d


def start(root: Path, pin: str, *, approved: bool = False) -> dict[str, Any]:
    """Explicit local approval gate for v2 only; never authenticate an operator/clock."""
    d = load(root, pin)
    require(
        d["version"] == GROUNDED_VERSION and approved is True,
        "campaign_execution_approval_required",
    )
    with lock(root, ".campaign.lock"):
        with closing(AnalysisStore(root / DB)) as store:
            existing = store.all("research_campaign_execution_window")
            if existing:
                require(len(existing) == 1, "campaign_execution_window_conflict")
                return existing[0][1]
            value = dict(
                version="research_campaign_execution_window_v1",
                campaign_id=pin,
                started_at=clock(),
                max_wall_seconds=d["request"]["wall_seconds"],
                max_inferences=d["request"]["llm_budget"],
                max_steps=d["request"]["execution_steps"],
                authority="explicit_local_operator_action_not_independent_authentication",
            )
            store.put("research_campaign_execution_window", value)
            return value


def execution_start(root: Path, d: dict[str, Any]) -> datetime:
    if d["version"] == VERSION:
        return datetime.fromisoformat(d["registered_at"])
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        windows = store.all("research_campaign_execution_window")
    require(len(windows) == 1, "campaign_execution_approval_required")
    w = windows[0][1]
    fields(w, "version campaign_id started_at max_wall_seconds max_inferences max_steps authority")
    require(
        w["version"] == "research_campaign_execution_window_v1"
        and w["campaign_id"] == digest(d)
        and w["max_wall_seconds"] == d["request"]["wall_seconds"]
        and w["max_inferences"] == d["request"]["llm_budget"]
        and w["max_steps"] == d["request"]["execution_steps"]
        and w["authority"] == "explicit_local_operator_action_not_independent_authentication",
        "campaign_execution_window_binding",
    )
    began = datetime.fromisoformat(w["started_at"])
    require(
        began >= datetime.fromisoformat(d["registered_at"]), "campaign_execution_window_chronology"
    )
    return began


def counts(cell: dict[str, Any], arm: str) -> dict[str, Any]:
    if cell["status"] != "READY":
        return dict(done=0, attempts=0, rejected=0, completed=0, failed=0)
    p = session.arm_root(Path(cell["root"]), arm)
    with closing(ReadOnlyAnalysisStore(p / DB)) as store:
        done = terminals(store)
        props = [v for _, v in store.all("llm_proposal")]
        return dict(
            completed=sum(v["state"] == "completed" for v in done.values()),
            failed=sum(v["state"] == "failed" for v in done.values()),
            done=len(done),
            attempts=max(len(props), len(store.all("autonomous_selection"))),
            rejected=sum(p["state"] != "accepted" for p in props),
        )


def _spawn(root: Path, pin: str, attempt: str, fd: int, timeout: float) -> dict[str, Any]:
    env = dict(os.environ)
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "LOKY_MAX_CPU_COUNT",
    ):
        env[key] = "1"
    for key in (
        "KRX_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        env.pop(key, None)
    args = [
        sys.executable,
        "-m",
        "donghak_stock_vision",
        "memory-research",
        "campaign-worker",
        "--root",
        str(root),
        "--campaign-pin-file",
        str(root / "worker-reference.pin"),
        "--attempt-id",
        attempt,
    ]
    # Reference is the exact immutable campaign identity, never an overwritten head.
    ref = root / "worker-reference.pin"
    if not ref.exists():
        write_new(ref, pin)
    require(read_pin(ref) == pin, "campaign_worker_reference")
    logs = root / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    began = time.monotonic()
    peak = 0
    reason = None
    with (logs / (attempt + ".log")).open("xb") as log:
        os.chmod(log.name, 0o600)
        child = subprocess.Popen(
            args,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=(fd,),
        )
        try:
            while child.poll() is None:
                rss = _command(["/bin/ps", "-o", "rss=", "-p", str(child.pid)])
                if rss.isdigit():
                    peak = max(peak, int(rss))
                if time.monotonic() - began >= timeout:
                    reason = "campaign_step_timeout"
                    break
                if peak > 1024 * 1024:
                    reason = "campaign_parent_rss_limit"
                    break
                if rss == "UNKNOWN":
                    reason = "campaign_rss_unknown"
                    break
                time.sleep(0.25)
            if reason:
                _stop(child)
        except BaseException:
            _stop(child)
            raise
        code = child.wait(timeout=10)
    return dict(
        returncode=code, reason=reason, wall_seconds=time.monotonic() - began, peak_rss_kb=peak
    )


def worker(root: Path, pin: str, attempt_id: str) -> None:
    def stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt("campaign_budget_stop")

    signal.signal(signal.SIGTERM, stop)
    d = load(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        a = store.get("research_campaign_attempt", attempt_id)
    began = execution_start(root, d)
    require(
        began <= datetime.now(UTC) < began + timedelta(seconds=d["request"]["wall_seconds"]),
        "campaign_worker_window_closed",
    )
    require(a["step"] in d["schedule"], "campaign_worker_step_owner")
    cell = next(c for c in d["cells"] if c["cell_id"] == a["step"]["cell"])
    result = session.run(Path(cell["root"]), cell["pin"], a["step"]["arm"])
    with closing(AnalysisStore(root / DB)) as writer:
        writer.put(
            "research_campaign_step_report",
            dict(attempt_id=attempt_id, step=a["step"], at=clock(), report=result),
        )


def run(root: Path, pin: str, steps: int = 1) -> dict[str, Any]:
    require(type(steps) is int and 1 <= steps <= 240, "campaign_step_bound")
    d = load(root, pin)
    paused = None
    import shutil

    with lock(root, ".campaign.lock") as fd:
        began = execution_start(root, d)
        for _ in range(steps):
            with closing(ReadOnlyAnalysisStore(root / DB)) as store:
                attempts = store.all("research_campaign_attempt")
            _reconcile(root, d)
            if (
                d["version"] == GROUNDED_VERSION
                and len(attempts) >= d["request"]["execution_steps"]
            ):
                paused = "campaign_execution_step_budget_exhausted"
                break
            reserved = sum(a["step"]["arm"] != "rule" for _, a in attempts)
            spent = {a["step"]["step_id"] for _, a in attempts}
            candidate = None
            for s in d["schedule"]:
                if s["step_id"] in spent:
                    continue
                c = next(c for c in d["cells"] if c["cell_id"] == s["cell"])
                n = counts(c, s["arm"])
                if (
                    c["status"] != "READY"
                    or n["done"] >= c["effective"]
                    or n["attempts"] >= 2 * c["effective"]
                    or n["rejected"]
                ):
                    continue
                if s["arm"] != "rule" and reserved >= d["request"]["llm_budget"]:
                    continue
                candidate = (s, c)
                break
            if candidate is None:
                paused = "campaign_schedule_or_inference_budget_exhausted"
                break
            owned = (
                [root]
                + [Path(c["root"]) for c in d["cells"] if c["status"] == "READY"]
                + [
                    session.arm_root(Path(c["root"]), a)
                    for c in d["cells"]
                    if c["status"] == "READY"
                    for a in arms(d)
                ]
            )
            used = sum(p.stat().st_size for owner in owned for p in owner.rglob("*") if p.is_file())
            if used > d["request"]["disk_mb"] * 1024**2 or shutil.disk_usage(root).free < 1024**3:
                paused = "campaign_disk_budget"
                break
            now = datetime.now(UTC)
            registered = began
            remaining = (
                registered + timedelta(seconds=d["request"]["wall_seconds"]) - now
            ).total_seconds()
            require(now >= registered, "campaign_clock_regression")
            if attempts:
                require(
                    now >= max(datetime.fromisoformat(a["started_at"]) for _, a in attempts),
                    "campaign_clock_regression",
                )
            if remaining < min(1200, d["request"]["step_seconds"] + 300):
                paused = "campaign_wall_budget_exhausted"
                break
            paused = native_ready(root)
            if paused:
                break
            s, c = candidate
            a = dict(
                campaign=pin,
                step=s,
                started_at=clock(),
                reserved_inferences=int(s["arm"] != "rule"),
            )
            with closing(AnalysisStore(root / DB)) as store:
                key = store.put("research_campaign_attempt", a)
            outcome = _spawn(root, pin, key, fd, min(remaining - 300, d["request"]["step_seconds"]))
            with closing(AnalysisStore(root / DB)) as store:
                store.put(
                    "research_campaign_attempt_end", dict(attempt_id=key, at=clock(), **outcome)
                )
            if outcome["reason"] in (
                "campaign_step_timeout",
                "campaign_parent_rss_limit",
                "campaign_rss_unknown",
            ):
                paused = outcome["reason"]
                break
        with closing(AnalysisStore(root / DB)) as store:
            store.put("research_campaign_run", dict(at=clock(), pause_reason=paused))
    return status(root, pin)


def _report(root: Path, pin: str) -> dict[str, Any]:
    d = load(root, pin)
    cells = []
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        ends = [v for _, v in store.all("research_campaign_attempt_end")]
        attempts = [v for _, v in store.all("research_campaign_attempt")]
    for c in d["cells"]:
        if c["status"] != "READY":
            cells.append(
                dict(
                    cellId=c["cell_id"],
                    cohort=c["cohort"],
                    horizon=c["horizon"],
                    status="INSUFFICIENT_EVIDENCE",
                    reason=c["reason"],
                )
            )
            continue
        rows = []
        costs: dict[str, Any] = {}
        frozen = document(str(Path(c["root"]) / "session.json"), maximum=8 * 1024**2)
        require(digest(frozen) == c["pin"], "campaign_child_pin")
        for arm in arms(d):
            p = session.arm_root(Path(c["root"]), arm)
            with closing(ReadOnlyAnalysisStore(p / DB)) as source:
                plan = source.get("autonomous_plan", frozen["plan_id"])
                data = source.get("autonomous_dataset", plan["dataset_id"])
                outcomes = terminals(source)
                proposals = [v for _, v in source.all("llm_proposal")]
                requests = [v for _, v in source.all("llm_request")]
                audits = [v for _, v in source.all("autonomous_resource_audit")]
                llm = [v for v in proposals if v.get("metadata", {}).get("inference_calls", 0)]
                costs[arm] = dict(
                    inferenceCalls=len(llm),
                    promptBytes=sum(len(v["prompt"].encode()) for v in requests),
                    promptTokens=sum(v["metadata"]["timings"]["prompt_eval_count"] for v in llm)
                    if all(
                        "prompt_eval_count" in v.get("metadata", {}).get("timings", {}) for v in llm
                    )
                    else None,
                    outputTokens=sum(v["metadata"]["timings"]["eval_count"] for v in llm)
                    if all("eval_count" in v.get("metadata", {}).get("timings", {}) for v in llm)
                    else None,
                    inferenceSeconds=sum(v["wall_seconds"] for v in proposals),
                    workerSeconds=sum(v["wall_seconds"] for v in audits),
                    successfulFits=sum(
                        v["state"] == "completed" and "cache_source" not in v
                        for v in outcomes.values()
                    ),
                    workerAttempts=len(source.all("autonomous_attempt")),
                    cacheHits=sum("cache_source" in v for v in outcomes.values()),
                    peakWorkerRSS=max((v["maximum_rss_kb"] for v in audits), default=0),
                    peakOllamaRSS=max(
                        (
                            v["metadata"]["maximum_rss_kb"]
                            for v in llm
                            if type(v.get("metadata", {}).get("maximum_rss_kb")) in (int, float)
                        ),
                        default=None,
                    )
                    if llm
                    else 0,
                    peakOllamaCPU=max(
                        (
                            v["metadata"]["maximum_cpu_percent"]
                            for v in llm
                            if type(v.get("metadata", {}).get("maximum_cpu_percent"))
                            in (int, float)
                        ),
                        default=None,
                    )
                    if llm
                    else 0,
                )
                if d["version"] == GROUNDED_VERSION:

                    def total(key: str, observations: list[dict[str, Any]] = llm) -> float | None:
                        values = [v.get("metadata", {}).get(key) for v in observations]
                        return sum(values) if all(type(n) in (int, float) for n in values) else None

                    wall = sum(v["wall_seconds"] for v in proposals)
                    valid = sum(v["state"] == "accepted" for v in proposals)
                    evaluated = sum(t["state"] == "completed" for t in outcomes.values())
                    costs[arm].update(
                        promptCharacters=sum(len(v["prompt"]) for v in requests),
                        preparationSeconds=sum(v.get("preparation_seconds", 0) for v in proposals),
                        loadSeconds=total("load_seconds"),
                        modelInferenceSeconds=total("inference_seconds"),
                        unloadSeconds=total("unload_seconds"),
                        secondsPerValidProposal=wall / valid if valid else None,
                        secondsPerCompletedEvaluation=(wall + costs[arm]["workerSeconds"])
                        / evaluated
                        if evaluated
                        else None,
                    )
                for key, t in outcomes.items():
                    if t["state"] != "completed":
                        continue
                    r = source.get("autonomous_result", t["result_id"])
                    h = r["config"]["goal"]["horizon"]
                    require(r["dataset_id"] == plan["dataset_id"], "campaign_result_dataset")
                    defs = data["goals"][str(h)]["folds"]
                    folds = []
                    for f, spec in zip(r["folds"], defs, strict=True):
                        e = f["evaluation"]
                        folds.append(
                            dict(
                                period=spec["evaluation"],
                                metrics={
                                    k: e["metrics"][k]
                                    for k in (
                                        "pr_auc_ap",
                                        "balanced_accuracy",
                                        "brier_score",
                                        "roc_auc",
                                    )
                                },
                                counts=e["counts"],
                                anchors=len(e.get("nonoverlapping_calendar_dates", [])),
                                stability=policy.stability(e.get("seen_robustness")),
                            )
                        )
                    rows.append(
                        dict(
                            arm=arm,
                            experiment_id=key,
                            population=digest(dict(dataset=plan["dataset_id"], folds=defs)),
                            folds=folds,
                            mean_ap=r["mean_ap"],
                            worst_ba=r["worst_balanced_accuracy"],
                            baseline_ap_gain=sum(
                                f["metrics"]["pr_auc_ap"]
                                - max(
                                    b["pr_auc_ap"] for b in orig["evaluation"]["baselines"].values()
                                )
                                for f, orig in zip(folds, r["folds"], strict=True)
                            )
                            / len(folds),
                            family=r["config"]["candidate"]["family"],
                            mask=r["config"]["mask"],
                        )
                    )
        cell = dict(
            cellId=c["cell_id"],
            cohort=c["cohort"],
            horizon=c["horizon"],
            status="INSUFFICIENT_EVIDENCE",
            effectiveBudget=c["effective"],
            analysis=policy.analyze(rows, d["request"]["candidate_budget"], arms(d)),
            costs=costs,
        )
        detail = session.report(Path(c["root"]), c["pin"])
        cell["session"] = session.publication(detail)
        if d["version"] == GROUNDED_VERSION:
            for arm in arms(d):
                keys = {
                    digest(a)
                    for a in attempts
                    if a["step"]["cell"] == c["cell_id"] and a["step"]["arm"] == arm
                }
                durations = [e.get("wall_seconds") for e in ends if e["attempt_id"] in keys]
                tracked = len(durations) == len(keys) and all(
                    type(v) in (int, float) for v in durations
                )
                # Pre-existing/manual session results have no campaign wall-time proof.
                if not keys and (
                    detail["arms"][arm]["generated"] or detail["arms"][arm]["completed"]
                ):
                    tracked = False
                wall = sum(durations) if tracked else None
                legacy_passes = sum(
                    f["arm"] == arm and f["preregistered_internal_pass"] for f in detail["feedback"]
                )
                evaluated = detail["arms"][arm]["completed"]
                costs[arm].update(
                    controllerWallSeconds=wall,
                    legacyInternalPasses=legacy_passes,
                    secondsPerCompletedEvaluation=wall / evaluated
                    if wall is not None and evaluated
                    else None,
                    secondsPerLegacyInternalPass=wall / legacy_passes
                    if wall is not None and legacy_passes
                    else None,
                )
        cells.append(cell)
    progress = status(root, pin)
    consumed = sum(a["done"] for c in progress["cells"] for a in c["arms"].values())
    completed = sum(a["completed"] for c in progress["cells"] for a in c["arms"].values())
    missing = []
    for c in progress["cells"]:
        for arm, n in c["arms"].items():
            if n["done"] < d["request"]["candidate_budget"]:
                reason = (
                    "source_unavailable"
                    if c["status"] != "READY"
                    else "proposal_rejected"
                    if n["rejected"]
                    else "eligible_catalog_exhausted"
                    if n["done"] >= c["effective"]
                    else progress["lastPause"] or "not_yet_executed"
                )
                missing.append(
                    dict(
                        cellId=c["cellId"],
                        arm=arm,
                        missing=d["request"]["candidate_budget"] - n["done"],
                        reason=reason,
                    )
                )
    calibration = sorted(ends, key=lambda e: e["at"])[: d["request"]["calibration_steps"]]
    return dict(
        version="research_campaign_report_v3"
        if d["version"] == GROUNDED_VERSION
        else "research_campaign_report_v1",
        campaignId=pin,
        plannedCells=planned_cells(d),
        plannedSlots=planned_cells(d) * d["request"]["candidate_budget"],
        cells=cells,
        attempts=len(attempts),
        reservedInferences=sum(a["reserved_inferences"] for a in attempts),
        wallSeconds=sum(e.get("wall_seconds") or 0 for e in ends),
        peakParentRSS=max((e.get("peak_rss_kb", 0) for e in ends), default=0),
        calibration=dict(
            plannedSteps=d["request"]["calibration_steps"],
            completedSteps=len(calibration),
            estimatedSecondsPerStep=sum(e["wall_seconds"] for e in calibration) / len(calibration)
            if calibration and all(e.get("wall_seconds") is not None for e in calibration)
            else None,
        ),
        progress=dict(
            consumedSlots=consumed,
            successfulEvaluations=completed,
            failedEvaluations=consumed - completed,
            remainingSlots=planned_cells(d) * d["request"]["candidate_budget"] - consumed,
            lastPause=progress["lastPause"],
            unexecuted=missing,
        ),
        cohortOverlap=d["cohort_overlap"],
        independentGeneralization="GENERALIZATION_NOT_ESTABLISHED",
        uncertaintyInterval=None,
        pitQualified=False,
        performanceApproved=False,
        executionAuthorized=False,
    )


def status(root: Path, pin: str) -> dict[str, Any]:
    d = load(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        attempts = [v for _, v in store.all("research_campaign_attempt")]
        ends = [v for _, v in store.all("research_campaign_attempt_end")]
        runs = [v for _, v in store.all("research_campaign_run")]
    return dict(
        version="research_campaign_status_v1",
        campaignId=pin,
        plannedCells=planned_cells(d),
        plannedSlots=planned_cells(d) * d["request"]["candidate_budget"],
        attempts=len(attempts),
        reservedInferences=sum(a["reserved_inferences"] for a in attempts),
        recordedSeconds=sum(e.get("wall_seconds") or 0 for e in ends),
        cells=[
            dict(
                cellId=c["cell_id"],
                cohort=c["cohort"],
                horizon=c["horizon"],
                status=c["status"],
                effective=c["effective"],
                arms={a: counts(c, a) for a in arms(d)},
            )
            for c in d["cells"]
        ],
        lastPause=max(runs, key=lambda r: r["at"])["pause_reason"] if runs else None,
        performanceApproved=False,
        pitQualified=False,
        executionAuthorized=False,
    )


def publication(value: dict[str, Any]) -> dict[str, Any]:
    require(
        value["version"]
        in (
            "research_campaign_report_v1",
            "research_campaign_report_v2",
            "research_campaign_report_v3",
        ),
        "campaign_report_version",
    )
    version = (
        "research_campaign_publication_v2"
        if value["version"] == "research_campaign_report_v2"
        else "research_campaign_publication_v1"
    )
    if value["version"] == "research_campaign_report_v3":
        version = "research_campaign_publication_v3"
    return validate_publication(value | dict(version=version))


def validate_publication(value: dict[str, Any]) -> dict[str, Any]:
    import math
    import re

    if value.get("version") == "research_campaign_publication_v2":
        require("executionWindows" in value, "campaign_execution_windows_required")
        base = {k: v for k, v in value.items() if k != "executionWindows"}
        base["version"] = "research_campaign_publication_v1"
        require(
            combine_execution_windows(base, value["executionWindows"]) == value,
            "campaign_window_reconstruction",
        )
        return value

    grounded = value.get("version") == "research_campaign_publication_v3"
    arm_names = session.arms(session.GROUNDED_VERSION) if grounded else ARMS
    cell_count = 6 * len(arm_names)
    fields(
        value,
        "version campaignId plannedCells plannedSlots cells attempts reservedInferences "
        "wallSeconds "
        "peakParentRSS calibration progress cohortOverlap independentGeneralization "
        "uncertaintyInterval "
        "pitQualified performanceApproved executionAuthorized",
    )
    require(
        value["version"] in ("research_campaign_publication_v1", "research_campaign_publication_v3")
        and value["plannedCells"] == cell_count
        and type(value["plannedSlots"]) is int
        and 1 <= value["plannedSlots"] <= cell_count * 8
        and value["independentGeneralization"] == "GENERALIZATION_NOT_ESTABLISHED"
        and value["uncertaintyInterval"] is None
        and all(
            value[k] is False
            for k in ("pitQualified", "performanceApproved", "executionAuthorized")
        ),
        "campaign_publication_flags",
    )

    def identifier(s: Any) -> bool:
        return type(s) is str and re.fullmatch("[a-f0-9]{64}", s) is not None

    def number(n: Any) -> bool:
        return type(n) in (int, float) and math.isfinite(n) and n >= 0

    require(
        identifier(value["campaignId"])
        and type(value["cells"]) is list
        and len(value["cells"]) <= 6,
        "campaign_publication_bound",
    )
    require(
        all(
            number(value[k])
            for k in ("attempts", "reservedInferences", "wallSeconds", "peakParentRSS")
        ),
        "campaign_publication_cost",
    )
    fields(value["calibration"], "plannedSteps completedSteps estimatedSecondsPerStep")
    require(
        all(number(value["calibration"][k]) for k in ("plannedSteps", "completedSteps"))
        and (
            value["calibration"]["estimatedSecondsPerStep"] is None
            or number(value["calibration"]["estimatedSecondsPerStep"])
        ),
        "campaign_publication_calibration",
    )
    fields(
        value["progress"],
        "consumedSlots successfulEvaluations failedEvaluations remainingSlots lastPause unexecuted",
    )
    require(
        all(
            type(value["progress"][k]) is int and 0 <= value["progress"][k] <= cell_count * 8
            for k in (
                "consumedSlots",
                "successfulEvaluations",
                "failedEvaluations",
                "remainingSlots",
            )
        ),
        "campaign_publication_progress",
    )
    require(
        value["progress"]["consumedSlots"] + value["progress"]["remainingSlots"]
        == value["plannedSlots"],
        "campaign_publication_progress",
    )
    require(
        value["progress"]["lastPause"] is None
        or __import__("re").fullmatch("[a-z][a-z0-9_]{0,120}", value["progress"]["lastPause"])
        is not None,
        "campaign_publication_pause",
    )
    require(
        type(value["progress"]["unexecuted"]) is list
        and len(value["progress"]["unexecuted"]) <= cell_count,
        "campaign_publication_unexecuted",
    )
    for row in value["progress"]["unexecuted"]:
        fields(row, "cellId arm missing reason")
        require(
            identifier(row["cellId"])
            and row["arm"] in arm_names
            and type(row["missing"]) is int
            and 1 <= row["missing"] <= 8
            and re.fullmatch("[a-z][a-z0-9_]{0,120}", row["reason"]) is not None,
            "campaign_publication_unexecuted",
        )
    fields(value["cohortOverlap"], "shared_tickers calendar_periods_overlap independent reason")
    require(
        number(value["cohortOverlap"]["shared_tickers"])
        and type(value["cohortOverlap"]["calendar_periods_overlap"]) is bool
        and value["cohortOverlap"]["independent"] is False
        and value["cohortOverlap"]["reason"] == "shared_market_dates_and_source_archive",
        "campaign_publication_overlap",
    )
    seen = set()
    for c in value["cells"]:
        require(
            type(c) is dict
            and set(c)
            <= set(
                "cellId cohort horizon status reason effectiveBudget analysis session costs".split()
            )
            and {"cellId", "cohort", "horizon", "status"} <= set(c),
            "campaign_publication_cell",
        )
        require(
            identifier(c["cellId"])
            and c["cellId"] not in seen
            and type(c["cohort"]) is str
            and re.fullmatch("[A-Za-z0-9_.-]{1,100}", c["cohort"]) is not None
            and type(c["horizon"]) is int
            and c["horizon"] in (1, 5, 20)
            and c["status"] == "INSUFFICIENT_EVIDENCE",
            "campaign_publication_scope",
        )
        seen.add(c["cellId"])
        if "reason" in c:
            require(
                re.fullmatch("[a-z][a-z0-9_]{0,120}", c["reason"]) is not None,
                "campaign_publication_reason",
            )
            continue
        require(
            type(c["effectiveBudget"]) is int and 1 <= c["effectiveBudget"] <= 8,
            "campaign_publication_budget",
        )
        fields(c["costs"], " ".join(arm_names))
        for cost in c["costs"].values():
            fields(
                cost,
                "inferenceCalls promptBytes promptTokens outputTokens inferenceSeconds "
                "workerSeconds "
                "successfulFits workerAttempts cacheHits peakWorkerRSS peakOllamaRSS peakOllamaCPU"
                + (
                    " promptCharacters preparationSeconds loadSeconds modelInferenceSeconds "
                    "unloadSeconds secondsPerValidProposal secondsPerCompletedEvaluation "
                    "controllerWallSeconds legacyInternalPasses secondsPerLegacyInternalPass"
                    if grounded
                    else ""
                ),
            )
            require(
                all(v is None or number(v) for v in cost.values()), "campaign_publication_costs"
            )
        if "session" in c:
            session.validate_publication(c["session"])
            require(set(c["session"]["arms"]) == set(arm_names), "campaign_session_arms")
        a = c["analysis"]
        fields(a, "arms comparisons policy generalization")
        require(a["policy"] == policy.POLICY, "campaign_publication_policy")
        rows = []
        fields(a["arms"], " ".join(arm_names))
        for arm, group in a["arms"].items():
            fields(group, "evaluated planned ap ba brier baseline_ap_gain worst_ba models")
            require(
                type(group["models"]) is list
                and len(group["models"]) <= 8
                and group["evaluated"] == len(group["models"]),
                "campaign_publication_model_count",
            )
            for m in group["models"]:
                fields(m, "experimentId family mask meanAP baselineAPGain folds")
                require(
                    identifier(m["experimentId"])
                    and all(
                        type(m[k]) is str and re.fullmatch("[A-Za-z0-9_.-]{1,100}", m[k])
                        for k in ("family", "mask")
                    )
                    and number(m["meanAP"])
                    and type(m["baselineAPGain"]) in (int, float)
                    and math.isfinite(m["baselineAPGain"])
                    and type(m["folds"]) is list
                    and 1 <= len(m["folds"]) <= 12,
                    "campaign_publication_model",
                )
                for f in m["folds"]:
                    fields(f, "period metrics counts anchors stability")
                    require(
                        policy.stability(f["stability"]) == f["stability"],
                        "campaign_publication_stability",
                    )
                    fields(f["period"], "start end")
                    from datetime import date

                    require(
                        date.fromisoformat(f["period"]["start"])
                        <= date.fromisoformat(f["period"]["end"]),
                        "campaign_publication_period",
                    )
                    fields(f["metrics"], "pr_auc_ap balanced_accuracy brier_score roc_auc")
                    require(
                        all(v is None or number(v) and v <= 1 for v in f["metrics"].values()),
                        "campaign_publication_metrics",
                    )
                    fields(f["counts"], "rows dates positive negative tickers")
                    require(
                        all(type(v) is int and 0 <= v <= 10**7 for v in f["counts"].values())
                        and type(f["anchors"]) is int
                        and 0 <= f["anchors"] <= f["counts"]["dates"],
                        "campaign_publication_counts",
                    )
                rows.append(
                    dict(
                        arm=arm,
                        experiment_id=m["experimentId"],
                        population="approved_common_cell",
                        family=m["family"],
                        mask=m["mask"],
                        mean_ap=m["meanAP"],
                        baseline_ap_gain=m["baselineAPGain"],
                        worst_ba=min(f["metrics"]["balanced_accuracy"] for f in m["folds"]),
                        folds=m["folds"],
                    )
                )
        # All candidate folds use the same preregistered cell source/split. No CI is inferred.
        require(
            policy.analyze(rows, value["plannedSlots"] // cell_count, arm_names) == a,
            "campaign_publication_reconstruction",
        )
    return value


def _reconcile(root: Path, d: dict[str, Any]) -> None:
    with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
        attempts = reader.all("research_campaign_attempt")
        ends = {v["attempt_id"] for _, v in reader.all("research_campaign_attempt_end")}
    # Inherited campaign lock excludes a surviving child after parent interruption.
    for key, a in attempts:
        if key not in ends:
            cell = next(c for c in d["cells"] if c["cell_id"] == a["step"]["cell"])
            target = session.arm_root(Path(cell["root"]), a["step"]["arm"])
            with lock(target, ".worker.lock"):
                pass
            session.feedback(
                Path(cell["root"]),
                session.load(Path(cell["root"]), cell["pin"]),
                a["step"]["arm"],
            )
            with closing(AnalysisStore(root / DB)) as sink:
                sink.put(
                    "research_campaign_attempt_end",
                    dict(
                        attempt_id=key,
                        at=clock(),
                        reason="interrupted_reconciled",
                        wall_seconds=None,
                    ),
                )


def recover(root: Path, pin: str) -> dict[str, Any]:
    d = load(root, pin)
    with lock(root, ".campaign.lock"):
        _reconcile(root, d)
    return status(root, pin)


def report(root: Path, pin: str) -> dict[str, Any]:
    with lock(root, ".campaign.lock"):
        return _report(root, pin)


def native_ready(root: Path) -> str | None:
    import httpx

    from donghak_stock_vision.storage.gemma_research import Ollama, process_resources
    from donghak_stock_vision.storage.research_resources import probe, resource_reason

    reason = resource_reason(root, probe())
    if reason:
        return reason
    backend = Ollama()
    try:
        if not backend.idle():
            return "shared_ollama_busy"
        state = process_resources()
        return (
            None
            if state["verified"] and state["rss_kb"] < 256 * 1024
            else "shared_ollama_release_unverified"
        )
    except (ValueError, OSError, httpx.TransportError):
        return "shared_ollama_unavailable"
    finally:
        backend.close()


def combine_execution_windows(
    source: dict[str, Any], windows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Read-only projection of verified source results and separately approved windows.

    The caller retains independently pinned source exports and window audits. This
    projection verifies local consistency, not operator or chronology authenticity.
    It never executes, changes budgets, or rewrites a source plan.
    """
    import copy
    import math
    import re

    require(type(windows) is list and 1 <= len(windows) <= 8, "campaign_window_bound")
    result = copy.deepcopy(source)
    require(
        result["version"] in ("research_campaign_report_v1", "research_campaign_publication_v1"),
        "campaign_window_source_version",
    )
    seen = set()
    for w in windows:
        fields(
            w,
            "windowId sourceCampaignId maxWallSeconds maxSteps maxInferences attempts "
            "reservedInferences evaluations failedEvaluations newFits cacheHits rejections "
            "inferenceCalls recordedStepSeconds peakParentRSS status independentChronology "
            "frozenDesignUnchanged",
        )
        require(
            type(w["windowId"]) is str
            and re.fullmatch("[a-f0-9]{64}", w["windowId"]) is not None
            and w["windowId"] not in seen
            and w["sourceCampaignId"] == result["campaignId"]
            and w["independentChronology"] is False
            and w["frozenDesignUnchanged"] is True,
            "campaign_window_binding",
        )
        seen.add(w["windowId"])
        for k in (
            "maxWallSeconds maxSteps maxInferences attempts reservedInferences evaluations "
            "failedEvaluations newFits cacheHits rejections inferenceCalls peakParentRSS"
        ).split():
            require(type(w[k]) is int and w[k] >= 0, "campaign_window_integer")
        require(
            900 <= w["maxWallSeconds"] <= 14400
            and 1 <= w["maxSteps"] <= 144
            and w["attempts"] <= w["maxSteps"]
            and w["inferenceCalls"] <= w["reservedInferences"] <= w["maxInferences"] <= 96
            and w["reservedInferences"] <= w["attempts"]
            and w["newFits"] + w["cacheHits"] == w["evaluations"]
            and w["evaluations"] + w["failedEvaluations"] + w["rejections"] <= w["attempts"]
            and w["peakParentRSS"] <= 1024 * 1024
            and type(w["recordedStepSeconds"]) in (int, float)
            and math.isfinite(w["recordedStepSeconds"])
            and 0 <= w["recordedStepSeconds"] <= w["maxWallSeconds"]
            and type(w["status"]) is str
            and re.fullmatch("[a-z][a-z0-9_]{0,120}", w["status"]) is not None,
            "campaign_window_limits",
        )
    costs = [v for c in result["cells"] for v in c.get("costs", {}).values()]
    sessions = [v for c in result["cells"] for v in c.get("session", {}).get("arms", {}).values()]
    require(
        sum(w["evaluations"] for w in windows) == result["progress"]["successfulEvaluations"]
        and sum(w["failedEvaluations"] for w in windows) == result["progress"]["failedEvaluations"]
        and sum(w["newFits"] for w in windows) == sum(c["successfulFits"] for c in costs)
        and sum(w["cacheHits"] for w in windows) == sum(c["cacheHits"] for c in costs)
        and sum(w["inferenceCalls"] for w in windows) == sum(c["inferenceCalls"] for c in costs)
        and sum(w["rejections"] for w in windows) == sum(v["rejected"] for v in sessions),
        "campaign_window_source_counts",
    )
    result.update(
        attempts=sum(w["attempts"] for w in windows),
        reservedInferences=sum(w["reservedInferences"] for w in windows),
        wallSeconds=sum(w["recordedStepSeconds"] for w in windows),
        peakParentRSS=max(w["peakParentRSS"] for w in windows),
    )
    require(result["attempts"] <= result["plannedSlots"], "campaign_window_total_steps")
    result["progress"]["lastPause"] = "authorized_execution_windows_closed"
    for row in result["progress"]["unexecuted"]:
        if row["reason"] not in (
            "proposal_rejected",
            "eligible_catalog_exhausted",
            "source_unavailable",
        ):
            row["reason"] = "authorized_execution_windows_closed"
    original_version = result["version"]
    result["version"] = "research_campaign_publication_v1"
    validate_publication(result)
    result["version"] = (
        "research_campaign_report_v2"
        if original_version == "research_campaign_report_v1"
        else "research_campaign_publication_v2"
    )
    result["executionWindows"] = copy.deepcopy(windows)
    return result
