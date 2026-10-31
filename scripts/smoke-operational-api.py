"""Exercise the pinned operational-only API contract without opening a network port."""

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from donghak_stock_vision.api import create_app
from donghak_stock_vision.api.ohlcv import PinnedOperationalReader


def verify(path: Path, fingerprint: str) -> dict[str, object]:
    reader = PinnedOperationalReader(path, expected_sha256=fingerprint)
    client = TestClient(create_app(operational_reader=reader), raise_server_exceptions=True)

    health = client.get("/api/v1/health")
    assert health.status_code == 200 and health.json()["mode"] == "read_only"
    summary_response = client.get("/api/v1/operational/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["schemaVersion"] == "operational_snapshot_summary_v1"
    assert summary["evidenceStatus"] == "unreviewed"
    assert summary["market"] == "KOSPI"
    assert type(summary["tickerCount"]) is int and summary["tickerCount"] >= 0
    assert type(summary["observationCount"]) is int and summary["observationCount"] >= 0

    catalog_response = client.get("/api/v1/operational/stocks", params={"limit": 25, "offset": 0})
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    assert catalog["schemaVersion"] == "operational_market_v1"
    assert catalog["evidenceStatus"] == "unreviewed"
    assert catalog["total"] == summary["tickerCount"]
    assert len(catalog["items"]) == min(25, catalog["total"])
    if catalog["items"]:
        ticker = catalog["items"][0]["ticker"]
        bars_response = client.get(f"/api/v1/operational/stocks/{ticker}/ohlcv")
        assert bars_response.status_code == 200
        bars = bars_response.json()
        assert bars["ticker"] == ticker and len(bars["bars"]) > 0
        latest = bars["bars"][-1]["date"]
        assert latest == catalog["items"][0]["latestDataDate"]

    assert client.get("/api/v1/stocks").status_code == 503
    return {
        "schemaVersion": "operational_api_smoke_v1",
        "status": "passed",
        "evidenceStatus": "unreviewed",
        "tickerCount": summary["tickerCount"],
        "observationCount": summary["observationCount"],
        "tradingDayCount": summary["tradingDayCount"],
        "firstTradingDate": summary["firstTradingDate"],
        "lastTradingDate": summary["lastTradingDate"],
        "networkRequests": 0,
        "paperExecutionEligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.snapshot, args.sha256), sort_keys=True))


if __name__ == "__main__":
    main()
