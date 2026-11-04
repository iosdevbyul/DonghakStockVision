"""Replay validation predictions and observed labels without changing frozen evidence."""

import argparse
import json
import sqlite3
from pathlib import Path

from donghak_stock_vision.storage.prediction_outcomes import prediction_outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-db", type=Path, required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--direction", choices=("up", "down"), required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-source-bars", type=int, default=100_000)
    args = parser.parse_args()
    try:
        result = prediction_outcomes(
            args.analysis_db,
            evaluation_id=args.evaluation_id,
            model_version=args.model_version,
            snapshot_id=args.snapshot_id,
            direction=args.direction,
            offset=args.offset,
            limit=args.limit,
            max_source_bars=args.max_source_bars,
        )
    except (ValueError, KeyError, TypeError, AttributeError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"prediction replay failed: {error}\n")
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
