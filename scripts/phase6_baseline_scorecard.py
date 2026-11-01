"""Read-only Phase 6 baseline scorecard from immutable evaluation artifacts."""

import argparse
import json
from pathlib import Path

from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore


def scorecard(path: Path, evaluation_id: str) -> dict[str, object]:
    if not path.is_file():
        raise ValueError("analysis database does not exist")
    store = ReadOnlyAnalysisStore(path)
    try:
        evaluation = store.get("evaluation", evaluation_id)
        if evaluation.get("mode") != "historical_research":
            raise ValueError("only historical research evaluations supported")
        if evaluation.get("operational_status") != "unregistered":
            raise ValueError("evaluation is not research-only")
        directions = {}
        for name in ("up", "down"):
            entry = evaluation["directions"][name]
            test = entry.get("test")
            baselines = entry.get("test_baselines", {})
            if not isinstance(test, dict) or not isinstance(baselines, dict):
                directions[name] = {"status": "unavailable"}
                continue
            ap = test.get("pr_auc_ap")
            baseline_aps = {
                key: value.get("pr_auc_ap")
                for key, value in baselines.items()
                if isinstance(value, dict)
            }
            numeric = {
                key: value
                for key, value in baseline_aps.items()
                if type(value) in (int, float)
            }
            margin = (
                ap - max(numeric.values())
                if type(ap) in (int, float) and numeric
                else None
            )
            directions[name] = {
                "status": entry.get("test_status", "unknown"),
                "evaluationStatus": entry.get("evaluation_status"),
                "testAveragePrecision": ap,
                "baselineAveragePrecision": baseline_aps,
                "testMarginVsBestBaseline": margin,
                "validationMargin": entry.get("validation_ap_margin"),
            }
        return {
            "schemaVersion": "phase6_baseline_scorecard_v1",
            "evaluationId": evaluation_id,
            "modelVersion": evaluation["model_version"],
            "snapshotId": evaluation["snapshot_id"],
            "mode": evaluation["mode"],
            "usageRestriction": evaluation["usage_restriction"],
            "qualityFlags": evaluation.get("quality_flags"),
            "directions": directions,
            "conclusion": "descriptive research only; no live accuracy or investment approval",
        }
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-db", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(scorecard(args.analysis_db, args.evaluation_id), sort_keys=True))
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"scorecard failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
