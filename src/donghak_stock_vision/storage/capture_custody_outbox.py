"""Explicit durable local custody outbox; never asserts authenticated acceptance."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from donghak_stock_vision.storage.capture_custody_transport import (
    CustodyTransport,
    submit_capture_receipt,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_custody_outbox (
    raw_id INTEGER PRIMARY KEY REFERENCES raw_capture_receipts(raw_id),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'retry', 'manual_review')),
    last_blocker TEXT,
    last_attempt_at TEXT,
    acknowledgement_sha256 TEXT
);
"""


@dataclass(frozen=True)
class OutboxAttempt:
    raw_id: int
    state: Literal["retry", "manual_review"]
    attempt_count: int
    blocker: str | None


def enqueue_capture_receipt(connection: sqlite3.Connection, raw_id: int) -> None:
    """Idempotently queue only receipts that exist; never reset attempt counts."""
    if raw_id <= 0:
        raise ValueError("raw_id must be positive")
    with connection:
        connection.execute(SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO capture_custody_outbox(raw_id) "
            "SELECT raw_id FROM raw_capture_receipts WHERE raw_id=?",
            (raw_id,),
        )
        if (
            connection.execute(
                "SELECT 1 FROM capture_custody_outbox WHERE raw_id=?", (raw_id,)
            ).fetchone()
            is None
        ):
            raise ValueError("capture receipt does not exist")


def process_outbox(
    connection: sqlite3.Connection,
    destination: Path,
    *,
    transport: CustodyTransport,
    expected_custodian_id: str,
    independently_pinned_ack_sha256: str,
    max_attempts: int = 3,
    limit: int = 100,
) -> tuple[OutboxAttempt, ...]:
    """Explicit processing, with persisted retry budget and no implicit remote success."""
    if not 1 <= max_attempts <= 100 or not 1 <= limit <= 10000:
        raise ValueError("invalid processing limits")
    # No implicit queue creation: callers must enqueue known receipts.
    connection.execute(SCHEMA)
    rows = connection.execute(
        "SELECT raw_id, attempt_count FROM capture_custody_outbox "
        "WHERE state IN ('pending', 'retry') AND attempt_count < ? "
        "ORDER BY raw_id LIMIT ?",
        (max_attempts, limit),
    ).fetchall()
    outcomes: list[OutboxAttempt] = []
    for raw_id, previous in rows:
        review = submit_capture_receipt(
            connection,
            destination,
            raw_id,
            transport=transport,
            expected_custodian_id=expected_custodian_id,
            independently_pinned_ack_sha256=independently_pinned_ack_sha256,
        )
        attempts = previous + 1
        # Matching content alone is never trusted remote acceptance. It is
        # quarantined for independent operator review, not silently retried.
        retriable = review.blockers in (
            ("custody_transport_failed",),
            ("invalid_custody_response",),
        )
        state: Literal["retry", "manual_review"] = (
            "retry" if retriable and attempts < max_attempts else "manual_review"
        )
        blocker = review.blockers[0] if review.blockers else "unverified_custody_claim"
        with connection:
            connection.execute(
                "UPDATE capture_custody_outbox SET attempt_count=?, state=?, "
                "last_blocker=?, last_attempt_at=?, acknowledgement_sha256=? "
                "WHERE raw_id=?",
                (
                    attempts,
                    state,
                    blocker,
                    datetime.now(UTC).isoformat(),
                    review.acknowledgement_sha256,
                    raw_id,
                ),
            )
        outcomes.append(OutboxAttempt(raw_id, state, attempts, blocker))
    return tuple(outcomes)
