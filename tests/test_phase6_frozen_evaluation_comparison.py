"""Tests for read-only Phase 6 frozen evaluation comparisons."""

import json
import subprocess
import sys
from pathlib import Path

from donghak_stock_vision.storage.analysis import AnalysisStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "phase6_compare_evaluations.py"


def _evaluation(snapshot: str, ap: float) -> dict[str, object]:
    return {
        "mode": "historical_research",
        "operational_status": "unregistered",
        "usage_restriction": "historical_research_only",
        "model_version": f"model-{ap}",
        "snapshot_id": snapshot,
        "directions": {
            name: {
                "test_status": "evaluated",
                "evaluation_status": "validation_qualified",
                "test": {"pr_auc_ap": ap},
                "test_baselines": {"prior": {"pr_auc_ap": 0.5}},
                "validation_ap_margin": 0.04,
            }
            for name in ("up", "down")
        },
    }


def _run(path: Path, a: str, b: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--analysis-db",
            str(path),
            "--reference-id",
            a,
            "--candidate-id",
            b,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_frozen_comparison_reports_descriptive_delta(tmp_path: Path) -> None:
    path = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(path)
    try:
        a = store.put("evaluation", _evaluation("snapshot-1", 0.6))
        b = store.put("evaluation", _evaluation("snapshot-1", 0.7))
    finally:
        store.close()
    result = _run(path, a, b)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["directions"]["up"]["testAPDelta"] == 0.7 - 0.6
    assert report["directions"]["down"]["status"] == "descriptive_only"


def test_frozen_comparison_rejects_different_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(path)
    try:
        a = store.put("evaluation", _evaluation("snapshot-1", 0.6))
        b = store.put("evaluation", _evaluation("snapshot-2", 0.7))
    finally:
        store.close()
    result = _run(path, a, b)
    assert result.returncode == 2
    assert "different snapshots" in result.stderr
