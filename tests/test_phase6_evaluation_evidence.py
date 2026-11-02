"""CLI smoke tests for the combined frozen Phase 6 evidence report."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from donghak_stock_vision.storage.analysis import AnalysisStore

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phase6_evaluation_evidence.py"


def test_combined_evidence_consistent_and_read_only(tmp_path: Path) -> None:
    database = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(database)
    matrix = {"tp": 1, "fp": 1, "tn": 1, "fn": 0}
    metrics = {
        "pr_auc_ap": 0.6,
        "confusion_matrix": matrix,
        "by_ticker": {"AAA": {"confusion_matrix": matrix}},
    }
    record = {
        "mode": "historical_research",
        "operational_status": "unregistered",
        "usage_restriction": "research_only",
        "model_version": "model-1",
        "snapshot_id": "snapshot-1",
        "directions": {
            "up": {
                "test_status": "evaluated",
                "evaluation_status": "baseline_not_beaten",
                "test": metrics,
                "test_baselines": {"prior": {"pr_auc_ap": 0.5}},
            },
            "down": {"test_status": "unavailable"},
        },
    }
    try:
        evaluation_id = store.put("evaluation", record)
    finally:
        store.close()
    before = database.read_bytes()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--analysis-db", str(database),
         "--evaluation-id", evaluation_id],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["baseline"]["directions"]["up"]["testMarginVsBestBaseline"] == pytest.approx(0.1)
    assert report["errors"]["directions"]["up"]["falsePositives"] == 1
    assert report["evaluationId"] == evaluation_id
    assert database.read_bytes() == before


def test_missing_database_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--analysis-db", str(tmp_path / "absent.sqlite3"),
         "--evaluation-id", "unknown"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
