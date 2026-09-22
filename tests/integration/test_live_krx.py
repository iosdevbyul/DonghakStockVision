"""Explicit opt-in only. Run after KRX grants both key and market API access."""

import os
from datetime import date
from pathlib import Path

import httpx
import pytest

from donghak_stock_vision.ingestion.pipeline import Pipeline
from donghak_stock_vision.providers.http import RequestGate, RetryingHTTP
from donghak_stock_vision.providers.krx import KRXProvider
from donghak_stock_vision.storage.sqlite import SQLiteStore


@pytest.mark.live
def test_approved_krx_round_trip(tmp_path: Path) -> None:
    if os.getenv("DSV_RUN_LIVE") != "1":
        pytest.skip("set DSV_RUN_LIVE=1 and export an approved KRX_API_KEY")
    key = os.getenv("KRX_API_KEY")
    if not key:
        pytest.fail("KRX_API_KEY required when DSV_RUN_LIVE=1")
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        provider = KRXProvider(key, RetryingHTTP(client, RequestGate(tmp_path / "quota.db")))
        store = SQLiteStore(tmp_path / "market.db")
        try:
            pipeline = Pipeline(provider, store)
            day = date(2024, 1, 2)
            result = pipeline.collect(["005930"], day, day)
            assert result.failed == 0 and result.changed == 1
            assert pipeline.collect(["005930"], day, day).changed == 0
            assert len(store.read("005930", day, day)) == 1
            assert store.check_integrity() == []
        finally:
            store.close()
