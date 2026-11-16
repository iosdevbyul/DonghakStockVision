"""Operator-approved, immutable multi-study research publication for local read-only UI.

Never serves raw experiment databases, report documents, paths or private source PINs.
The offline operator approves an independent index digest binding six report/snapshot pairs.
"""
import hashlib
import json
import re
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.research_studies import PinnedStudyReader

VERSION = "research_lab_publication_index_v1"
HEX = re.compile(r"^[a-f0-9]{64}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")
METRICS = ("pr_auc_ap", "balanced_accuracy", "brier_score", "n", "positive", "negative",
           "accuracy", "roc_auc", "f1")
MAX_REPORT = 8 * 1024 * 1024
MAX_INDEX = 64 * 1024


class ResearchLabError(ValueError):
    """Public-safe error codes; never include filesystem paths or raw values."""


def ensure(ok: bool, code: str) -> None:
    if not ok:
        raise ResearchLabError(code)


def approved_file(root: Path, name: str, maximum: int) -> bytes:
    ensure(bool(NAME.fullmatch(name)) and name not in {".", ".."}, "invalid_publication_name")
    path = root / name
    ensure(
        not path.is_symlink() and path.is_file()
        and 0 < path.stat().st_size <= maximum
        and path.stat().st_mode & 0o077 == 0,
        "invalid_publication_file",
    )
    content = path.read_bytes()
    ensure(0 < len(content) <= maximum, "oversized_publication")
    return content


def approved_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ResearchLabError("invalid_publication_json") from error
    ensure(type(value) is dict, "invalid_publication_document")
    return value


def metric_view(metrics: Any) -> dict[str, Any] | None:
    if not isinstance(metrics, dict):
        return None
    return {key: metrics.get(key) for key in METRICS if key in metrics}


def safe_diagnostic(value: Any) -> dict[str, Any] | None:
    """Allowlisted diagnostic populations, never source paths or selection authority."""
    if not isinstance(value, dict):
        return None
    assessment = value.get("assessment", value)
    if not isinstance(assessment, dict):
        return None
    groups: dict[str, Any] = {}
    for section in ("periods", "input_regimes", "heldout_regimes", "ticker_distribution"):
        source = assessment.get(section)
        if isinstance(source, dict):
            groups[section] = {
                key: {
                    "metrics": metric_view(item.get("metrics")),
                    "status": item.get("status")
                    if item.get("status") in {"internal_descriptive_sufficient", "insufficient"}
                    else "UNKNOWN",
                    "independentlyQualified": False,
                }
                for key, item in source.items()
                if isinstance(key, str) and NAME.fullmatch(key) and isinstance(item, dict)
            }
    for section in ("seen_symbols", "heldout_symbols"):
        item = assessment.get(section)
        if isinstance(item, dict):
            groups[section] = {
                "metrics": metric_view(item.get("metrics")),
                "status": item.get("status")
                if item.get("status") in {"internal_descriptive_sufficient", "insufficient"}
                else "UNKNOWN",
                "independentlyQualified": False,
            }
    return groups or None


class ResearchLabPublication:
    """Preload safe projections of immutable independently pinned source artifacts.

    Source files are read only at startup. All HTTP requests use in-memory safe
    projections, so mutation after startup cannot alter any published response.
    """

    def __init__(self, root: Path, index_sha256: str) -> None:
        ensure(bool(HEX.fullmatch(index_sha256)), "invalid_publication_pin")
        ensure(root.is_absolute() and not root.is_symlink() and root.is_dir(), "invalid_root")
        ensure(root.stat().st_mode & 0o077 == 0, "unprotected_publication_root")
        raw = approved_file(root, "index.json", MAX_INDEX)
        ensure(hashlib.sha256(raw).hexdigest() == index_sha256, "index_pin_mismatch")
        index = approved_json(raw)
        ensure(index.get("version") == VERSION, "unsupported_publication_version")
        entries = index.get("entries")
        ensure(isinstance(entries, list) and 1 <= len(entries) <= 24, "invalid_study_count")
        self.studies: dict[str, dict[str, Any]] = {}
        self.experiments: dict[tuple[str, str], dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            self._ingest(root, entry)
        ensure(len(self.studies) == len(entries), "duplicate_study")
        for group in self.groups.values():
            ensure(len({v["selectionPolicy"] for v in group}) == len(group),
                   "duplicate_policy_arm")

    def _ingest(self, root: Path, entry: Any) -> None:
        ensure(
            type(entry) is dict
            and set(entry) == {
                "studyId", "snapshot", "snapshotSha256", "report", "reportSha256"
            },
            "invalid_publication_entry",
        )
        study_id = entry["studyId"]
        ensure(isinstance(study_id, str) and bool(HEX.fullmatch(study_id)),
               "invalid_study_id")
        ensure(study_id not in self.studies, "duplicate_study")
        for field in ("snapshotSha256", "reportSha256"):
            ensure(isinstance(entry[field], str) and bool(HEX.fullmatch(entry[field])),
                   "invalid_publication_pin")
        for field in ("snapshot", "report"):
            ensure(isinstance(entry[field], str) and bool(NAME.fullmatch(entry[field])),
                   "invalid_publication_name")
        path = root / entry["snapshot"]
        ensure(entry["snapshot"].endswith(".sqlite3"), "invalid_snapshot_extension")
        ensure(entry["report"].endswith(".json") and entry["report"] != "index.json",
               "invalid_report_extension")
        reader = PinnedStudyReader(path, entry["snapshotSha256"], study_id)
        rows = reader.experiments(study_id, 0, 1000)["items"]
        original_ids = {row["experimentId"] for row in rows if row["status"] == "COMPLETED"}
        failed_count = sum(row["status"] == "FAILED" for row in rows)
        with closing(ReadOnlyAnalysisStore(path)) as store:
            plan = store.get("autonomous_plan", study_id)
            dataset = store.get("autonomous_dataset", plan["dataset_id"])
            validated_terminals = reader._read()[1]
            actual_results = {
                key: store.get("autonomous_result", validated_terminals[key]["result_id"])
                for key in original_ids
            }
        report_raw = approved_file(root, entry["report"], MAX_REPORT)
        ensure(hashlib.sha256(report_raw).hexdigest() == entry["reportSha256"],
               "report_pin_mismatch")
        report = approved_json(report_raw)
        ensure(
            report.get("version") == "multi_stock_research_report_v1"
            and report.get("locked") is True
            and report.get("dataset_id") == plan["dataset_id"]
            and report.get("dataset_version") == dataset["dataset_version"]
            and report.get("selection_policy") == plan["selection_policy"]
            and report.get("cohort") == dataset["generalization"]["cohort"]
            and report.get("completed") == len(original_ids)
            and report.get("failed") == failed_count
            and report.get("completed") + report.get("failed") == plan["experiment_budget"]
            and report.get("protected_final_test_read") is False
            and report.get("independent_future_performance_established") is False
            and report.get("pit_qualified") is False
            and report.get("performance_approved") is False
            and report.get("execution_authorized") is False,
            "report_owner_mismatch",
        )
        comparison = digest({
            key: value for key, value in plan.items()
            if key not in ("selection_policy", "policy_parent")
        })
        ensure(report.get("comparison_conditions_pin") == comparison,
               "comparison_scope_mismatch")
        horizons = report.get("horizons")
        ensure(isinstance(horizons, dict) and set(horizons) == set(dataset["goals"]),
               "invalid_report_horizons")
        report_ids: set[str] = set()
        summaries = []
        for horizon, value in sorted(horizons.items(), key=lambda kv: int(kv[0])):
            ensure(isinstance(value, dict) and isinstance(value.get("experiments"), list),
                   "invalid_report_experiments")
            candidates = value["experiments"]
            selected = value.get("selected_without_heldout_scores")
            horizon_ids = {r.get("experiment_id") for r in candidates if isinstance(r, dict)}
            ensure(selected is None or selected in horizon_ids, "invalid_selected_experiment")
            for result in candidates:
                ensure(isinstance(result, dict), "invalid_report_experiment")
                key = result.get("experiment_id")
                ensure(
                    key in original_ids and key not in report_ids
                    and result.get("config") == next(
                        row["config"] for row in plan["configurations"]
                        if row["experiment_id"] == key
                    ),
                    "invalid_report_experiment_link",
                )
                original = actual_results[key]
                ensure(
                    result.get("mean_seen_cv_ap") == original["mean_ap"]
                    and result.get("stability_seen_cv_ap") == original["stability_ap"]
                    and result.get("overfit_gap") == original["largest_train_gap"],
                    "report_metrics_owner_mismatch",
                )
                ensure(
                    result.get("robust_summary") == original.get("robust_summary")
                    and result.get("deferred_diagnostic") == original.get("deferred_diagnostic"),
                    "report_diagnostic_owner_mismatch",
                )
                if "folds" in original:
                    ensure(
                        result.get("fold_seen_metrics") == [
                            fold["evaluation"]["metrics"] for fold in original["folds"]
                        ] and result.get("fold_baselines") == [
                            fold["evaluation"]["baselines"] for fold in original["folds"]
                        ],
                        "report_fold_owner_mismatch",
                    )
                report_ids.add(key)
                ref = next(row for row in rows if row["experimentId"] == key)
                ref = ref | {
                    "reportId": hashlib.sha256(report_raw).hexdigest(),
                    "selectedForHorizon": key == selected,
                    "seenFoldMetrics": [
                        metric_view(v) for v in result.get("fold_seen_metrics", [])
                    ],
                    "seenFoldBaselines": [
                        {
                            name: metric_view(value)
                            for name, value in fold.items()
                            if name in ("training_prior", "training_majority", "momentum")
                        }
                        for fold in result.get("fold_baselines", [])
                        if isinstance(fold, dict)
                    ],
                    "deferredDiagnostic": safe_diagnostic(result.get("deferred_diagnostic")),
                    "robustSummary": {
                        name: result["robust_summary"].get(name)
                        for name in ("passes", "sufficient_folds", "worst_balanced_accuracy",
                                     "worst_baseline_ap_gain", "negative_dispersion")
                    } if isinstance(result.get("robust_summary"), dict) else None,
                    "comparisonQualification": "DESCRIPTIVE_RESEARCH_ONLY",
                }
                self.experiments[(study_id, key)] = ref
            summaries.append({
                "horizon": int(horizon),
                "experimentCount": len(candidates),
                "selectedExperimentId": selected,
                "selectedMeanAP": next(
                    (result.get("mean_seen_cv_ap") for result in candidates
                     if result["experiment_id"] == selected), None
                ),
                "bestSeenAP": max(
                    (float(result["mean_seen_cv_ap"]) for result in candidates),
                    default=None,
                ),
                "selectedSeenFoldMetrics": next(
                    ([metric_view(m) for m in result.get("fold_seen_metrics", [])]
                     for result in candidates if result["experiment_id"] == selected),
                    [],
                ),
                "selectedDeferredDiagnostic": next(
                    (safe_diagnostic(result.get("deferred_diagnostic"))
                     for result in candidates if result["experiment_id"] == selected),
                    None,
                ),
                "selectionUsesDeferredDiagnostic": False,
            })
        ensure(report_ids == original_ids, "incomplete_report_identity")
        discovery = report.get("discovery_curve", [])
        ensure(isinstance(discovery, list), "invalid_discovery_curve")
        if discovery:
            discovered_ids = [v.get("experiment_id") for v in discovery if isinstance(v, dict)]
            ensure(len(discovery) == len(discovered_ids)
                   and len(set(discovered_ids)) == len(discovered_ids)
                   and set(discovered_ids) == original_ids,
                   "invalid_discovery_identity")
        report_id = hashlib.sha256(report_raw).hexdigest()
        summary = {
            "studyId": study_id,
            "reportId": report_id,
            "cohort": report["cohort"],
            "selectionPolicy": report["selection_policy"],
            "experimentBudget": plan["experiment_budget"],
            "completed": report["completed"],
            "failed": report["failed"],
            "locked": True,
            "comparisonGroupId": digest([comparison, report["cohort"]]),
            "horizons": summaries,
            "researchOnly": True,
            "independentValidationStatus": "NOT_ESTABLISHED",
            "pitQualified": False,
            "performanceApproved": False,
            "executionAuthorized": False,
        }
        ensure(report_id not in self.reports, "duplicate_report")
        self.studies[study_id] = summary
        self.groups[summary["comparisonGroupId"]].append(summary)
        self.reports[report_id] = summary | {
            "researchPeriod": {
                key: report.get(key) for key in ("first_date", "last_date", "dates",
                                                "observations")
            },
            "sourceClass": "ARCHIVED_KRX_RESEARCH_NOT_INDEPENDENT_PIT",
            "memoryChangedChoices": report.get("memory_changed_choices"),
            "cacheHits": report.get("cache_hits"),
            "learnerSeconds": report.get("learner_seconds"),
            "maximumRssKb": report.get("maximum_rss_kb"),
            "discoveryCurve": [
                {
                    "experimentId": item.get("experiment_id"),
                    "selection": item.get("selection"),
                    "cacheReused": item.get("cache_reused"),
                }
                for item in report.get("discovery_curve", [])
                if isinstance(item, dict) and item.get("experiment_id") in original_ids
            ],
            "limitations": [
                "independent_future_performance_not_established",
                "historical_diagnostic_not_selection_evidence",
                "population_comparison_descriptive_only",
            ],
        }

    def catalog(self, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        rows = sorted(self.studies.values(), key=lambda s: (s["cohort"], s["selectionPolicy"]))
        return {
            "schemaVersion": "research_lab_studies_v1",
            "total": len(rows), "offset": offset, "limit": limit,
            "items": rows[offset : offset + limit],
        }

    def study(self, study_id: str) -> dict[str, Any] | None:
        return self.studies.get(study_id)

    def memory(self, study_id: str) -> dict[str, Any] | None:
        study = self.studies.get(study_id)
        if study is None:
            return None
        report = self.reports[study["reportId"]]
        return {
            "schemaVersion": "research_lab_memory_v1",
            "studyId": study_id,
            "events": [
                {
                    "experimentId": item["experimentId"],
                    "selectionOrder": item["selection"],
                    "cacheReused": item["cacheReused"],
                    "parentExperimentId": self.experiments[
                        (study_id, item["experimentId"])
                    ]["parentExperimentId"],
                }
                for item in report["discoveryCurve"]
            ],
            "memoryChangedChoices": report["memoryChangedChoices"],
            "interpretation": "HISTORICAL_SEARCH_ONLY_NOT_CAUSAL_LLM_EVIDENCE",
        }

    def experiment_catalog(
        self, study_id: str, offset: int = 0, limit: int = 25
    ) -> dict[str, Any] | None:
        if study_id not in self.studies:
            return None
        rows = [row for (owner, _), row in self.experiments.items() if owner == study_id]
        rows.sort(key=lambda x: (x["horizon"], x["experimentId"]))
        return {
            "schemaVersion": "research_lab_experiments_v1", "studyId": study_id,
            "total": len(rows), "offset": offset, "limit": limit,
            "items": rows[offset : offset + limit],
        }

    def experiment(self, study_id: str, experiment_id: str) -> dict[str, Any] | None:
        return self.experiments.get((study_id, experiment_id))

    def report_catalog(self, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        rows = sorted(self.studies.values(), key=lambda s: (s["cohort"], s["selectionPolicy"]))
        return {
            "schemaVersion": "research_lab_reports_v1",
            "total": len(rows), "offset": offset, "limit": limit,
            "items": rows[offset : offset + limit],
        }

    def report(self, report_id: str) -> dict[str, Any] | None:
        return self.reports.get(report_id)

    def comparisons(self) -> dict[str, Any]:
        rows = []
        for group_id, arms in sorted(self.groups.items()):
            arm_list = sorted(arms, key=lambda x: x["selectionPolicy"])
            horizons = sorted({h["horizon"] for arm in arm_list for h in arm["horizons"]})
            rows.append({
                "comparisonGroupId": group_id,
                "cohort": arm_list[0]["cohort"],
                "policies": [arm["selectionPolicy"] for arm in arm_list],
                "completeThreeArmComparison": set(a["selectionPolicy"] for a in arm_list) == {
                    "adaptive", "predeclared", "robust"
                },
                "horizons": [{
                    "horizon": horizon,
                    "arms": [{
                        "studyId": arm["studyId"],
                        "policy": arm["selectionPolicy"],
                        **next((h for h in arm["horizons"] if h["horizon"] == horizon), {}),
                    } for arm in arm_list],
                } for horizon in horizons],
                "conclusionStatus": "DESCRIPTIVE_ONLY_NOT_INDEPENDENT",
                "policySuperiorityEstablished": False,
            })
        return {"schemaVersion": "research_lab_comparisons_v1", "items": rows}
