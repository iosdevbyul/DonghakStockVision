"""Read-only Phase 6 error attribution using frozen test confusion matrices."""

import argparse
import json
from pathlib import Path
from typing import Any

from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore


def _counts(metrics: dict[str, Any]) -> dict[str, int] | None:
    matrix = metrics.get("confusion_matrix")
    if not isinstance(matrix, dict):
        return None
    keys = ("tp", "fp", "tn", "fn")
    if any(type(matrix.get(key)) is not int or matrix[key] < 0 for key in keys):
        return None
    return {key: matrix[key] for key in keys}


def attribute(path: Path, evaluation_id: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("analysis database does not exist")
    store = ReadOnlyAnalysisStore(path)
    try:
        evaluation = store.get("evaluation", evaluation_id)
        if (
            evaluation.get("mode") != "historical_research"
            or evaluation.get("operational_status") != "unregistered"
        ):
            raise ValueError("research-only frozen evaluation required")
        output: dict[str, Any] = {}
        for direction in ("up", "down"):
            entry = evaluation["directions"][direction]
            test = entry.get("test")
            if not isinstance(test, dict) or entry.get("test_status") != "evaluated":
                output[direction] = {"status": "unavailable", "tickers": []}
                continue
            aggregate = _counts(test)
            by_ticker = test.get("by_ticker")
            if aggregate is None or not isinstance(by_ticker, dict):
                output[direction] = {"status": "unavailable", "tickers": []}
                continue
            tickers = []
            for ticker, metrics in sorted(by_ticker.items()):
                if not isinstance(ticker, str) or not isinstance(metrics, dict):
                    raise ValueError("invalid ticker metrics")
                counts = _counts(metrics)
                if counts is None:
                    raise ValueError("invalid ticker confusion matrix")
                mistakes = counts["fp"] + counts["fn"]
                n = sum(counts.values())
                tickers.append({
                    "ticker": ticker,
                    "samples": n,
                    "falsePositives": counts["fp"],
                    "falseNegatives": counts["fn"],
                    "mistakes": mistakes,
                    "mistakeRate": mistakes / n if n else None,
                })
            # Check that per-ticker evidence covers the immutable aggregate.
            if (
                sum(row["falsePositives"] for row in tickers) != aggregate["fp"]
                or sum(row["falseNegatives"] for row in tickers) != aggregate["fn"]
                or sum(row["samples"] for row in tickers) != sum(aggregate.values())
            ):
                raise ValueError("ticker attribution does not reconcile with test totals")
            output[direction] = {
                "status": "descriptive_only",
                "falsePositives": aggregate["fp"],
                "falseNegatives": aggregate["fn"],
                "tickers": sorted(tickers, key=lambda x: (-x["mistakes"], x["ticker"])),
            }
        return {
            "schemaVersion": "phase6_error_attribution_v1",
            "evaluationId": evaluation_id,
            "modelVersion": evaluation["model_version"],
            "snapshotId": evaluation["snapshot_id"],
            "directions": output,
            "warning": "Frozen historical errors only; no causal attribution or live performance claim.",
        }
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-db", type=Path, required=True)
    parser.add_argument("--evaluation-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(attribute(args.analysis_db, args.evaluation_id), sort_keys=True))
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"error attribution failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
