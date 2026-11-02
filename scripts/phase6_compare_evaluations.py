"""Compare two frozen Phase 6 evaluations without changing research artifacts."""

import argparse
import json
from pathlib import Path

from phase6_baseline_scorecard import scorecard


def compare(path: Path, reference_id: str, candidate_id: str) -> dict[str, object]:
    if reference_id == candidate_id:
        raise ValueError("distinct evaluation IDs required")
    reference = scorecard(path, reference_id)
    candidate = scorecard(path, candidate_id)
    if reference["snapshotId"] != candidate["snapshotId"]:
        raise ValueError("different snapshots cannot be compared as paired results")
    if reference["usageRestriction"] != candidate["usageRestriction"]:
        raise ValueError("usage restrictions differ")
    directions: dict[str, object] = {}
    for name in ("up", "down"):
        a = reference["directions"][name]
        b = candidate["directions"][name]
        ref_ap = a.get("testAveragePrecision")
        cand_ap = b.get("testAveragePrecision")
        comparable = (
            a.get("status") == "evaluated"
            and b.get("status") == "evaluated"
            and type(ref_ap) in (int, float)
            and type(cand_ap) in (int, float)
        )
        directions[name] = {
            "status": "descriptive_only" if comparable else "not_comparable",
            "referenceTestAP": ref_ap,
            "candidateTestAP": cand_ap,
            "testAPDelta": cand_ap - ref_ap if comparable else None,
        }
    return {
        "schemaVersion": "phase6_frozen_comparison_v1",
        "snapshotId": reference["snapshotId"],
        "referenceEvaluationId": reference_id,
        "candidateEvaluationId": candidate_id,
        "referenceModelVersion": reference["modelVersion"],
        "candidateModelVersion": candidate["modelVersion"],
        "directions": directions,
        "warning": (
            "Shared test observations are not a new holdout. "
            "This descriptive comparison does not justify model selection, "
            "deployment, or live trading."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-db", required=True, type=Path)
    parser.add_argument("--reference-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(compare(args.analysis_db, args.reference_id, args.candidate_id), sort_keys=True))
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"comparison failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
