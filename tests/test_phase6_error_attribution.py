"""Phase 6 frozen error attribution CLI tests."""

import json
import subprocess
import sys
from pathlib import Path

from donghak_stock_vision.storage.analysis import AnalysisStore

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phase6_error_attribution.py"


def _metrics(fp: int, fn: int, tp: int = 1, tn: int = 1) -> dict[str, object]:
    return {"confusion_matrix": {"fp": fp, "fn": fn, "tp": tp, "tn": tn}}


def _run(path: Path, evaluation_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--analysis-db", str(path), "--evaluation-id", evaluation_id],
        capture_output=True,
        text=True,
        check=False,
    )


def test_error_attribution_reconciles_and_ranks_tickers(tmp_path: Path) -> None:
    path = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(path)
    metrics = _metrics(2, 1, tp=2, tn=2)
    metrics["by_ticker"] = {
        "BBB": _metrics(0, 1),
        "AAA": _metrics(2, 0),
    }
    evaluation = {
        "mode": "historical_research",
        "operational_status": "unregistered",
        "model_version": "model-1",
        "snapshot_id": "snapshot-1",
        "directions": {
            "up": {"test_status": "evaluated", "test": metrics},
            "down": {"test_status": "unavailable"},
        },
    }
    try:
        evaluation_id = store.put("evaluation", evaluation)
    finally:
        store.close()
    result = _run(path, evaluation_id)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["directions"]["up"]["tickers"][0]["ticker"] == "AAA"
    assert report["directions"]["up"]["falsePositives"] == 2
    assert report["directions"]["down"]["status"] == "unavailable"


def test_error_attribution_rejects_inconsistent_ticker_counts(tmp_path: Path) -> None:
    path = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(path)
    metrics = _metrics(2, 1)
    metrics["by_ticker"] = {"AAA": _metrics(0, 0)}
    try:
        evaluation_id = store.put(
            "evaluation",
            {
                "mode": "historical_research",
                "operational_status": "unregistered",
                "model_version": "model-1",
                "snapshot_id": "snapshot-1",
                "directions": {
                    "up": {"test_status": "evaluated", "test": metrics},
                    "down": {"test_status": "unavailable"},
                },
            },
        )
    finally:
        store.close()
    result = _run(path, evaluation_id)
    assert result.returncode == 2
    assert "does not reconcile" in result.stderr
