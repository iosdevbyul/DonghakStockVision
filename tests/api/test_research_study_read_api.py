"""Offline contracts for the local pinned autonomous study projection."""

import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.research_studies import PinnedStudyReader, StudyReadError
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import AnalysisStore


def study(tmp_path: Path) -> tuple[Path, str, str, str]:
    path = tmp_path / "study.sqlite3"
    config = {
        "goal": {"horizon": 5},
        "candidate": {"family": "ridge", "value": 1},
        "mask": "baseline",
        "stock_policy": "pooled",
    }
    experiment_id = digest({"test_configuration": config})
    plan = {
        "version": "autonomous_plan_v6",
        "selection_policy": "robust",
        "experiment_budget": 12,
        "pit_qualified": False,
        "performance_approved": False,
        "execution_authorized": False,
        "configurations": [{"experiment_id": experiment_id, "config": config}],
    }
    plan_id = digest(plan)
    store = AnalysisStore(path)
    store.put("autonomous_plan", plan)
    store.put(
        "autonomous_attempt",
        {"plan_id": plan_id, "experiment_id": experiment_id, "parent_experiment": None},
    )
    result_id = store.put(
        "autonomous_result",
        {
            "experiment_id": experiment_id,
            "config": config,
            "mean_ap": 0.55,
            "stability_ap": 0.52,
            "largest_train_gap": 0.04,
        },
    )
    store.put(
        "autonomous_terminal",
        {
            "experiment_id": experiment_id,
            "config": config,
            "state": "completed",
            "result_id": result_id,
            "mean_ap": 0.55,
            "stability_ap": 0.52,
            "largest_train_gap": 0.04,
            "checkpoint": "/private/do-not-expose",
        },
    )
    store.close()
    os.chmod(path, 0o600)
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), plan_id, experiment_id


def test_read_only_study_and_original_experiment_id(tmp_path: Path) -> None:
    path, pin, plan_id, experiment_id = study(tmp_path)
    reader = PinnedStudyReader(path, pin, plan_id)
    app = create_app(study_reader=reader)
    with TestClient(app, client=("127.0.0.1", 12345), base_url="http://127.0.0.1") as client:
        study_response = client.get("/api/v1/research/studies")
        assert study_response.status_code == 200
        assert study_response.json()["items"][0]["studyId"] == plan_id
        assert study_response.json()["items"][0]["independentValidationStatus"] == "NOT_ESTABLISHED"
        listing = client.get(f"/api/v1/research/studies/{plan_id}/experiments")
        assert listing.status_code == 200
        row = listing.json()["items"][0]
        assert row["experimentId"] == experiment_id
        assert row["meanAP"] == 0.55
        assert row["reportId"] is None
        assert "checkpoint" not in str(row)
        assert "source_pin" not in str(row)
        assert (
            client.get(f"/api/v1/research/studies/{plan_id}/experiments/{experiment_id}").json()
            == row
        )
        assert (
            client.get(f"/api/v1/research/studies/{plan_id}/experiments/{'f' * 64}").status_code
            == 404
        )


def test_local_only_and_missing_reader(tmp_path: Path) -> None:
    path, pin, plan_id, _ = study(tmp_path)
    reader = PinnedStudyReader(path, pin, plan_id)
    app = create_app(study_reader=reader)
    with TestClient(app) as client:
        assert client.get("/api/v1/research/studies").status_code == 403
    with TestClient(app, client=("127.0.0.1", 12345), base_url="http://127.0.0.1") as client:
        assert (
            client.get(
                "/api/v1/research/studies", headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert client.get(f"/api/v1/research/studies/{'f' * 64}/experiments").status_code == 404
    with TestClient(
        create_app(), client=("127.0.0.1", 12345), base_url="http://127.0.0.1"
    ) as client:
        assert client.get("/api/v1/research/studies").status_code == 503


def test_mismatched_or_mutated_snapshot_rejected(tmp_path: Path) -> None:
    path, pin, plan_id, _ = study(tmp_path)
    with pytest.raises(StudyReadError):
        PinnedStudyReader(path, "a" * 64, plan_id)
    reader = PinnedStudyReader(path, pin, plan_id)
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(StudyReadError):
        reader.collection()


def test_unauthorized_plan_flags_rejected(tmp_path: Path) -> None:
    path = tmp_path / "study.sqlite3"
    plan = {
        "version": "autonomous_plan_v6",
        "pit_qualified": True,
        "performance_approved": False,
        "execution_authorized": False,
        "configurations": [],
    }
    with_store = AnalysisStore(path)
    plan_id = with_store.put("autonomous_plan", plan)
    with_store.close()
    os.chmod(path, 0o600)
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(StudyReadError, match="unsafe_study_approval"):
        PinnedStudyReader(path, pin, plan_id)
