"""Actual offline capture/ingest/model/daily-owner readback through the public API."""

import hashlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.integration.test_daily_research import AS_OF, DAY
from tests.integration.test_daily_research import collected as collected
from tests.integration.test_daily_research import model as model
from tests.integration.test_krx_scheduler import config as config

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.daily_research import DailyResearchReader
from donghak_stock_vision.storage.daily_research import run_daily
from donghak_stock_vision.storage.forward_capture import DB as CAPTURE_DB
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig
from donghak_stock_vision.storage.operational_market import DB as MARKET_DB


def test_verified_capture_to_stored_research_to_read_only_api(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
) -> None:
    sources = [
        collected.path("capture_root") / CAPTURE_DB,
        collected.path("operational_root") / MARKET_DB,
        model[0],
    ]
    before = [p.read_bytes() for p in sources]
    output = tmp_path / "daily.sqlite3"
    retained = run_daily(
        collected,
        output,
        model[0],
        model[1],
        DAY,
        ("000001", "000002"),
        AS_OF,
        allow_synthetic_fixture=True,
    ).to_dict()
    original_output = output.read_bytes()
    pin = hashlib.sha256(original_output).hexdigest()
    with patch(
        "donghak_stock_vision.storage.daily_research._analyze",
        side_effect=AssertionError("API must never infer"),
    ):
        reader = DailyResearchReader(output, expected_sha256=pin)
        client = TestClient(create_app(daily_research_reader=reader))
        response = client.get(
            "/api/v1/stocks/000001/research-analysis", params={"tradingDate": DAY.isoformat()}
        )
    assert response.status_code == 200
    row = response.json()["records"][0]
    assert row["researchScore"] == retained["results"]["000001"]["research_score"]
    assert row["modelVersion"] == model[1]
    assert row["dataOrigin"] == "synthetic_fixture"
    assert row["usageRestriction"] == "synthetic_test_only"
    assert row["observedPeriod"] == {"start": "2025-07-07", "end": DAY.isoformat()}
    assert not row["forwardPredictionEligible"] and not row["pitQualified"]
    assert row["performanceMetrics"] is None
    assert [p.read_bytes() for p in sources] == before
    assert output.read_bytes() == original_output
    assert str(tmp_path) not in response.text
