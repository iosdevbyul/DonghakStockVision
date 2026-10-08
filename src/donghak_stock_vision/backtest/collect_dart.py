"""Explicit opt-in disclosure acquisition; never collects prices or runs research."""

import argparse
import calendar
import gzip
import hashlib
import json
from pathlib import Path

import httpx
from dotenv import dotenv_values

from donghak_stock_vision.backtest.dart_evidence import corporation_mapping
from donghak_stock_vision.backtest.dart_transport import DartEvidenceClient
from donghak_stock_vision.backtest.validation import require


def collect(root: Path, evidence: Path) -> None:
    """Resume the documented company searches and all-company I003 monthly scope.

    The initial official corporation ZIP and guide receipts must already exist.
    Unresolved share classes are never guessed from names or zero-appended codes.
    All responses are additive. Failed cached requests require explicit review.
    """
    universe = tuple(
        json.loads(
            (root / ".data/oos-preparation-20261001/part2-freeze/universe.json").read_text()
        )["tickers"]
    )
    raw = (evidence / "raw/corpCode.zip").read_bytes()
    inventory = json.loads((evidence / "support/initial-retrieval.json").read_text())
    records = [r for r in inventory if r.get("file") == "corpCode.zip"]
    require(
        len(records) == 1 and hashlib.sha256(raw).hexdigest() == records[0]["sha256"],
        "corporation_source_mismatch",
    )
    rows = corporation_mapping(raw, universe).to_dict()["securities"]
    corps = sorted(
        {
            r["matches"][0]["corp_code"]
            for r in rows
            if r["current_status"] == "CURRENT_IDENTITY_VERIFIED"
        }
    )
    with httpx.Client(timeout=45) as http:
        client = DartEvidenceClient(
            dotenv_values(root / ".env").get("DART_API_KEY"), evidence / "raw/requests", http
        )
        for corp in corps:
            client.filings(
                dict(
                    corp_code=corp,
                    bgn_de="20240614",
                    end_de="20250707",
                    last_reprt_at="N",
                    sort="date",
                    sort_mth="asc",
                )
            )
        for year, month in [(2024, m) for m in range(6, 13)] + [(2025, m) for m in range(1, 8)]:
            client.filings(
                dict(
                    bgn_de=f"{year}{month:02}01",
                    end_de=f"{year}{month:02}{calendar.monthrange(year, month)[1]:02}",
                    pblntf_detail_ty="I003",
                    last_reprt_at="N",
                    sort="date",
                    sort_mth="asc",
                )
            )
    # Only sanitized response status counts reach stdout.
    counts: dict[str, int] = {}
    for p in (evidence / "raw/requests").glob("*.gz"):
        code = str(json.loads(gzip.decompress(p.read_bytes())).get("status"))
        counts[code] = counts.get(code, 0) + 1
    print(json.dumps(dict(corporations=len(corps), official_result_counts=counts), sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        collect(args.root, args.evidence)
    except Exception:
        # No exception representation or sensitive client/request traceback.
        raise SystemExit("DART_ACQUISITION_BLOCKED: inspect sanitized receipts") from None


if __name__ == "__main__":
    main()
