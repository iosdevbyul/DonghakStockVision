"""Offline Track C E2E: pinned market snapshot and KRX diagnostics stay read-only."""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.api import create_app
from donghak_stock_vision.api.ohlcv import OperationalReadError, PinnedOperationalReader
from tests.api.test_krx_scheduler_diagnostics import reports
from tests.api.test_operational_market_read import make_snapshot


def test_pinned_snapshot_and_diagnostics_consistent_and_unreviewed(tmp_path: Path) -> None:
    path = tmp_path / "market.snapshot.sqlite3"
    sha = make_snapshot(path)
    client = TestClient(
        create_app(
            operational_reader=PinnedOperationalReader(path, expected_sha256=sha),
            krx_diagnostics_reports=reports,
        )
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    summary = client.get("/api/v1/operational/summary")
    diagnostics = client.get("/api/v1/operations/krx-scheduler/diagnostics")
    catalog = client.get("/api/v1/operational/stocks")
    bars = client.get("/api/v1/operational/stocks/005930/ohlcv")

    assert all(response.status_code == 200 for response in (summary, diagnostics, catalog, bars))
    assert summary.json()["lastTradingDate"] == "2026-10-29"
    assert summary.json()["evidenceStatus"] == "unreviewed"
    assert catalog.json()["items"][0]["latestDataDate"] == "2026-10-29"
    assert bars.json()["bars"][0]["date"] == "2026-10-29"
    assert diagnostics.json()["dueSessions"] == 1
    assert diagnostics.json()["schedulerEnabled"] is False
    assert diagnostics.json()["paperExecutionEligible"] is False
    assert diagnostics.json()["networkRequests"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_missing_diagnostics_provider_remains_explicitly_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "market.snapshot.sqlite3"
    sha = make_snapshot(path)
    client = TestClient(
        create_app(operational_reader=PinnedOperationalReader(path, expected_sha256=sha))
    )
    assert client.get("/api/v1/operational/summary").status_code == 200
    response = client.get("/api/v1/operations/krx-scheduler/diagnostics")
    assert response.status_code == 503
    assert response.json()["detail"] == "KRX diagnostics not configured"


def test_snapshot_tampering_never_promotes_evidence(tmp_path: Path) -> None:
    path = tmp_path / "market.snapshot.sqlite3"
    sha = make_snapshot(path)
    reader = PinnedOperationalReader(path, expected_sha256=sha)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(OperationalReadError, match="fingerprint"):
        reader.catalog(0, 25)
