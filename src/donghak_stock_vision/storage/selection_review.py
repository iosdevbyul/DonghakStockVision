"""Read-only frozen-study selection review; no training or public API publication."""

import json
import statistics as st
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.models import selection_reliability as policy
from donghak_stock_vision.models.autonomous import masked
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.experiment import Candidate, predict_candidate
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, terminals
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.operational_market import DB as OP_DB
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.real_market_training import _quiescent
from donghak_stock_vision.storage.research_checkpoint import verified_estimator
from donghak_stock_vision.storage.research_studies import _file_sha256 as file_hash
from donghak_stock_vision.storage.selection_assessment_source import prepare as prepare_source
from donghak_stock_vision.storage.stock_research import report as original_report

REVIEW_DB = "selection.sqlite3"


def selection_view(e: dict[str, Any]) -> dict[str, Any]:
    return {
        k: e[k]
        for k in (
            "experiment_id",
            "config",
            "mean_seen_cv_ap",
            "overfit_gap",
            "robust_summary",
            "fold_seen_metrics",
            "fold_baselines",
        )
    } | dict(
        nonoverlapping=[dict(calendar_dates=n["calendar_dates"]) for n in e["nonoverlapping"]],
        fold_generalization=[
            dict(seen_symbols=dict(counts=f["seen_symbols"]["counts"]))
            for f in e["fold_generalization"]
        ],
    )


def register(request: dict[str, Any], root: Path, pins: Path) -> str:
    fields(request, "version studies assessment")
    require(
        request["version"] == "selection_review_request_v1" and len(request["studies"]) == 6,
        "selection_review_request",
    )
    require(
        all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "selection_separate_output",
    )
    cases = []
    studies = []
    diagnosis = []
    owners = set()
    for i, item in enumerate(request["studies"]):
        fields(item, "root plan_pin_file database_sha256 report report_sha256")
        parent = Path(item["root"])
        require(
            root.parent == parent.parent and not pins.is_relative_to(parent),
            "selection_output_owner",
        )
        _quiescent(parent / DB, item["database_sha256"])
        pin = read_pin(Path(item["plan_pin_file"]))
        plan, data = context(parent, pin, code_check=False)
        require(plan["version"] == "autonomous_plan_v6", "selection_study_version")
        require(file_hash(Path(item["report"])) == item["report_sha256"], "selection_report_pin")
        report = document(item["report"], maximum=32 * 1024**2)
        require(
            report == original_report(parent, pin)
            and report["locked"]
            and report["completed"] == 12
            and report["failed"] == 0,
            "selection_original_report_owner",
        )
        owner = (data["dataset_version"], plan["selection_policy"])
        require(owner not in owners, "selection_duplicate_study")
        owners.add(owner)
        original_request = document(str(parent / "request.json"))
        require(
            request["assessment"]["research"] == original_request["research"]
            and request["assessment"]["protocol"] == original_request["protocol"]
            and request["assessment"]["evaluation_window"]["start"]
            > data["historical"]["diagnostic"]["end"],
            "selection_assessment_must_be_new",
        )
        with closing(ReadOnlyAnalysisStore(parent / DB)) as store:
            done = terminals(store)
        studies.append(
            item
            | dict(
                plan_pin=pin,
                dataset_id=plan["dataset_id"],
                cohort=data["generalization"]["cohort"],
                policy=plan["selection_policy"],
                tickers=data["tickers"],
                heldout_tickers=data["generalization"]["heldout_tickers"],
                versions=plan["code"]["versions"],
            )
        )
        for h, g in report["horizons"].items():
            for stock in ("pooled", "heldout"):
                es = [e for e in g["experiments"] if e["config"]["stock_policy"] == stock]
                selected = policy.select([selection_view(e) for e in es])
                ids = {
                    selected[k]
                    for k in ("original_group_selection", "highest_search_ap", "stable_selection")
                    if selected[k] is not None
                }
                records = {k: done[k] for k in ids}
                with closing(ReadOnlyAnalysisStore(parent / DB)) as store:
                    prior_counts = [
                        store.get("autonomous_result", done[k]["result_id"])["training"]
                        for k in ids
                    ]
                require(
                    all(c == prior_counts[0] for c in prior_counts),
                    "selection_training_population_mismatch",
                )
                training_samples = [
                    Sample.from_dict(s)
                    for s in data["goals"][h]["samples"]
                    if s["label_end"] <= data["historical"]["search_end"]
                    and (
                        stock == "pooled"
                        or s["ticker"] not in set(data["generalization"]["heldout_tickers"])
                    )
                ]
                reference = dict(
                    mean=[st.mean(s.values[j] for s in training_samples) for j in range(46)],
                    scale=[st.pstdev(s.values[j] for s in training_samples) for j in range(46)],
                )
                cases.append(
                    dict(
                        study=i,
                        horizon=int(h),
                        stock_policy=stock,
                        selection=selected,
                        models=records,
                        training_prior=prior_counts[0]["positive"] / prior_counts[0]["rows"],
                        input_reference=reference,
                        recorded_report_selection=g["selected_without_heldout_scores"],
                    )
                )
                diagnosis.append(
                    dict(
                        study=i,
                        horizon=int(h),
                        stock_policy=stock,
                        selected=selected,
                        experiments=[
                            dict(
                                experiment_id=e["experiment_id"],
                                family=e["config"]["candidate"]["family"],
                                mask=e["config"]["mask"],
                                parameters=e["config"]["candidate"],
                                cv=policy.evidence(selection_view(e)),
                                fold_metrics=e["fold_seen_metrics"],
                                fold_baselines=e["fold_baselines"],
                                heldout=[f["heldout_symbols"] for f in e["fold_generalization"]],
                                periods=[f["periods"] for f in e["fold_generalization"]],
                                regimes=[f["heldout_regimes"] for f in e["fold_generalization"]],
                                nonoverlapping=e["nonoverlapping"],
                            )
                            for e in es
                        ],
                        q3_diagnostic_used=False,
                    )
                )
        _quiescent(parent / DB, item["database_sha256"])
    require(
        len({s["cohort"] for s in studies}) == 2
        and all(
            {s["policy"] for s in studies if s["cohort"] == c}
            == {"adaptive", "predeclared", "robust"}
            for c in {s["cohort"] for s in studies}
        ),
        "selection_policy_cohort_matrix",
    )
    for cohort in {s["cohort"] for s in studies}:
        members = [s for s in studies if s["cohort"] == cohort]
        require(
            len({s["dataset_id"] for s in members}) == 1
            and len({digest(s["heldout_tickers"]) for s in members}) == 1,
            "selection_same_budget_same_population",
        )
    plan = dict(
        version="selection_review_plan_v1",
        pins_root=str(pins),
        request=request,
        request_pin=digest(request),
        policy=policy.POLICY,
        policy_pin=digest(policy.POLICY),
        studies=studies,
        cases=cases,
        code=provenance(),
        prior_selections_verified=72,
        new_training_budget=0,
        q3_used_for_selection=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    root.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    with closing(AnalysisStore(root / REVIEW_DB)) as store:
        store.put_many(
            [
                ("selection_review_plan", plan),
                (
                    "selection_diagnosis",
                    dict(
                        version="selection_diagnosis_v1",
                        cases=diagnosis,
                        prior_selections=72,
                        q3_used=False,
                    ),
                ),
            ]
        )
    (root / REVIEW_DB).chmod(0o600)
    write_new(root / "preregistration.json", FrozenJSON.freeze(plan).payload_json)
    write_new(pins / "plan.pin", digest(plan))
    write_new(root / "diagnosis.json", json.dumps(diagnosis))
    return digest(plan)


def retained_review_plan(root: Path, pin: str) -> dict[str, Any]:
    require(
        root.is_absolute()
        and root.resolve() == root
        and root.stat().st_mode & 0o077 == 0
        and (root / REVIEW_DB).is_file(),
        "selection_private_root",
    )
    with closing(ReadOnlyAnalysisStore(root / REVIEW_DB)) as store:
        plan = store.get("selection_review_plan", pin)
    require(
        plan["version"] == "selection_review_plan_v1"
        and plan["policy"] == policy.POLICY
        and plan["policy_pin"] == digest(policy.POLICY)
        and all(
            plan[k] is False
            for k in ("pit_qualified", "performance_approved", "execution_authorized")
        ),
        "selection_plan_binding",
    )
    require(
        FrozenJSON((root / "preregistration.json").read_text()).identifier == pin,
        "selection_preregistration_changed",
    )
    for s in plan["studies"]:
        _quiescent(Path(s["root"]) / DB, s["database_sha256"])
        require(
            read_pin(Path(s["plan_pin_file"])) == s["plan_pin"]
            and file_hash(Path(s["report"])) == s["report_sha256"],
            "selection_retained_reference_changed",
        )
    return plan


def review_context(root: Path, pin: str, *, code_check: bool = True) -> dict[str, Any]:
    plan = retained_review_plan(root, pin)
    code = provenance()
    require(
        (not code_check or plan["code"]["implementation_hash"] == code["implementation_hash"])
        and plan["code"]["versions"] == code["versions"],
        "selection_code_or_runtime_changed",
    )
    a = plan["request"]["assessment"]
    _quiescent(Path(a["research"]["database"]), a["research"]["pin"])
    _quiescent(Path(a["operational"]["root"]) / OP_DB, a["operational"]["pin"])
    return plan


def assess_worker(root: Path, pin: str, attempt: str) -> None:
    plan = review_context(root, pin)
    with closing(ReadOnlyAnalysisStore(root / REVIEW_DB)) as store:
        existing = store.all("selection_assessment")
    if existing:
        require(
            len(existing) == 1 and existing[0][1]["plan_pin"] == pin,
            "selection_assessment_conflict",
        )
        return
    with closing(ReadOnlyAnalysisStore(root / REVIEW_DB)) as store:
        bound = store.get(
            "selection_readout_index", read_pin(Path(plan["pins_root"]) / "readout.pin")
        )
        require(bound["plan_pin"] == pin, "selection_readout_owner")
        sources = {
            k: store.get("selection_assessment_source", v) for k, v in bound["sources"].items()
        }
    scores = {}
    cases = []
    for i, study in enumerate(plan["studies"]):
        cohort = study["cohort"]
        require(
            cohort in sources and sources[cohort]["tickers"] == study["tickers"],
            "selection_readout_population",
        )
        source = sources[cohort]
        spec = plan["request"]["assessment"]
        require(
            source["version"] == "selection_assessment_source_v1"
            and source["evaluation_window"] == spec["evaluation_window"]
            and not source["q3_labels_generated"]
            and not source["protected_final_test_read"],
            "selection_readout_scope",
        )
        require(
            all(
                spec["evaluation_window"]["start"]
                <= s["trading_date"]
                <= s["label_end"]
                <= spec["evaluation_window"]["end"]
                for g in source["goals"].values()
                for s in g["samples"]
            ),
            "selection_readout_label_scope",
        )
        for case in [c for c in plan["cases"] if c["study"] == i]:
            samples = [
                Sample.from_dict(s) for s in source["goals"][str(case["horizon"])]["samples"]
            ]
            held = set(study["heldout_tickers"])
            samples = [s for s in samples if s.ticker in held]
            # Both controls use the same target stocks; pooled models are explicitly NOT unseen.
            predictions = {}
            for key, record in case["models"].items():
                cache_key = (study["dataset_id"], key)
                if cache_key not in scores:
                    candidate = Candidate(**record["config"]["candidate"])
                    model = verified_estimator(
                        Path(record["checkpoint"]),
                        record["model_sha256"],
                        candidate.family,
                        study["versions"],
                    )
                    scores[cache_key] = (
                        predict_candidate(
                            candidate, model, masked(samples, record["config"]["mask"])
                        )
                        if samples
                        else []
                    )
                predictions[key] = scores[cache_key]
            prior = [case["training_prior"]] * len(samples)
            sel = case["selection"]
            old = predictions[sel["original_group_selection"]]
            top = predictions[sel["highest_search_ap"]]
            new = predictions[sel["stable_selection"]] if sel["stable_selection"] else prior
            labels = [int(s.label) for s in samples if s.label is not None]
            require(len(labels) == len(samples), "selection_missing_label")
            cases.append(
                dict(
                    study=i,
                    cohort=cohort,
                    policy=study["policy"],
                    horizon=case["horizon"],
                    stock_policy=case["stock_policy"],
                    unseen_at_fit=case["stock_policy"] == "heldout",
                    selection=sel,
                    coverage=(1.0 if sel["stable_selection"] else 0.0) if samples else None,
                    coverage_basis="eligible_labeled_rows_only_not_all_sessions",
                    input_shift=shift(case["input_reference"], samples),
                    selection_independent_readout=True,
                    prospective_prediction=False,
                    metrics={
                        k: metrics(labels, p)
                        for k, p in [
                            ("old", old),
                            ("highest_search_ap", top),
                            ("stable_or_prior", new),
                            ("training_prior", prior),
                        ]
                    },
                    rows=[
                        dict(
                            ticker=s.ticker,
                            day=s.trading_date.isoformat(),
                            label_end=s.label_end.isoformat(),
                            label=int(s.label),
                            old=o,
                            new=n,
                        )
                        for s, o, n in zip(samples, old, new, strict=True)
                        if s.label_end is not None and s.label is not None
                    ],
                )
            )
    comparisons = []
    for pol in ("adaptive", "predeclared", "robust"):
        for h in (1, 5, 20):
            for stock in ("pooled", "heldout"):
                group = [
                    c
                    for c in cases
                    if c["policy"] == pol and c["horizon"] == h and c["stock_policy"] == stock
                ]
                days = sorted({r["day"] for c in group for r in c["rows"]})
                chosen = []
                end = None
                for day in days:
                    if end is None or day > end:
                        chosen.append(day)
                        end = max(
                            r["label_end"] for c in group for r in c["rows"] if r["day"] == day
                        )
                by_cohort = {
                    c["cohort"]: [r for r in c["rows"] if r["day"] in chosen] for c in group
                }
                gains = [
                    st.mean(
                        (r["old"] - r["label"]) ** 2 - (r["new"] - r["label"]) ** 2 for r in rows
                    )
                    for rows in by_cohort.values()
                    if rows
                ]
                temporal = [
                    st.mean(
                        st.mean(
                            (r["old"] - r["label"]) ** 2 - (r["new"] - r["label"]) ** 2
                            for r in rows
                            if r["day"] == day
                        )
                        for rows in by_cohort.values()
                        if any(r["day"] == day for r in rows)
                    )
                    for day in chosen
                ]
                rows = [r for rr in by_cohort.values() for r in rr]
                labels = [r["label"] for r in rows]
                old_ba = metrics(labels, [r["old"] for r in rows])["balanced_accuracy"]
                new_ba = metrics(labels, [r["new"] for r in rows])["balanced_accuracy"]
                result = policy.paired_gain(
                    temporal, gains, 0.0 if old_ba is None or new_ba is None else new_ba - old_ba
                )
                if old_ba is None or new_ba is None:
                    result["status"] = "IMPROVEMENT_NOT_ESTABLISHED"
                comparisons.append(
                    dict(
                        policy=pol,
                        horizon=h,
                        stock_policy=stock,
                        nonoverlapping_dates=chosen,
                        result=result,
                    )
                )
    result = dict(
        version="selection_assessment_v1",
        plan_pin=pin,
        policy_pin=plan["policy_pin"],
        cases=cases,
        comparisons=comparisons,
        source_pins={k: digest(v) for k, v in sources.items()},
        new_model_training=0,
        old_experiments_reexecuted=0,
        q3_diagnostic_reused=False,
        classification="retrospective_selection_disjoint_readout_not_prospective",
        independent_future_performance_established=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    review_context(root, pin)
    with closing(AnalysisStore(root / REVIEW_DB)) as store:
        store.put_many(
            [
                *[("selection_assessment_source", v) for v in sources.values()],
                ("selection_assessment", result),
            ]
        )
    retain_assessment(root, plan, digest(result), result)


def run_assessment(root: Path, pin: str) -> dict[str, Any]:
    import uuid

    from donghak_stock_vision.storage.autonomous_runner import launch
    from donghak_stock_vision.storage.gemma_research import process_resources
    from donghak_stock_vision.storage.research_resources import lock, probe, resource_reason

    plan = review_context(root, pin)
    require((Path(plan["pins_root"]) / "readout.pin").is_file(), "selection_prepare_readout_first")
    with lock(root, ".runner.lock"), lock(root.parent, ".autonomous-heavy-work.lock"):
        with closing(ReadOnlyAnalysisStore(root / REVIEW_DB)) as store:
            existing = store.all("selection_assessment")
        if existing:
            require(
                len(existing) == 1 and existing[0][1]["plan_pin"] == pin,
                "selection_assessment_conflict",
            )
            retain_assessment(root, review_context(root, pin), existing[0][0], existing[0][1])
            return dict(
                state="COMPLETE",
                reused=True,
                assessment_id=existing[0][0],
                old_experiments_reexecuted=0,
                new_training=0,
            )
        reason = resource_reason(root, probe())
        resident = process_resources()
        if reason or not resident.get("verified") or resident["rss_kb"] >= 256 * 1024:
            return dict(
                state="BLOCKED",
                reason=reason or "ollama_release_resources_unverified",
                new_training=0,
                old_experiments_reexecuted=0,
            )
        attempt = uuid.uuid4().hex
        audit = launch(root, pin, attempt, selection_review=True)
        with closing(AnalysisStore(root / REVIEW_DB)) as store:
            store.put("selection_resource_audit", audit | dict(attempt=attempt))
        require(
            audit["returncode"] == 0 and audit["reason"] is None,
            "selection_assessment_worker_failed",
        )
        with closing(ReadOnlyAnalysisStore(root / REVIEW_DB)) as store:
            results = store.all("selection_assessment")
        require(len(results) == 1, "selection_assessment_incomplete")
        return dict(
            state="COMPLETE",
            reused=False,
            assessment_id=results[0][0],
            new_training=0,
            old_experiments_reexecuted=0,
            resource_audit=audit,
        )


def retain_assessment(root: Path, plan: dict[str, Any], key: str, result: dict[str, Any]) -> None:
    require(digest(result) == key, "selection_assessment_pin")
    path = root / "assessment.json"
    pin = Path(plan["pins_root"]) / "assessment.pin"
    if path.exists():
        require(FrozenJSON(path.read_text()).identifier == key, "selection_assessment_tampered")
    else:
        write_new(path, json.dumps(result))
    if pin.exists():
        require(read_pin(pin) == key, "selection_assessment_reference_changed")
    else:
        write_new(pin, key)


def shift(reference: dict[str, Any], samples: list[Sample]) -> dict[str, Any]:
    from donghak_stock_vision.features.research import FEATURES_V2

    if not samples:
        return dict(status="NO_TARGET_SAMPLES", causal_attribution="UNVERIFIED")
    values = [
        dict(
            feature=name,
            standardized_mean_change=(st.mean(s.values[j] for s in samples) - reference["mean"][j])
            / (reference["scale"][j] or 1),
            previously_constant=reference["scale"][j] == 0,
        )
        for j, name in enumerate(FEATURES_V2)
    ]
    return dict(
        largest_changes=sorted(
            values, key=lambda v: abs(v["standardized_mean_change"]), reverse=True
        )[:8],
        basis="training_population_vs_target_stocks_and_time",
        causal_attribution="UNVERIFIED",
        used_for_selection=False,
    )


def prepare_readout(root: Path, pin: str) -> dict[str, Any]:
    from donghak_stock_vision.storage.research_resources import lock, probe, resource_reason

    plan = review_context(root, pin)
    with lock(root, ".runner.lock"), lock(root.parent, ".autonomous-heavy-work.lock"):
        reason = resource_reason(root, probe())
        require(reason is None, "selection_source_resources_unavailable")
        index_path = Path(plan["pins_root"]) / "readout.pin"
        if index_path.exists():
            with closing(ReadOnlyAnalysisStore(root / REVIEW_DB)) as store:
                index = store.get("selection_readout_index", read_pin(index_path))
            require(index["plan_pin"] == pin, "selection_readout_owner")
            return dict(state="SOURCE_READY", reused=True, new_training=0)
        sources = {}
        for study in plan["studies"]:
            if study["cohort"] not in sources:
                sources[study["cohort"]] = prepare_source(
                    plan["request"]["assessment"], study["tickers"]
                )
        index = dict(
            version="selection_readout_index_v1",
            plan_pin=pin,
            sources={k: digest(v) for k, v in sources.items()},
            q3_diagnostic_reused=False,
            model_predictions_generated=False,
        )
        review_context(root, pin)
        with closing(AnalysisStore(root / REVIEW_DB)) as store:
            store.put_many(
                [
                    *[("selection_assessment_source", v) for v in sources.values()],
                    ("selection_readout_index", index),
                ]
            )
        write_new(index_path, digest(index))
        return dict(
            state="SOURCE_READY",
            reused=False,
            new_training=0,
            source_rows=sum(v["source_rows"] for v in sources.values()),
            labeled_rows={
                k: {h: len(g["samples"]) for h, g in v["goals"].items()} for k, v in sources.items()
            },
        )


def historical_q3_appendix(root: Path, pin: str) -> dict[str, Any]:
    """Only after registration; never an input to selection, refitting or thresholds."""
    plan = review_context(root, pin)
    rows = []
    for case in plan["cases"]:
        source = plan["studies"][case["study"]]
        old = document(source["report"], maximum=32 * 1024**2)
        ids = {
            case["selection"]["original_group_selection"],
            case["selection"]["highest_search_ap"],
        }
        for e in old["horizons"][str(case["horizon"])]["experiments"]:
            if e["experiment_id"] in ids:
                q = e["deferred_diagnostic"]["heldout_symbols"]
                rows.append(
                    dict(
                        study=case["study"],
                        horizon=case["horizon"],
                        stock_policy=case["stock_policy"],
                        experiment_id=e["experiment_id"],
                        original_selected=e["experiment_id"]
                        == case["selection"]["original_group_selection"],
                        highest_search_ap=e["experiment_id"]
                        == case["selection"]["highest_search_ap"],
                        metrics=q["metrics"],
                        counts=q["counts"],
                        status=q["status"],
                    )
                )
    appendix = dict(
        version="legacy_q3_diagnosis_v1",
        registration_pin=pin,
        rows=rows,
        selection_already_frozen=True,
        used_for_selection=False,
        used_for_tuning=False,
    )
    with closing(AnalysisStore(root / REVIEW_DB)) as store:
        store.put("legacy_q3_diagnosis", appendix)
    return appendix
