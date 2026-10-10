"""The API must never manufacture security-level inference from global metrics."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.stock_analysis import AnalysisPublicationReader


def test_unconfigured_publication_is_unavailable() -> None:
    result = TestClient(create_app()).get("/api/v1/stocks/005930/analysis")
    assert result.status_code == 503


def test_empty_approved_publication_reports_empty_records(tmp_path: Path) -> None:
    path = tmp_path / "published.json"
    path.write_text(
        json.dumps({"schemaVersion": "published_stock_analysis_v1", "sources": []}),
        encoding="utf-8",
    )
    client = TestClient(create_app(analysis_reader=AnalysisPublicationReader.from_path(path)))
    response = client.get("/api/v1/stocks/005930/analysis")
    assert response.status_code == 200
    assert response.json() == {"ticker": "005930", "records": []}
    assert client.get("/api/v1/stocks/nope/analysis").status_code == 422


def test_rejects_unaudited_scored_record(tmp_path: Path) -> None:
    path = tmp_path / "published.json"
    payload = {
        "schemaVersion": "published_stock_analysis_v1",
        "sources": [
            {
                "ticker": "005930",
                "records": [
                    {
                        "id": "unapproved",
                        "ticker": "005930",
                        "analyzedAt": "2026-10-01T18:30:00+09:00",
                        "dataThrough": "2026-10-01",
                        "modelId": "model",
                        "modelVersion": "v1",
                        "direction": "up",
                        "score": 0.8,
                        "validation": "pending",
                        "explanation": None,
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        AnalysisPublicationReader.from_path(path)
