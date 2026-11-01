"""Conservative read-only sample coverage gate for Phase 6 research."""

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from phase6_real_data_inventory import inventory


def assess(path: Path) -> dict[str, object]:
    report = inventory(path)
    samples: dict[str, set[str]] = defaultdict(set)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        for ticker, day, payload in db.execute(
            "SELECT ticker, trading_date, payload FROM bars ORDER BY ticker, trading_date"
        ):
            bar = json.loads(payload)
            if (
                bar.get("provider") == "krx"
                and bar.get("adjustment") == "unadjusted"
                and bar.get("ticker") == ticker
                and bar.get("trading_date") == day
                and isinstance(day, str)
            ):
                samples[ticker].add(day)
    eligible = {ticker: dates for ticker, dates in samples.items() if len(dates) >= 504}
    distinct_days = len(set().union(*eligible.values())) if eligible else 0
    coverage_ok = len(eligible) >= 5 and distinct_days >= 504
    blockers = []
    if report["invalidIdentityRows"] or report["missingProvenanceRows"]:
        blockers.append("data_identity_or_provenance_invalid")
    if len(eligible) < 5:
        blockers.append("fewer_than_five_tickers_with_504_observations")
    if distinct_days < 504:
        blockers.append("fewer_than_504_observed_dates")
    blockers.extend(
        ["trading_calendar_unverified", "corporate_actions_unverified",
         "pit_availability_unverified", "directional_label_balance_unmeasured"]
    )
    return {
        "schemaVersion": "phase6_coverage_gate_v1",
        "sourceSha256": report["sha256"],
        "candidateTickerCount": len(eligible),
        "distinctObservedDates": distinct_days,
        "candidateTickers": sorted(eligible),
        "preliminaryCoverageSatisfied": coverage_ok,
        "readyForModelEvaluation": False,
        "blockers": blockers,
        "note": "Counts are preliminary. 504 observed rows do not establish 504 eligible sessions. "
                "Training, validation and test balance must be assessed using the existing "
                "learning pipeline and an independently reviewed quality manifest.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(assess(args.db), sort_keys=True))
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        parser.exit(2, f"coverage assessment failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
