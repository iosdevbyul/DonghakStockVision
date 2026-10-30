"""KRX diagnostics are projections, not execution or policy authorization."""

from copy import deepcopy

import pytest

from donghak_stock_vision.storage.krx_operations_diagnostics import summarize


def reports() -> tuple[dict[str, object], dict[str, object]]:
    backlog: dict[str, object] = {
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
    }
    preflight: dict[str, object] = {
        "blockers": [],
        "ready_for_manual_provider_capture": False,
        "ready_for_scheduled_collection": False,
    }
    return backlog, preflight


def test_due_session_does_not_authorize_collection() -> None:
    backlog, preflight = reports()
    original = deepcopy((backlog, preflight))
    result = summarize(backlog, preflight)
    assert result["status"] == "collection_due"
    assert result["dueSessions"] == 1
    assert result["networkRequests"] == 0
    assert result["schedulerChanged"] is False
    assert result["paperExecutionEligible"] is False
    assert (backlog, preflight) == original


@pytest.mark.parametrize(
    ("field", "value", "status"),
    [
        ("pending_phase", "capture_pending", "pending_review"),
        ("stored_without_verified_history", ["2026-10-28"], "evidence_review_required"),
        ("reserved_without_verified_completion", ["2026-10-28"], "evidence_review_required"),
        ("expired_dates", ["2026-10-27"], "expired_sessions"),
    ],
)
def test_alert_precedence(field: str, value: object, status: str) -> None:
    backlog, preflight = reports()
    backlog[field] = value
    assert summarize(backlog, preflight)["status"] == status


def test_readiness_blocker_and_empty_due() -> None:
    backlog, preflight = reports()
    preflight["blockers"] = ["policy_not_ready"]
    assert summarize(backlog, preflight)["status"] == "preflight_blocked"
    assert summarize(backlog, preflight)["preflightBlockers"] == ["policy_not_ready"]
    preflight["blockers"] = []
    backlog["due_dates"] = []
    assert summarize(backlog, preflight)["status"] == "no_due_sessions"


def test_invalid_enablement_fails_closed() -> None:
    backlog, preflight = reports()
    backlog["enabled"] = 1
    with pytest.raises(ValueError, match="enablement"):
        summarize(backlog, preflight)
