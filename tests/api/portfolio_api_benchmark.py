"""Bounded queries of explicitly published synthetic archives, no publication/replay."""

import argparse
import json
import platform
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.historical_portfolios import (
    PortfolioPublicationReader,
    read_index,
)
from donghak_stock_vision.backtest.receipt_archive import DEFAULT_LIMITS


def measure(root: Path, pin: str) -> dict[str, Any]:
    reader = PortfolioPublicationReader(root, pin)
    entries = [e for e in read_index(reader.files, pin)["entries"] if not e["revoked"]]
    if not 1 <= len(entries) <= 4:
        raise ValueError("benchmark requires 1–4 published synthetic reports")
    identifier = entries[0]["id"]
    info = reader.summary(identifier)
    queries = {
        "list": "/api/v1/research/historical-portfolios?limit=2",
        "summary": f"/api/v1/research/historical-portfolios/{identifier}",
        "ticker": (
            f"/api/v1/research/historical-portfolios/{identifier}/tickers/"
            f"{info['tickers'][0]}?limit=2"
        ),
        "trades": f"/api/v1/research/historical-portfolios/{identifier}/trades?limit=2",
    }
    rows = []
    for name, url in queries.items():
        observations = []
        for attempt in range(2):
            # Cold = new reader/app, warm = same reader/app and OS cache; no app cache.
            if attempt == 0:
                owner_started = perf_counter()
                http = TestClient(
                    create_app(portfolio_reader=PortfolioPublicationReader(root, pin)),
                    base_url="http://127.0.0.1",
                    client=("127.0.0.1", 50000),
                )
                owner_startup = perf_counter() - owner_started
            started = perf_counter()
            response = http.get(url)
            elapsed = perf_counter() - started
            response.raise_for_status()
            observations.append(
                {
                    "kind": "cold_owner" if attempt == 0 else "warm_owner",
                    "seconds": elapsed,
                    "owner_startup_seconds": owner_startup if attempt == 0 else None,
                    "response_bytes": len(response.content),
                }
            )
        rows.append({"query": name, "observations": observations})
    started = perf_counter()
    reader.files.digest(f"{identifier}.archive.json", DEFAULT_LIMITS.transport_bytes)
    integrity = perf_counter() - started
    return {
        "published_archives": len(entries),
        "model_family": info["modelFamily"],
        "fill_count": info["fillCount"],
        "ticker_count": len(info["tickers"]),
        "queries": rows,
        "archive_integrity_recheck_seconds": integrity,
        "archive_bytes": (root / f"{identifier}.archive.json").stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("index_sha256")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "scope": "synthetic_loopback_TestClient_no_production_SLA",
        "measurement": measure(args.root, args.index_sha256),
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
