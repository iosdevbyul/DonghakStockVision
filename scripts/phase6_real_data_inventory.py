"""Read-only inventory of an existing Phase 1 market database; never fetches data."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


def inventory(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError("market database does not exist")
    digest_before = hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"bars", "bar_sources", "raw_pages", "collection_runs"} <= tables:
            raise ValueError("not a Phase 1 market database")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("foreign key check failed")
        counts: Counter[str] = Counter()
        markets: Counter[str] = Counter()
        first_day: str | None = None
        last_day: str | None = None
        tickers: set[str] = set()
        bad_rows = 0
        for ticker, day, payload in db.execute(
            "SELECT ticker, trading_date, payload FROM bars ORDER BY ticker, trading_date"
        ):
            try:
                bar = json.loads(payload)
                if bar["ticker"] != ticker or bar["trading_date"] != day:
                    raise ValueError("bar identity mismatch")
                provider = bar["provider"]
                market = bar["market"]
                adjustment = bar["adjustment"]
                if not isinstance(provider, str) or not isinstance(market, str):
                    raise ValueError("invalid provenance")
                if not isinstance(adjustment, str):
                    raise ValueError("invalid adjustment")
                counts[f"{provider}/{adjustment}"] += 1
                markets[market] += 1
                tickers.add(ticker)
                first_day = min(first_day, day) if first_day else day
                last_day = max(last_day, day) if last_day else day
            except (ValueError, TypeError, KeyError):
                bad_rows += 1
        missing_sources = db.execute(
            "SELECT COUNT(*) FROM bars b WHERE NOT EXISTS "
            "(SELECT 1 FROM bar_sources s "
            "WHERE s.ticker=b.ticker AND s.trading_date=b.trading_date)"
        ).fetchone()[0]
        runs = dict(
            db.execute("SELECT status, COUNT(*) FROM collection_runs GROUP BY status")
        )
        raw_pages = db.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0]
    digest_after = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest_before != digest_after:
        raise ValueError("database changed during inspection; use a frozen copy")
    return {
        "schemaVersion": "phase6_real_data_inventory_v1",
        "sha256": digest_before,
        "rowCount": sum(counts.values()),
        "tickerCount": len(tickers),
        "firstTradingDate": first_day,
        "lastTradingDate": last_day,
        "providerAdjustmentRows": dict(sorted(counts.items())),
        "marketRows": dict(sorted(markets.items())),
        "collectionRunStatuses": runs,
        "rawPageCount": raw_pages,
        "invalidIdentityRows": bad_rows,
        "missingProvenanceRows": missing_sources,
        "realDataPresent": any(k.startswith("krx/") for k in counts),
        "readyForModelEvaluation": False,
        "evaluationGate": "Review sample counts, quality manifest, PIT and corporate actions separately",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(inventory(args.db), sort_keys=True))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"inventory failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
