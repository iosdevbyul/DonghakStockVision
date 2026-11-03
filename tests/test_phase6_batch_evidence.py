"""Batch evidence audit tests with only synthetic immutable records."""

import json
import subprocess
import sys
from pathlib import Path

from donghak_stock_vision.storage.analysis import AnalysisStore

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phase6_batch_evidence.py"


def _run(database: Path, ids: list[str], strict: bool = False) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--analysis-db",
        str(database),
    ]
    for evaluation_id in ids:
        command.extend(["--evaluation-id", evaluation_id])
    if strict:
        command.append("--require-complete")
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_batch_audit_preserves_order_and_rejects_incomplete_evidence(tmp_path: Path) -> None:
    database = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(database)
    matrix = {"tp": 1, "fp": 1, "tn": 1, "fn": 0}
    metrics = {
        "pr_auc_ap": 0.6,
        "confusion_matrix": matrix,
        "by_ticker": {"AAA": {"confusion_matrix": matrix}},
    }
    evaluated = {
        "test_status": "evaluated",
        "test": metrics,
        "test_baselines": {"prior": {"pr_auc_ap": 0.5}},
    }
    common = {
        "mode": "historical_research",
        "operational_status": "unregistered",
        "usage_restriction": "research_only",
        "snapshot_id": "snapshot-1",
    }
    try:
        complete_id = store.put(
            "evaluation",
            {
                **common,
                "model_version": "complete-model",
                "directions": {"up": evaluated, "down": evaluated},
            },
        )
        incomplete_id = store.put(
            "evaluation",
            {
                **common,
                "model_version": "incomplete-model",
                "directions": {"up": evaluated, "down": {"test_status": "unavailable"}},
            },
        )
    finally:
        store.close()
    before = database.read_bytes()
    selected = [incomplete_id, complete_id]
    result = _run(database, selected)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["evaluationCount"] == 2
    assert output["completeCount"] == 1
    assert output["incompleteCount"] == 1
    assert output["allComplete"] is False
    assert output["evaluationsWithBlockers"] == 1
    assert output["blockerCounts"]["down_test_not_evaluated"] == 1
    assert output["blockerCounts"]["down_error_attribution_unavailable"] == 1
    assert output["incompleteEvaluationIds"] == [incomplete_id]
    assert output["blockerEvaluationIds"]["down_test_not_evaluated"] == [incomplete_id]
    assert output["blockerEvaluationIds"]["down_error_attribution_unavailable"] == [incomplete_id]
    assert [row["evaluationId"] for row in output["reports"]] == selected
    strict = _run(database, selected, strict=True)
    assert strict.returncode == 3, strict.stderr
    assert json.loads(strict.stdout)["allComplete"] is False
    successful = _run(database, [complete_id], strict=True)
    assert successful.returncode == 0, successful.stderr
    assert json.loads(successful.stdout)["allComplete"] is True
    assert database.read_bytes() == before


def test_batch_audit_rejects_duplicate_ids_and_missing_database(tmp_path: Path) -> None:
    database = tmp_path / "absent.sqlite3"
    duplicate = _run(database, ["same", "same"])
    assert duplicate.returncode == 2
    assert "duplicate evaluation IDs" in duplicate.stderr
    missing = _run(database, ["missing"])
    assert missing.returncode == 2
    assert not database.exists()
