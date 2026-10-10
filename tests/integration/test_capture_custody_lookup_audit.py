"""Local audit of non-authoritative remote custody lookups."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_lookup_audit import (
    lookup_outbox_and_audit,
)
from donghak_stock_vision.storage.capture_custody_outbox import enqueue_capture_receipt
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


class MissingTransport:
    def lookup(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        return None

    def submit(self, receipt_bytes: bytes, *, raw_id: int, idempotency_key: str) -> bytes:
        raise AssertionError("submit must not be called")


def test_lookup_records_absence_without_modifying_outbox(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    destination = tmp_path / "exports"
    destination.mkdir()
    try:
        raw_id = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, destination)
        enqueue_capture_receipt(store.connection, raw_id)
        first = lookup_outbox_and_audit(
            store.connection, destination, raw_id, transport=MissingTransport()
        )
        second = lookup_outbox_and_audit(
            store.connection, destination, raw_id, transport=MissingTransport()
        )
        assert first.observation.status == "not_found_unverified"
        assert not first.observation.independent_custody_verified
        assert not first.observation.inference_authorized
        assert first.observation.idempotency_key == second.observation.idempotency_key
        assert first.event_id != second.event_id
        state = store.connection.execute(
            "SELECT state,attempt_count FROM capture_custody_outbox WHERE raw_id=?",
            (raw_id,),
        ).fetchone()
        assert state == ("pending", 0)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute("DELETE FROM capture_custody_lookup_events")
    finally:
        store.close()


def test_lookup_requires_existing_outbox_item(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    destination = tmp_path / "exports"
    destination.mkdir()
    try:
        with pytest.raises(ValueError, match="queued"):
            lookup_outbox_and_audit(
                store.connection, destination, 999, transport=MissingTransport()
            )
    finally:
        store.close()
