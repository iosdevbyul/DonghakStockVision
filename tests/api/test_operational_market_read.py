import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.ohlcv import OperationalReadError, PinnedOperationalReader
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.storage.sqlite import OPERATIONAL_APPLICATION_ID


def make_snapshot(path: Path) -> str:
    bar = DailyBar(
        ticker="005930",
        trading_date=date(2026, 10, 29),
        open=100,
        high=110,
        low=90,
        close=105,
        volume=7,
        trading_value=735,
        provider="krx",
        market="KOSPI",
        adjustment="unadjusted",
        collected_at=datetime(2026, 10, 30, tzinfo=UTC),
    )
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA application_id={OPERATIONAL_APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
        db.execute(
            "CREATE TABLE market_observations "
            "(market TEXT, ticker TEXT, trading_date TEXT, payload TEXT, validation_status TEXT)"
        )
        db.execute(
            "INSERT INTO market_observations VALUES(?,?,?,?,?)",
            ("KOSPI", "005930", "2026-10-29", json.dumps(bar.to_dict()), "validated"),
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_unconfigured_operational_routes_are_unavailable() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/operational/stocks").status_code == 503
    assert client.get("/api/v1/operational/stocks/005930/ohlcv").status_code == 503


def test_pinned_operational_catalog_and_exact_bars(tmp_path: Path) -> None:
    path = tmp_path / "operational.snapshot.sqlite3"
    fingerprint = make_snapshot(path)
    reader = PinnedOperationalReader(path, expected_sha256=fingerprint)
    client = TestClient(create_app(operational_reader=reader))
    catalog = client.get("/api/v1/operational/stocks?offset=0&limit=25")
    assert catalog.status_code == 200
    assert catalog.json() == {
        "schemaVersion": "operational_market_v1",
        "evidenceStatus": "unreviewed",
        "items": [{"ticker": "005930", "latestDataDate": "2026-10-29"}],
        "total": 1,
        "offset": 0,
        "limit": 25,
    }
    result = client.get("/api/v1/operational/stocks/005930/ohlcv")
    assert result.status_code == 200
    assert result.json()["bars"] == [
        {
            "date": "2026-10-29",
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 105,
            "volume": 7,
        }
    ]
    assert client.get("/api/v1/operational/stocks/999999/ohlcv").status_code == 404
    assert client.get("/api/v1/operational/stocks/bad/ohlcv").status_code == 422


def test_requires_offline_pinned_copy_and_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "operational.sqlite3"
    sha = make_snapshot(path)
    with pytest.raises(OperationalReadError, match="offline"):
        PinnedOperationalReader(path, expected_sha256=sha)
    snapshot = tmp_path / "operational.snapshot.sqlite3"
    path.rename(snapshot)
    with pytest.raises(OperationalReadError, match="fingerprint"):
        PinnedOperationalReader(snapshot, expected_sha256="0" * 64)
    reader = PinnedOperationalReader(snapshot, expected_sha256=sha)
    with snapshot.open("ab") as out:
        out.write(b"tamper")
    with pytest.raises(OperationalReadError, match="fingerprint"):
        reader.catalog(0, 25)


def test_operational_only_cli_serves_real_observations_without_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision import api_cli

    path = tmp_path / "operational.snapshot.sqlite3"
    fingerprint = make_snapshot(path)
    monkeypatch.delenv("DSV_API_SNAPSHOT_PATH", raising=False)
    captured: list[FastAPI] = []

    def serve(app: FastAPI, **kwargs: object) -> None:
        captured.append(app)

    monkeypatch.setattr("uvicorn.run", serve)
    api_cli.main(
        [
            "--operational-only",
            "--operational-snapshot",
            str(path),
            "--operational-snapshot-sha256",
            fingerprint,
        ]
    )
    assert len(captured) == 1
    client = TestClient(captured[0])
    assert client.get("/api/v1/operational/stocks").json()["total"] == 1
    assert client.get("/api/v1/operational/stocks/005930/ohlcv").status_code == 200
    assert client.get("/api/v1/stocks").status_code == 503


def test_operational_only_cli_rejects_mixed_research_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision import api_cli

    monkeypatch.delenv("DSV_API_SNAPSHOT_PATH", raising=False)
    snapshot = tmp_path / "operational.snapshot.sqlite3"
    with pytest.raises(SystemExit) as missing:
        api_cli.main(["--operational-only"])
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as mixed:
        api_cli.main(
            [
                "--operational-only",
                "--operational-snapshot",
                str(snapshot),
                "--operational-snapshot-sha256",
                "0" * 64,
                "--analysis-publication",
                str(tmp_path / "analysis.json"),
            ]
        )
    assert mixed.value.code == 2
