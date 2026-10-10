import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.ohlcv import OhlcvMarketReader, OhlcvReaderError
from donghak_stock_vision.data.schema import DailyBar


def make_database(path: Path) -> str:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE bars (ticker TEXT, trading_date TEXT, payload TEXT)")
        bar = DailyBar(
            ticker="005930",
            trading_date=date(2025, 6, 30),
            open=100,
            high=110,
            low=90,
            close=105,
            volume=7,
            trading_value=735,
            provider="krx",
            market="KOSPI",
            adjustment="unadjusted",
            collected_at=datetime(2025, 7, 1, tzinfo=UTC),
        )
        db.execute(
            "INSERT INTO bars VALUES (?, ?, ?)",
            (bar.ticker, bar.trading_date.isoformat(), json.dumps(bar.to_dict())),
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ohlcv_requires_explicit_market_reader() -> None:
    assert TestClient(create_app()).get("/api/v1/stocks/005930/ohlcv").status_code == 503


def test_ohlcv_uses_exact_original_bars(tmp_path: Path) -> None:
    path = tmp_path / "market.sqlite3"
    fingerprint = make_database(path)
    reader = OhlcvMarketReader(path, expected_sha256=fingerprint)
    response = TestClient(create_app(ohlcv_reader=reader)).get("/api/v1/stocks/005930/ohlcv")
    assert response.status_code == 200
    assert response.json() == {
        "ticker": "005930",
        "bars": [
            {"date": "2025-06-30", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 7}
        ],
    }


def test_ohlcv_rejects_unknown_and_invalid_ticker(tmp_path: Path) -> None:
    path = tmp_path / "market.sqlite3"
    reader = OhlcvMarketReader(path, expected_sha256=make_database(path))
    client = TestClient(create_app(ohlcv_reader=reader))
    assert client.get("/api/v1/stocks/999999/ohlcv").status_code == 404
    assert client.get("/api/v1/stocks/not-valid/ohlcv").status_code == 422


def test_ohlcv_rejects_modified_database(tmp_path: Path) -> None:
    path = tmp_path / "market.sqlite3"
    old_sha = make_database(path)
    with path.open("ab") as file:
        file.write(b"changed")
    with pytest.raises(OhlcvReaderError, match="fingerprint"):
        OhlcvMarketReader(path, expected_sha256=old_sha)
