"""Read-only audit of explicitly selected frozen Phase 6 research evaluations."""

import argparse
import json
from pathlib import Path
from typing import Any

from phase6_evaluation_evidence import evidence


def audit(path: Path, evaluation_ids: list[str]) -> dict[str, Any]:
    if not evaluation_ids:
        raise ValueError("at least one evaluation ID required")
    if len(evaluation_ids) != len(set(evaluation_ids)):
        raise ValueError("duplicate evaluation IDs")
    if any(not item.strip() for item in evaluation_ids):
        raise ValueError("empty evaluation ID")
    reports = [evidence(path, evaluation_id) for evaluation_id in evaluation_ids]
    complete = sum(
        report["evidenceCompleteness"]["complete"] is True for report in reports
    )
    return {
        "schemaVersion": "phase6_evidence_batch_v1",
        "evaluationCount": len(reports),
        "completeCount": complete,
        "incompleteCount": len(reports) - complete,
        "allComplete": complete == len(reports),
        "reports": reports,
        "limitations": (
            "Historical descriptive evidence only. Comparing reports is not "
            "independent forward validation or authorization to trade."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-db", type=Path, required=True)
    parser.add_argument("--evaluation-id", action="append", required=True)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit 3 when any selected evaluation has incomplete evidence",
    )
    args = parser.parse_args()
    try:
        result = audit(args.analysis_db, args.evaluation_id)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        if args.require_complete and not result["allComplete"]:
            return 3
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"batch evidence audit failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
