"""Screener must publish existing records without generating predictions."""

from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.stock_analysis import (
    AnalysisPublication,
    AnalysisPublicationReader,
)


def test_unconfigured_screener_returns_503() -> None:
    assert TestClient(create_app()).get("/api/v1/analysis/screener").status_code == 503


def test_empty_published_screener_returns_no_predictions() -> None:
    publication = AnalysisPublication.model_validate(
        {"schemaVersion": "published_stock_analysis_v1", "sources": []}
    )
    client = TestClient(create_app(analysis_reader=AnalysisPublicationReader(publication)))
    response = client.get("/api/v1/analysis/screener")
    assert response.status_code == 200
    assert response.json() == {"entries": []}


def test_screener_preserves_verified_published_record() -> None:
    publication = AnalysisPublication.model_validate(
        {
            "schemaVersion": "published_stock_analysis_v1",
            "sources": [
                {
                    "ticker": "005930",
                    "records": [
                        {
                            "id": "reviewed-run-1",
                            "ticker": "005930",
                            "analyzedAt": "2026-10-01T18:30:00+09:00",
                            "dataThrough": "2026-10-01",
                            "modelId": "approved-model",
                            "modelVersion": "immutable-v1",
                            "direction": "up",
                            "score": 0.75,
                            "validation": "verified",
                            "explanation": None,
                        }
                    ],
                }
            ],
        }
    )
    client = TestClient(create_app(analysis_reader=AnalysisPublicationReader(publication)))
    response = client.get("/api/v1/analysis/screener")
    assert response.status_code == 200
    assert response.json()["entries"] == [
        {
            "ticker": "005930",
            "name": None,
            "analysisId": "reviewed-run-1",
            "modelId": "approved-model",
            "modelVersion": "immutable-v1",
            "direction": "up",
            "score": 0.75,
            "validation": "verified",
            "dataThrough": "2026-10-01",
            "analyzedAt": "2026-10-01T18:30:00+09:00",
        }
    ]
