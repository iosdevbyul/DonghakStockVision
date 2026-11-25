"""One preregistered R2 run with immutable AnalysisStore feedback and fold artifacts."""

import hashlib
import os
import time
import uuid
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import failure_feedback as measurement
from donghak_stock_vision.models.autonomous import masked
from donghak_stock_vision.models.experiment import (
    Candidate,
    estimator_bytes,
    fit_candidate,
    predict_candidate,
)
from donghak_stock_vision.models.research_v2 import contract
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.signals.dataset import counts
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, LIMITS, clock, private_root
from donghak_stock_vision.storage.failure_feedback_source import (
    RANGES,
    outcomes,
    samples,
    source_owner,
)
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_training import _quiescent
from donghak_stock_vision.storage.research_resources import lock, probe, resource_reason
from donghak_stock_vision.storage.selection_review import retained_review_plan

REFERENCES = ("0749dbce9f90", "062781842aa3", "ef86f61be32b")
FLAGS = dict(
    pit_qualified=False,
    performance_approved=False,
    execution_authorized=False,
    independently_qualified=False,
    prospective=False,
)


def register(request: dict[str, Any], root: Path, pins: Path) -> str:
    fields(request, "version selection_root selection_plan_pin_file r1_report r1_report_pin_file")
    require(
        request["version"] == "prediction_failure_feedback_request_v1", "feedback_request_version"
    )
    selection = Path(request["selection_root"])
    require(
        all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and root.parent == selection.parent
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "feedback_private_output_scope",
    )
    retained = retained_review_plan(selection, read_pin(Path(request["selection_plan_pin_file"])))
    studies = [source_owner(s) for s in retained["studies"] if s["policy"] == "adaptive"]
    require(
        {s["cohort"] for s in studies} == {"historical-cohort1", "historical-cohort2"}
        and all(len(s["heldout_tickers"]) == 4 and len(s["tickers"]) == 20 for s in studies),
        "feedback_cohort_scope",
    )
    report = Path(request["r1_report"])
    require(
        report.is_absolute()
        and report.resolve() == report
        and report.is_file()
        and report.stat().st_mode & 0o077 == 0
        and report.stat().st_size <= 32 * 1024**2,
        "feedback_audit_file",
    )
    evidence = FrozenJSON(report.read_text())
    require(
        read_pin(Path(request["r1_report_pin_file"])) == evidence.identifier,
        "feedback_r1_audit_binding",
    )
    audit = evidence.to_dict()
    require(
        audit["version"] == "prediction_performance_audit_v1" and audit["status"] == "PARTIAL",
        "feedback_r1_audit_scope",
    )
    cases = [(c, c["models"]["original_group_selection"]) for c in audit["readout"]]
    origins = []
    for prefix, expected in zip(REFERENCES, (42, 42, 46), strict=True):
        matching = [
            (c, m)
            for c, m in cases
            if c["horizon"] == 5
            and c["stock_policy"] == "heldout"
            and c["policy"] == ("predeclared" if prefix == REFERENCES[-1] else "adaptive")
            and m["experiment_id"].startswith(prefix)
        ]
        require(len(matching) == 1, "feedback_origin_not_unique")
        case, model = matching[0]
        failures = [
            {
                k: r[k]
                for k in (
                    "sample_id",
                    "ticker",
                    "input_date",
                    "target_date",
                    "stock_regimes",
                    "actual_label",
                    "predicted_direction",
                    "prediction_score",
                )
            }
            for r in model["rows"]
            if r["outcome"] == "incorrect"
        ]
        require(
            sum(r["predicted_direction"] == "up" for r in failures) == expected,
            "feedback_original_false_positives",
        )
        origins.append(
            dict(
                cohort=case["cohort"],
                experiment_id=model["experiment_id"],
                model_id=model["model_id"],
                evaluation_id=model["result_id"],
                config=dict(candidate=model["candidate"], mask=model["mask"]),
                metrics=model["metrics"],
                failure_rows=failures,
            )
        )
    require(
        [o["cohort"] for o in origins]
        == ["historical-cohort1", "historical-cohort2", "historical-cohort2"]
        and origins[0]["config"]["candidate"]["family"] == "linear"
        and origins[0]["config"]["candidate"]["parameters"]["solver"] == "lbfgs"
        and origins[1]["config"]["candidate"] == origins[2]["config"]["candidate"]
        and origins[1]["config"]["candidate"]["family"] == "random_forest"
        and [o["config"]["mask"] for o in origins[1:]] == ["v2_daily", "v2_legacy"],
        "feedback_origin_controls",
    )
    hypothesis: dict[str, dict[str, Any]] = dict(
        calibration=dict(
            method="calibration_only_log_odds_intercept",
            epsilon=measurement.EPSILON,
            origins=[o["experiment_id"] for o in origins[:2]],
            criteria=measurement.CRITERIA["calibration"],
        ),
        mask=dict(
            method="controlled_random_forest_mask_only",
            controls=["v2_legacy", "v2_daily"],
            origins=[o["experiment_id"] for o in origins[1:]],
            criteria=measurement.CRITERIA["mask"],
        ),
    )
    plan = dict(
        version="prediction_failure_feedback_plan_v1",
        registered_at=clock(),
        request=request,
        r1_report_id=evidence.identifier,
        r1_readout_id=audit["readout_id"],
        origins=origins,
        sources=studies,
        hypotheses={k: v | dict(hypothesis_id=digest(v)) for k, v in hypothesis.items()},
        ranges=RANGES,
        label_contract=contract(5),
        seed=42,
        code=provenance(),
        limits=LIMITS,
        maximum_base_fits=5,
        maximum_calibration_fits=2,
        classification="previously_exposed_internal_development",
        **FLAGS,
    )
    root.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    fd = os.open(root / DB, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(root / DB)) as store:
        pin = store.put("failure_feedback_plan", plan)
    write_new(pins / "plan.pin", pin)
    return pin


def _plan(root: Path, pin: str) -> dict[str, Any]:
    private_root(root)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        plan = store.get("failure_feedback_plan", pin)
    require(
        plan["version"] == "prediction_failure_feedback_plan_v1"
        and plan["limits"] == LIMITS
        and plan["ranges"] == RANGES
        and plan["label_contract"] == contract(5)
        and all(plan[k] is False for k in FLAGS),
        "feedback_plan_binding",
    )
    return plan


def _models(
    root: Path,
    pin: str,
    study: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], float, dict[str, Any]]:
    cohort = study["cohort"]
    train = samples(study, "train")
    calibration = samples(study, "calibration")
    train_counts, cal_counts = counts(train), counts(calibration)
    require(
        train_counts["dates"] >= 252
        and train_counts["rows"] >= 1000
        and min(train_counts["positive"], train_counts["negative"]) >= 50
        and cal_counts["dates"] >= 40
        and cal_counts["rows"] >= 200
        and min(cal_counts["positive"], cal_counts["negative"]) >= 20,
        "feedback_fit_population",
    )
    prior = train_counts["positive"] / train_counts["rows"]
    base = next(o["config"] for o in plan["origins"][:2] if o["cohort"] == cohort)
    forest = plan["origins"][1]["config"]["candidate"]
    configs = dict(
        original=base,
        daily=dict(candidate=forest, mask="v2_daily"),
        legacy=dict(candidate=forest, mask="v2_legacy"),
    )
    directory = root / "models"
    directory.mkdir(mode=0o700, exist_ok=True)
    require(
        directory.resolve() == directory and directory.stat().st_mode & 0o077 == 0,
        "feedback_checkpoint_directory",
    )
    fitted: dict[str, Any] = {}
    descriptors: dict[str, Any] = {}
    exact: dict[str, str] = {}
    exact_train = digest([s.to_dict() for s in train])
    for arm, cfg in configs.items():
        key = digest(dict(plan_id=pin, cohort=cohort, config=cfg, training=exact_train))
        if key in exact:
            fitted[arm] = fitted[exact[key]]
            descriptors[arm] = descriptors[exact[key]] | dict(exact_in_run_reuse=True)
            continue
        started = time.monotonic()
        candidate = Candidate(**cfg["candidate"])
        model = fit_candidate(
            candidate, masked(train, cfg["mask"]), feature_names=plan["label_contract"]["features"]
        )
        binary = estimator_bytes(model)
        require(len(binary) <= LIMITS["model_maximum_mb"] * 1024**2, "feedback_checkpoint_bound")
        with (directory / (key + ".checkpoint")).open("xb") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(binary)
            out.flush()
            os.fsync(out.fileno())
        fitted[arm] = model
        exact[key] = arm
        descriptors[arm] = dict(
            experiment_id=key,
            model_id=hashlib.sha256(binary).hexdigest(),
            checkpoint="models/" + key + ".checkpoint",
            config=cfg,
            seed=plan["seed"],
            wall_seconds=time.monotonic() - started,
            exact_in_run_reuse=False,
        )
    cfg = configs["original"]
    start_cal = time.monotonic()
    cal_scores = predict_candidate(
        Candidate(**cfg["candidate"]), fitted["original"], masked(calibration, cfg["mask"])
    )
    labels = [int(s.label) for s in calibration if s.label is not None]
    offset = measurement.prior_offset(labels, cal_scores)
    calibration_rows = [
        dict(
            sample_id=digest(s.to_dict()),
            input_date=s.trading_date.isoformat(),
            ticker=s.ticker,
            label=s.label,
            original_score=score,
        )
        for s, score in zip(calibration, cal_scores, strict=True)
    ]
    cal_record = dict(
        training_population=exact_train,
        training_counts=train_counts,
        calibration_population=digest([s.to_dict() for s in calibration]),
        calibration_counts=cal_counts,
        training_prior=prior,
        calibration_prior=sum(labels) / len(labels),
        rows=calibration_rows,
        offset=offset,
        model_id=descriptors["original"]["model_id"],
        method="calibration_only_log_odds_intercept",
        wall_seconds=time.monotonic() - start_cal,
    )
    return fitted, descriptors, offset, cal_record


def worker(root: Path, pin: str, attempt: str) -> None:
    plan = _plan(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        require(
            store.get("failure_feedback_attempt", attempt)["plan_id"] == pin
            and not store.all("failure_feedback"),
            "feedback_attempt_binding",
        )
    require(plan["code"] == provenance(), "feedback_runtime_or_code_changed")
    started = time.monotonic()
    sources = [source_owner(s) for s in plan["sources"]]
    require(sources == plan["sources"], "feedback_source_changed")
    fits = [_models(root, pin, s, plan) for s in sources]
    # The worker has finished both calibration fits before selecting evaluation labels.
    with closing(AnalysisStore(root / DB)) as store:
        calibrators = store.put_many(
            [
                ("failure_feedback_calibrator", dict(plan_id=pin, cohort=s["cohort"]) | item[3])
                for s, item in zip(sources, fits, strict=True)
            ]
        )
        frozen_id = store.put(
            "failure_feedback_frozen_models",
            dict(
                plan_id=pin,
                models=[item[1] for item in fits],
                calibrator_ids=calibrators,
                frozen_at=clock(),
            ),
        )
    cases = []
    for study, (models, descriptors, offset, cal_record), cal_id in zip(
        sources, fits, calibrators, strict=True
    ):
        test, rows, excluded = outcomes(study)
        scores: dict[str, list[float]] = {}
        for arm, fitted in models.items():
            cfg = descriptors[arm]["config"]
            scores[arm] = (
                scores["original"]
                if descriptors[arm]["exact_in_run_reuse"]
                else predict_candidate(
                    Candidate(**cfg["candidate"]), fitted, masked(test, cfg["mask"])
                )
            )
        scores["calibrated"] = measurement.calibrated(scores["original"], offset)
        scores["training_prior"] = [cal_record["training_prior"]] * len(rows)
        threshold = plan["label_contract"]["threshold"]
        for i, r in enumerate(rows):
            r["scores"] = {k: value[i] for k, value in scores.items()}
            r["predictions"] = {k: int(value >= threshold) for k, value in r["scores"].items()}
            r["correct"] = {k: y == r["label"] for k, y in r["predictions"].items()}
        cases.append(
            dict(
                cohort=study["cohort"],
                evaluation_population=digest([s.to_dict() for s in test]),
                models=descriptors,
                calibrator_id=cal_id,
                calibration=measurement.compare(rows, "calibration"),
                mask=measurement.compare(rows, "mask"),
                rows=rows,
                excluded_rows=excluded,
                exclusions=dict(Counter(r["status"] for r in excluded)),
            )
        )
        _quiescent(Path(study["root"]) / DB, study["database_sha256"])
    decisions = {}
    for name in ("calibration", "mask"):
        verdicts = {c[name]["verdict"] for c in cases}
        decisions[name] = (
            "INSUFFICIENT_EVIDENCE"
            if "INSUFFICIENT_EVIDENCE" in verdicts
            else "REJECTED"
            if "REJECTED" in verdicts
            else "SUPPORTED"
        )
    with closing(AnalysisStore(root / DB)) as store:
        total = [m for c in cases for m in c["models"].values()]
        fits_completed = sum(not m["exact_in_run_reuse"] for m in total)
        store.put(
            "failure_feedback",
            dict(
                plan_id=pin,
                frozen_models_id=frozen_id,
                hypotheses=plan["hypotheses"],
                origins=plan["origins"],
                cases=cases,
                decisions=decisions,
                counters=dict(
                    base_fits=fits_completed,
                    calibration_fits=len(calibrators),
                    evaluation_inferences=fits_completed,
                    calibration_inferences=len(calibrators),
                    exact_in_run_reuse=len(total) - fits_completed,
                    historical_replay=0,
                    prior_score_reuse=0,
                    network_requests=0,
                ),
                worker_seconds=time.monotonic() - started,
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
        with closing(ReadOnlyAnalysisStore(root / DB)) as store:
            if store.all("failure_feedback"):
                reports = store.all("failure_feedback_resource_audit")
                require(
                    len(reports) == 1
                    and reports[0][1]["returncode"] == 0
                    and reports[0][1]["reason"] is None,
                    "feedback_result_resource_audit_unavailable",
                )
                return dict(state="COMPLETE", reused=True)
            require(not store.all("failure_feedback_attempt"), "feedback_attempt_already_consumed")
        reason = resource_reason(root, probe())
        resident = process_resources()
        if reason or not resident.get("verified") or resident["rss_kb"] >= 256 * 1024:
            return dict(state="BLOCKED", reason=reason or "ollama_release_resources_unverified")
        with closing(AnalysisStore(root / DB)) as store:
            attempt = store.put(
                "failure_feedback_attempt", dict(plan_id=pin, nonce=uuid.uuid4().hex)
            )
        audit = launch(root, pin, attempt, failure_feedback=True, reservation_fd=reservation)
        with closing(AnalysisStore(root / DB)) as store:
            store.put("failure_feedback_resource_audit", audit | dict(attempt=attempt))
        if audit["returncode"] != 0 or audit["reason"] is not None:
            return dict(state="BLOCKED", resource_audit=audit)
        return dict(state="COMPLETE", resource_audit=audit)


def report(root: Path, pin: str) -> FrozenJSON:
    plan = _plan(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        records = store.all("failure_feedback")
        require(len(records) == 1, "feedback_result_unavailable")
        feedback_id, result = records[0]
        require(result["plan_id"] == pin, "feedback_report_binding")
        frozen = store.get("failure_feedback_frozen_models", result["frozen_models_id"])
        calibrators = {
            key: store.get("failure_feedback_calibrator", key) for key in frozen["calibrator_ids"]
        }
        audits = [value for _, value in store.all("failure_feedback_resource_audit")]
    require(
        len(audits) == 1
        and audits[0]["returncode"] == 0
        and audits[0]["reason"] is None
        and frozen["plan_id"] == pin
        and all(v["plan_id"] == pin for v in calibrators.values()),
        "feedback_report_resource_binding",
    )
    for models in frozen["models"]:
        for entry in models.values():
            path = root / entry["checkpoint"]
            require(
                path.resolve() == path
                and path.is_file()
                and path.stat().st_size <= LIMITS["model_maximum_mb"] * 1024**2
                and hashlib.sha256(path.read_bytes()).hexdigest() == entry["model_id"],
                "feedback_report_checkpoint_binding",
            )
    return FrozenJSON.freeze(
        dict(
            feedback_id=feedback_id,
            result=result,
            preregistration=plan,
            frozen_models=frozen,
            calibrators=calibrators,
            resource_audit=audits[0],
        )
    )
