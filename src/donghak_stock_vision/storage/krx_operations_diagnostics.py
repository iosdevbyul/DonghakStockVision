"""Bounded, read-only KRX operations dashboard from existing owner reports."""

from collections.abc import Mapping
from typing import Any


def summarize(
    backlog: Mapping[str, Any],
    preflight: Mapping[str, Any],
    daily_research: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project existing evidence without changing authorization or scheduler state."""
    due = backlog["due_dates"]
    expired = backlog["expired_dates"]
    reserved = backlog["reserved_without_verified_completion"]
    unverified = backlog["stored_without_verified_history"]
    blockers = preflight["blockers"]
    if not all(isinstance(value, list) for value in (due, expired, reserved, unverified, blockers)):
        raise ValueError("invalid scheduler diagnostic source")
    pending = backlog["pending_phase"]
    enabled = backlog["enabled"]
    if type(enabled) is not bool:
        raise ValueError("invalid scheduler enablement")
    if pending is not None:
        status = "pending_review"
    elif unverified or reserved:
        status = "evidence_review_required"
    elif expired:
        status = "expired_sessions"
    elif blockers:
        status = "preflight_blocked"
    elif due:
        status = "collection_due"
    else:
        status = "no_due_sessions"
    result = {
        "version": "krx_operations_diagnostics_v1",
        "status": status,
        "schedulerEnabled": enabled,
        "lastPersistedTradingDate": backlog["latest_persisted_trading_date"],
        "storedObservations": backlog["stored_observations"],
        "completedSessions": len(backlog["completed"]),
        "dueSessions": len(due),
        "expiredSessions": len(expired),
        "reservedWithoutVerifiedCompletion": len(reserved),
        "storedWithoutVerifiedHistory": len(unverified),
        "oldestDueDate": backlog["oldest_due_date"],
        "pendingPhase": pending,
        "storageCapacityReady": backlog["storage_capacity_ready"],
        "preflightBlockers": blockers,
        "readyForManualCapture": preflight["ready_for_manual_provider_capture"],
        "readyForScheduledCollection": preflight["ready_for_scheduled_collection"],
        "operatorConfirmationStillRequired": True,
        "networkRequests": 0,
        "schedulerChanged": False,
        "paperExecutionEligible": False,
    }

    if daily_research is not None:
        if (
            daily_research.get("version") != "daily_research_v1"
            or daily_research.get("future_prediction_recording_available") is not False
        ):
            raise ValueError("invalid daily research diagnostic source")
        result["dailyResearch"] = {
            key: daily_research[key]
            for key in (
                "day",
                "analysis_status",
                "completed_tickers",
                "failed_tickers",
                "blocked_tickers",
                "pending_tickers",
                "collection_status",
                "storage_status",
                "input_latest_trading_date",
                "input_age_calendar_days",
                "last_successful_analysis_day",
                "model_version",
                "data_origin",
                "usage_restriction",
                "future_prediction_recording_available",
                "future_prediction_blocker",
            )
        }
    return result
