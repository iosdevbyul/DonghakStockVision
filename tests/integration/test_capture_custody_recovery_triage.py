"""Recovery triage never treats a custody lookup as trusted acceptance."""

import sqlite3

import pytest

from donghak_stock_vision.storage.capture_custody_lookup_audit import LOOKUP_SCHEMA
from donghak_stock_vision.storage.capture_custody_outbox import SCHEMA as OUTBOX_SCHEMA
from donghak_stock_vision.storage.capture_custody_recovery_triage import (
    list_custody_recovery_items,
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE raw_capture_receipts(raw_id INTEGER PRIMARY KEY)")
    connection.executescript(OUTBOX_SCHEMA)
    connection.executescript(LOOKUP_SCHEMA)
    for raw_id, state in ((1, "pending"), (2, "retry"), (3, "manual_review")):
        connection.execute("INSERT INTO raw_capture_receipts VALUES (?)", (raw_id,))
        connection.execute(
            "INSERT INTO capture_custody_outbox(raw_id,state) VALUES (?,?)",
            (raw_id, state),
        )
    connection.execute(
        "INSERT INTO capture_custody_lookup_events "
        "(raw_id,status,observed_at) VALUES (?,?,?)",
        (2, "not_found_unverified", "2026-10-11T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO capture_custody_lookup_events "
        "(raw_id,status,observed_at) VALUES (?,?,?)",
        (3, "claim_received_unverified", "2026-10-11T00:01:00+00:00"),
    )
    connection.commit()
    return connection


def test_recovery_triage_is_read_only_and_never_accepts() -> None:
    connection = _connection()
    try:
        before = connection.total_changes
        items = list_custody_recovery_items(connection)
        assert [(item.raw_id, item.action) for item in items] == [
            (1, "lookup"),
            (2, "retry_pending"),
            (3, "review"),
        ]
        assert all(not item.authenticated_acceptance for item in items)
        assert all(not item.inference_authorized for item in items)
        assert connection.total_changes == before
    finally:
        connection.close()


def test_recovery_triage_paginates_and_rejects_invalid_limits() -> None:
    connection = _connection()
    try:
        first = list_custody_recovery_items(connection, limit=2)
        second = list_custody_recovery_items(
            connection, limit=2, after_raw_id=first[-1].raw_id
        )
        assert [item.raw_id for item in first + second] == [1, 2, 3]
        with pytest.raises(ValueError, match="pagination"):
            list_custody_recovery_items(connection, after_raw_id=-1)
    finally:
        connection.close()
