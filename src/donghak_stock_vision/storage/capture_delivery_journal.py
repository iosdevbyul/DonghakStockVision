"""Durable local recording of explicit capture receipt reconciliation attempts."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from donghak_stock_vision.storage.capture_evidence_delivery import (
    ReceiptDelivery,
    reconcile_capture_receipts,
)
from donghak_stock_vision.storage.capture_evidence_sink import verify_exported_capture_receipt

JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_delivery_attempts (
    id INTEGER PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES raw_capture_receipts(raw_id),
    recorded_at TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('delivered','recovered','blocked')),
    reason TEXT,
    CHECK (outcome = 'blocked' OR reason IS NULL)
);
CREATE INDEX IF NOT EXISTS capture_delivery_attempts_raw
ON capture_delivery_attempts(raw_id, id);
CREATE TRIGGER IF NOT EXISTS capture_delivery_attempts_no_update
BEFORE UPDATE ON capture_delivery_attempts
BEGIN SELECT RAISE(ABORT, 'delivery journal immutable'); END;
CREATE TRIGGER IF NOT EXISTS capture_delivery_attempts_no_delete
BEFORE DELETE ON capture_delivery_attempts
BEGIN SELECT RAISE(ABORT, 'delivery journal immutable'); END;
"""


@dataclass(frozen=True)
class JournalBatch:
    results: tuple[ReceiptDelivery, ...]
    next_raw_id: int


def reconcile_and_journal(
    connection: sqlite3.Connection,
    destination: Path,
    *,
    after_raw_id: int = 0,
    limit: int = 100,
) -> JournalBatch:
    """Record every result; never treat a file or local journal as remote custody.

    Filesystem and SQLite cannot commit atomically. A crash after export but
    before journaling is recovered by verifying the existing file on retry.
    """
    connection.executescript(JOURNAL_SCHEMA)
    results = reconcile_capture_receipts(
        connection, destination, after_raw_id=after_raw_id, limit=limit
    )
    recorded: list[ReceiptDelivery] = []
    for item in results:
        # Re-check the output before persisting a successful delivery observation.
        if item.status != "blocked" and not verify_exported_capture_receipt(
            connection, item.raw_id, destination
        ):
            item = ReceiptDelivery(item.raw_id, "blocked", "post_export_verification_failed")
        with connection:
            connection.execute(
                "INSERT INTO capture_delivery_attempts "
                "(raw_id,recorded_at,outcome,reason) VALUES(?,?,?,?)",
                (item.raw_id, datetime.now(UTC).isoformat(), item.status, item.reason),
            )
        recorded.append(item)
    return JournalBatch(tuple(recorded), results[-1].raw_id if results else after_raw_id)
