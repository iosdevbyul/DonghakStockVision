"""Read-only operator triage of locally recorded custody outbox and lookup state."""

import sqlite3
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CustodyRecoveryItem:
    raw_id: int
    outbox_state: str
    attempt_count: int
    latest_lookup_status: str | None
    action: Literal["review", "lookup", "retry_pending"]
    authenticated_acceptance: bool = False
    inference_authorized: bool = False


def list_custody_recovery_items(
    connection: sqlite3.Connection,
    *,
    after_raw_id: int = 0,
    limit: int = 100,
) -> tuple[CustodyRecoveryItem, ...]:
    """Summarize existing local records without contacting a custodian or changing state.

    A lookup response is always unverified. No review item authorizes replay
    or promotes remote acceptance.
    """
    if after_raw_id < 0 or not 1 <= limit <= 10000:
        raise ValueError("invalid recovery pagination")
    rows = connection.execute(
        "SELECT o.raw_id,o.state,o.attempt_count,"
        "(SELECT e.status FROM capture_custody_lookup_events e "
        "WHERE e.raw_id=o.raw_id ORDER BY e.id DESC LIMIT 1) "
        "FROM capture_custody_outbox o WHERE o.raw_id>? "
        "ORDER BY o.raw_id LIMIT ?",
        (after_raw_id, limit),
    ).fetchall()
    items: list[CustodyRecoveryItem] = []
    for raw_id, state, attempts, lookup_status in rows:
        if state == "manual_review" or lookup_status == "claim_received_unverified":
            action: Literal["review", "lookup", "retry_pending"] = "review"
        elif lookup_status is None or lookup_status == "blocked":
            action = "lookup"
        else:
            action = "retry_pending" if state in ("pending", "retry") else "review"
        items.append(CustodyRecoveryItem(raw_id, state, attempts, lookup_status, action))
    return tuple(items)
