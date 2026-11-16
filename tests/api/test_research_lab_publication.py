"""Six-study archived research publication contract: IDs, reports and safe comparisons."""

import hashlib
import json
import os
import shutil
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.research_lab_export_cli import export
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.research_lab_publication import (
    ResearchLabError,
    ResearchLabPublication,
    VERSION,
)


def create_publication(tmp_path: Path) -> tuple[Path, str, list[dict[str, Any]]]:
    root = tmp_path / "publication"
    root.mkdir(mode=0o700)
    entries: list[dict[str, Any]] = []
    original: list[dict[str, Any]] = []
    for cohort in ("A", "B"):
        for policy in ("adaptive", "predeclared", "robust"):
            snapshot = f"{cohort}-{policy}.sqlite3"
            report_name = f"{cohort}-{policy}.json"
            snapshot_path = root / snapshot
            configurations = []
            identities = []
            for h in (1, 5, 20):
                for i in range(4):
                    config = {
                        "goal": {"horizon": h},
                        "stock_policy": "heldout",
                        "candidate": {"family": "ridge", "value": i + 1},
                        "mask": "baseline",
                    }
                    identity = digest([cohort, h, i, config])
                    identities.append((h, identity, config))
                    configurations.append({"experiment_id": identity, "config": config})
            dataset = {
                "dataset_version": "test-fixture",
                "generalization": {"cohort": cohort},
                "goals": {"1": {}, "5": {}, "20": {}},
            }
            dataset_id = digest(dataset)
            base = {
                "version": "autonomous_plan_v6",
                "dataset_id": dataset_id,
                "configurations": configurations,
                "experiment_budget": 12,
                "pit_qualified": False,
                "performance_approved": False,
                "execution_authorized": False,
            }
            plan = base | {"selection_policy": policy}
            if policy != "adaptive":
                plan["policy_parent"] = {"root": "/private/no-expose", "pin": "f" * 64}
            plan_id = digest(plan)
            store = AnalysisStore(snapshot_path)
            store.put("autonomous_plan", plan)
            store.put("autonomous_dataset", dataset)
            horizons: dict[str, Any] = {}
            for h in (1, 5, 20):
                experiments = []
                rows = [row for row in identities if row[0] == h]
                for index, (_, key, config) in enumerate(rows):
                    ap = 0.50 + index * 0.02
                    fold_metrics = {
                        "pr_auc_ap": ap,
                        "balanced_accuracy": 0.50,
                        "brier_score": 0.24,
                        "n": 20,
                    }
                    fold_baselines = {
                        "training_prior": {"pr_auc_ap": 0.50, "n": 20}
                    }
                    robust_summary = {"passes": 1, "sufficient_folds": 1}
                    deferred_diagnostic = {
                        "assessment": {
                            "periods": {
                                "2026-Q3": {"metrics": {
                                    "balanced_accuracy": 0.48, "n": 20,
                                    "private_details": "/private/never-public",
                                }}
                            }
                        }
                    }
                    result = {
                        "experiment_id": key,
                        "config": config,
                        "mean_ap": ap,
                        "stability_ap": ap - 0.01,
                        "largest_train_gap": 0.02,
                        "robust_summary": robust_summary,
                        "deferred_diagnostic": deferred_diagnostic,
                        "folds": [{
                            "evaluation": {
                                "metrics": fold_metrics, "baselines": fold_baselines
                            }
                        }],
                    }
                    result_id = store.put("autonomous_result", result)
                    parent = rows[index - 1][1] if index > 0 else None
                    store.put("autonomous_attempt", {
                        "plan_id": plan_id,
                        "experiment_id": key,
                        "parent_experiment": parent,
                    })
                    store.put("autonomous_terminal", result | {
                        "state": "completed",
                        "result_id": result_id,
                        "checkpoint": "/private/checkpoint/not-for-transport",
                    })
                    experiments.append({
                        "experiment_id": key,
                        "config": config,
                        "mean_seen_cv_ap": ap,
                        "stability_seen_cv_ap": ap - 0.01,
                        "overfit_gap": 0.02,
                        "fold_seen_metrics": [fold_metrics],
                        "fold_baselines": [fold_baselines],
                        "robust_summary": robust_summary,
                        "deferred_diagnostic": deferred_diagnostic,
                    })
                horizons[str(h)] = {
                    "selected_without_heldout_scores": rows[0][1],
                    "experiments": experiments,
                }
            store.close()
            os.chmod(snapshot_path, 0o600)
            condition = digest({
                k: v for k, v in plan.items()
                if k not in ("selection_policy", "policy_parent")
            })
            report = {
                "version": "multi_stock_research_report_v1",
                "dataset_version": "test-fixture",
                "cohort": cohort,
                "selection_policy": policy,
                "dataset_id": dataset_id,
                "comparison_conditions_pin": condition,
                "completed": 12,
                "failed": 0,
                "locked": True,
                "protected_final_test_read": False,
                "independent_future_performance_established": False,
                "pit_qualified": False,
                "performance_approved": False,
                "execution_authorized": False,
                "horizons": horizons,
                "memory_changed_choices": 2,
                "cache_hits": 1,
                "learner_seconds": 100,
                "maximum_rss_kb": 1000,
                "first_date": "2023-01-01",
                "last_date": "2026-09-30",
                "dates": 1160,
                "observations": 30000,
                "historical": {
                    "references": [{"root": "/private/secret", "pin": "a" * 64}]
                },
                "discovery_curve": [{
                    "experiment_id": key, "selection": i + 1,
                    "cache_reused": False,
                } for i, (_, key, _) in enumerate(identities)],
            }
            report_path = root / report_name
            report_path.write_text(json.dumps(report))
            os.chmod(report_path, 0o600)
            entries.append({
                "studyId": plan_id,
                "snapshot": snapshot,
                "snapshotSha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
                "report": report_name,
                "reportSha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            })
            original.append({"studyId": plan_id, "report": report, "reportPath": report_path})
    index = root / "index.json"
    index.write_text(json.dumps({"version": VERSION, "entries": entries}))
    os.chmod(index, 0o600)
    return root, hashlib.sha256(index.read_bytes()).hexdigest(), original


def test_six_reports_seventy_two_experiments_and_descriptive_comparisons(
    tmp_path: Path,
) -> None:
    root, pin, original = create_publication(tmp_path)
    lab = ResearchLabPublication(root, pin)
    app = create_app(lab_reader=lab)
    with TestClient(
        app, client=("127.0.0.1", 12345), base_url="http://127.0.0.1"
    ) as client:
        catalog = client.get("/api/v1/research/lab/studies")
        assert catalog.status_code == 200
        assert catalog.json()["total"] == 6
        reports = client.get("/api/v1/research/lab/reports")
        assert reports.json()["total"] == 6
        comparisons = client.get("/api/v1/research/lab/comparisons").json()
        assert len(comparisons["items"]) == 2
        assert all(
            item["completeThreeArmComparison"]
            and not item["policySuperiorityEstablished"]
            for item in comparisons["items"]
        )
        for owner in original:
            study_id = owner["studyId"]
            detail = client.get(f"/api/v1/research/lab/studies/{study_id}")
            assert detail.status_code == 200
            report_id = detail.json()["reportId"]
            report = client.get(f"/api/v1/research/lab/reports/{report_id}")
            assert report.status_code == 200
            assert report.json()["independentValidationStatus"] == "NOT_ESTABLISHED"
            assert "/private/" not in report.text
            assert "pin" not in report.text.lower()
            first = client.get(
                f"/api/v1/research/lab/studies/{study_id}/experiments?limit=5"
            ).json()
            assert first["total"] == 12 and len(first["items"]) == 5
            second = client.get(
                f"/api/v1/research/lab/studies/{study_id}/experiments?offset=5"
            ).json()
            assert len(second["items"]) == 7
            key = first["items"][0]["experimentId"]
            experiment = client.get(
                f"/api/v1/research/lab/studies/{study_id}/experiments/{key}"
            ).json()
            assert experiment["experimentId"] == key
            assert experiment["reportId"] == report_id
            assert experiment["seenFoldMetrics"][0]["pr_auc_ap"] >= 0.5
            assert "/private/" not in str(experiment)
            memory = client.get(f"/api/v1/research/lab/studies/{study_id}/memory")
            assert memory.status_code == 200
            assert len(memory.json()["events"]) == 12
            assert "/private/" not in memory.text


def test_missing_or_remote_access_denied(tmp_path: Path) -> None:
    root, pin, _ = create_publication(tmp_path)
    app = create_app(lab_reader=ResearchLabPublication(root, pin))
    with TestClient(app) as client:
        assert client.get("/api/v1/research/lab/studies").status_code == 403
    with TestClient(
        app, client=("127.0.0.1", 12345), base_url="http://127.0.0.1"
    ) as client:
        assert client.get(
            "/api/v1/research/lab/studies",
            headers={"Origin": "https://remote.example"},
        ).status_code == 403
        assert client.get(
            f"/api/v1/research/lab/reports/{'a' * 64}"
        ).status_code == 404
        assert client.get(
            f"/api/v1/research/lab/studies/{'a' * 64}/experiments"
        ).status_code == 404
    with TestClient(
        create_app(), client=("127.0.0.1", 12345), base_url="http://127.0.0.1"
    ) as client:
        assert client.get("/api/v1/research/lab/studies").status_code == 503


def test_corrupt_index_and_report_owner_fail_closed(tmp_path: Path) -> None:
    root, pin, original = create_publication(tmp_path)
    with pytest.raises(ResearchLabError, match="index_pin_mismatch"):
        ResearchLabPublication(root, "a" * 64)
    target = original[0]["reportPath"]
    report = json.loads(target.read_text())
    report["cohort"] = "forged"
    target.write_text(json.dumps(report))
    os.chmod(target, 0o600)
    with pytest.raises(ResearchLabError, match="report_pin_mismatch"):
        ResearchLabPublication(root, pin)
    # Re-pinning both the report and index cannot fabricate dataset ownership.
    index_path = root / "index.json"
    index = json.loads(index_path.read_text())
    index["entries"][0]["reportSha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    index_path.write_text(json.dumps(index))
    with pytest.raises(ResearchLabError, match="report_owner_mismatch"):
        ResearchLabPublication(root, hashlib.sha256(index_path.read_bytes()).hexdigest())


def test_publication_never_reloads_after_startup(tmp_path: Path) -> None:
    root, pin, original = create_publication(tmp_path)
    lab = ResearchLabPublication(root, pin)
    original[0]["reportPath"].write_text("tampered")
    assert lab.report_catalog()["total"] == 6
    assert lab.comparisons()["items"][0]["policySuperiorityEstablished"] is False


def test_offline_export_creates_valid_approved_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, original = create_publication(tmp_path)
    sources = []
    for index, owner in enumerate(original):
        private = tmp_path / f"source-{index}"
        private.mkdir(mode=0o700)
        policy = owner["report"]["selection_policy"]
        cohort = owner["report"]["cohort"]
        shutil.copy2(root / f"{cohort}-{policy}.sqlite3", private / "experiments.sqlite3")
        shutil.copy2(root / f"{cohort}-{policy}.json", private / "final-research-report.json")
        sources.append(f"{private}:{owner['studyId']}")

    def read_context(private: Path, study_id: str, *, code_check: bool = False) -> tuple[
        dict[str, Any], dict[str, Any]
    ]:
        assert not code_check
        with closing(ReadOnlyAnalysisStore(private / "experiments.sqlite3")) as store:
            return store.get("autonomous_plan", study_id), {}

    monkeypatch.setattr("donghak_stock_vision.research_lab_export_cli.context", read_context)
    output = tmp_path / "export"
    receipt = export(sources, output)
    assert receipt["httpServingEnabled"] is False
    assert receipt["researchExecutionTriggered"] is False
    assert receipt["studyCount"] == 6
    assert output.stat().st_mode & 0o077 == 0
    assert all(item.stat().st_mode & 0o077 == 0 for item in output.iterdir())
    assert ResearchLabPublication(output, receipt["indexSha256"]).catalog()["total"] == 6
    with pytest.raises(ValueError, match="invalid_publication_output"):
        export(sources, output)
