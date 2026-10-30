"""Read-only KRX operations API requires an explicitly injected report provider."""

from fastapi.testclient import TestClient

from donghak_stock_vision.api import create_app


def reports() -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "due_dates": ["2026-10-29"],
            "expired_dates": [],
            "reserved_without_verified_completion": [],
            "stored_without_verified_history": [],
            "pending_phase": None,
            "enabled": False,
            "latest_persisted_trading_date": "2026-10-28",
            "stored_observations": 940,
            "completed": [],
            "oldest_due_date": "2026-10-29",
            "storage_capacity_ready": True,
        },
        {
            "blockers": [],
            "ready_for_manual_provider_capture": False,
            "ready_for_scheduled_collection": False,
        },
    )


def test_diagnostics_is_unavailable_without_explicit_provider() -> None:
    response = TestClient(create_app()).get("/api/v1/operations/krx-scheduler/diagnostics")
    assert response.status_code == 503
    assert response.json()["detail"] == "KRX diagnostics not configured"


def test_diagnostics_uses_existing_read_only_summary() -> None:
    calls = 0

    def provider() -> tuple[dict[str, object], dict[str, object]]:
        nonlocal calls
        calls += 1
        return reports()

    response = TestClient(create_app(krx_diagnostics_reports=provider)).get(
        "/api/v1/operations/krx-scheduler/diagnostics"
    )
    assert response.status_code == 200
    payload = response.json()
    assert calls == 1
    assert payload["version"] == "krx_operations_diagnostics_v1"
    assert payload["status"] == "collection_due"
    assert payload["dueSessions"] == 1
    assert payload["storedObservations"] == 940
    assert payload["schedulerEnabled"] is False
    assert payload["operatorConfirmationStillRequired"] is True
    assert payload["networkRequests"] == 0
    assert payload["schedulerChanged"] is False
    assert payload["paperExecutionEligible"] is False


def test_diagnostics_rejects_state_mutation_methods() -> None:
    client = TestClient(create_app(krx_diagnostics_reports=reports))
    assert client.post("/api/v1/operations/krx-scheduler/diagnostics").status_code == 405
