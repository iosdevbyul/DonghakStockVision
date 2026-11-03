"""Bundle existing frozen Phase 6 evaluation evidence into a read-only report."""

import argparse
import json
import math
from pathlib import Path
from typing import Any, cast

from phase6_baseline_scorecard import scorecard
from phase6_error_attribution import attribute


def _completeness(baseline: dict[str, object], errors: dict[str, object]) -> dict[str, object]:
    """Fail closed on incomplete or non-finite frozen evaluation evidence."""
    blockers: list[str] = []
    scores = cast(dict[str, dict[str, Any]], baseline["directions"])
    attributions = cast(dict[str, dict[str, Any]], errors["directions"])
    for direction in ("up", "down"):
        score = scores[direction]
        attribution = attributions[direction]
        if score.get("status") != "evaluated":
            blockers.append(f"{direction}_test_not_evaluated")
        ap = score.get("testAveragePrecision")
        if type(ap) not in (int, float) or not math.isfinite(ap) or not 0 <= ap <= 1:
            blockers.append(f"{direction}_test_ap_invalid")
        baselines = score.get("baselineAveragePrecision")
        if not isinstance(baselines, dict) or not baselines:
            blockers.append(f"{direction}_baselines_missing")
        elif any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
            for value in baselines.values()
        ):
            blockers.append(f"{direction}_baseline_ap_invalid")
        margin = score.get("testMarginVsBestBaseline")
        if (
            type(ap) in (int, float)
            and math.isfinite(ap)
            and isinstance(baselines, dict)
            and baselines
            and all(type(v) in (int, float) and math.isfinite(v) for v in baselines.values())
        ):
            expected = ap - max(baselines.values())
            if (
                type(margin) not in (int, float)
                or not math.isfinite(margin)
                or not math.isclose(margin, expected, rel_tol=1e-9, abs_tol=1e-12)
            ):
                blockers.append(f"{direction}_test_margin_inconsistent")
        validation_margin = score.get("validationMargin")
        if validation_margin is not None and (
            type(validation_margin) not in (int, float)
            or not math.isfinite(validation_margin)
        ):
            blockers.append(f"{direction}_validation_margin_invalid")
        if attribution.get("status") != "descriptive_only":
            blockers.append(f"{direction}_error_attribution_unavailable")
    return {
        "complete": not blockers,
        "blockers": blockers,
        "meaning": "Historical evidence completeness only, not model readiness or approval",
    }


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
        "evidenceCompleteness": _completeness(baseline, errors),
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
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit 3 if frozen research evidence is incomplete; still print the report",
    )
    args = parser.parse_args()
    try:
        report = evidence(args.analysis_db, args.evaluation_id)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        if args.require_complete and not report["evidenceCompleteness"]["complete"]:
            return 3
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"evidence report failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
