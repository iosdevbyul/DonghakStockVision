from pathlib import Path

from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.snapshot import SnapshotReader
from tests.api.helpers import write_snapshot


def test_health_does_not_require_research_snapshot() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "DonghakStockVision",
        "mode": "read_only",
    }


def test_data_routes_fail_closed_without_reader() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/project/status")

    assert response.status_code == 503
    assert response.json() == {"detail": "research snapshot reader is not configured"}


def test_snapshot_routes_match_frontend_contract(tmp_path: Path) -> None:
    reader = SnapshotReader.from_path(write_snapshot(tmp_path / "snapshot.json"))
    client = TestClient(create_app(reader))

    project = client.get("/api/v1/project/status")
    coverage = client.get("/api/v1/research/coverage")
    models = client.get("/api/v1/models")
    evidence = client.get("/api/v1/evidence")
    baseline = client.get("/api/v1/backtests/baseline")
    stocks = client.get("/api/v1/stocks")

    assert project.status_code == 200
    assert project.json()["productName"] == "Tris Market Lens"
    assert coverage.status_code == 200
    assert coverage.json()["unexplainedTickerSessions"] == 8_118
    assert models.json() == []
    assert evidence.json() == []
    assert baseline.json()["performanceAvailable"] is False
    assert stocks.json() == []


def test_detail_routes_return_not_found_when_snapshot_has_no_match(tmp_path: Path) -> None:
    reader = SnapshotReader.from_path(write_snapshot(tmp_path / "snapshot.json"))
    client = TestClient(create_app(reader))

    assert client.get("/api/v1/models/up").status_code == 404
    assert client.get("/api/v1/evidence/calendar").status_code == 404
    assert client.get("/api/v1/stocks/005930").status_code == 404


def test_stock_route_rejects_malformed_ticker_before_reader_lookup(tmp_path: Path) -> None:
    reader = SnapshotReader.from_path(write_snapshot(tmp_path / "snapshot.json"))
    client = TestClient(create_app(reader))

    response = client.get("/api/v1/stocks/not-a-ticker")

    assert response.status_code == 422


def test_local_frontend_origin_is_allowed(tmp_path: Path) -> None:
    reader = SnapshotReader.from_path(write_snapshot(tmp_path / "snapshot.json"))
    client = TestClient(create_app(reader))

    response = client.get(
        "/api/v1/project/status",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
