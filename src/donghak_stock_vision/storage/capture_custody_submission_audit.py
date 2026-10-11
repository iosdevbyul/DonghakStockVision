"""Append-only audit of explicit custody submissions, never of trusted acceptance."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from donghak_stock_vision.storage.capture_custody_idempotent_transport import (
    CustodyTransportObservation,
    IdempotentCustodyTransport,
    observe_idempotent_custody,
)
from donghak_stock_vision.storage.capture_custody_outbox import SCHEMA as OUTBOX_SCHEMA

SUBMISSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_custody_submission_events (
    id INTEGER PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES capture_custody_outbox(raw_id),
    idempotency_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('claim_received_unverified', 'blocked')),
    blocker TEXT,
    observed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS capture_custody_submission_no_update
BEFORE UPDATE ON capture_custody_submission_events
BEGIN SELECT RAISE(ABORT, 'custody submission audit immutable'); END;
CREATE TRIGGER IF NOT EXISTS capture_custody_submission_no_delete
BEFORE DELETE ON capture_custody_submission_events
BEGIN SELECT RAISE(ABORT, 'custody submission audit immutable'); END;
"""


@dataclass(frozen=True)
class AuditedCustodySubmission:
    event_id: int
    observation: CustodyTransportObservation


def submit_outbox_and_audit(
    connection: sqlite3.Connection,
    destination: Path,
    raw_id: int,
    *,
    transport: IdempotentCustodyTransport,
) -> AuditedCustodySubmission:
    """Submit an explicitly queued receipt and audit the unverified outcome.

    This does not change outbox state, increment retries or authorize custody.
    """
    if raw_id <= 0:
        raise ValueError("raw ID must be positive")
    connection.executescript(OUTBOX_SCHEMA)
    connection.executescript(SUBMISSION_SCHEMA)
    row = connection.execute(
        "SELECT state FROM capture_custody_outbox WHERE raw_id=?", (raw_id,)
    ).fetchone()
    if row is None:
        raise ValueError("only queued receipts can be submitted")
    if row[0] == "manual_review":
        raise ValueError("manual review receipts require separate replay authorization")
    observation = observe_idempotent_custody(
        connection, destination, raw_id, transport=transport, operation="submit"
    )
    with connection:
        cursor = connection.execute(
            "INSERT INTO capture_custody_submission_events "
            "(raw_id,idempotency_key,status,blocker,observed_at) VALUES (?,?,?,?,?)",
            (
                raw_id,
                observation.idempotency_key,
                observation.status,
                observation.blockers[0] if observation.blockers else None,
                datetime.now(UTC).isoformat(),
            ),
        )
    if cursor.lastrowid is None:
        raise RuntimeError("custody submission audit event not recorded")
    return AuditedCustodySubmission(cursor.lastrowid, observation)
