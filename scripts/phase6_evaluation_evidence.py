"""Bundle existing frozen Phase 6 evaluation evidence into a read-only report."""

import argparse
import json
from pathlib import Path

from phase6_baseline_scorecard import scorecard
from phase6_error_attribution import attribute


def evidence(path: Path, evaluation_id: str) -> dict[str, object]:
    baseline = scorecard(path, evaluation_id)
    errors = attribute(path, evaluation_id)
    if (
        baseline["evaluationId"] != errors["evaluationId"]
        or baseline["snapshotId"] != errors["snapshotId"]
        or baseline["modelVersion"] != errors["modelVersion"]
    ):
        raise ValueError("evaluation evidence identity mismatch")
    return {
        "schemaVersion": "phase6_evaluation_evidence_v1",
        "evaluationId": evaluation_id,
        "modelVersion": baseline["modelVersion"],
        "snapshotId": baseline["snapshotId"],
        "baseline": baseline,
        "errors": errors,
        "limitations": (
            "Historical research evidence only. Repeated inspection of the frozen "
            "holdout is not independent validation and cannot authorize trading."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-db", type=Path, required=True)
    parser.add_argument("--evaluation-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(evidence(args.analysis_db, args.evaluation_id), sort_keys=True))
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"evidence report failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
