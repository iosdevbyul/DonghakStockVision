"""Explicit custody submissions preserve untrusted observation boundaries."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_outbox import enqueue_capture_receipt
from donghak_stock_vision.storage.capture_custody_submission_audit import (
    submit_outbox_and_audit,
)
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def submit(self, receipt_bytes: bytes, *, raw_id: int, idempotency_key: str) -> bytes:
        self.calls.append(idempotency_key)
        return b"unverified-remote-claim"

    def lookup(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        raise AssertionError("lookup must not be called")


def test_explicit_submit_is_audited_without_acceptance(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    destination = tmp_path / "export"
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
        transport = RecordingTransport()
        first = submit_outbox_and_audit(store.connection, destination, raw_id, transport=transport)
        second = submit_outbox_and_audit(store.connection, destination, raw_id, transport=transport)
        assert first.observation.status == "claim_received_unverified"
        assert first.event_id != second.event_id
        assert len(set(transport.calls)) == 1
        assert not first.observation.independent_custody_verified
        assert not first.observation.inference_authorized
        row = store.connection.execute(
            "SELECT state,attempt_count FROM capture_custody_outbox WHERE raw_id=?",
            (raw_id,),
        ).fetchone()
        assert row == ("pending", 0)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute("DELETE FROM capture_custody_submission_events")
    finally:
        store.close()


def test_submission_rejects_unknown_and_quarantined_receipts(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    destination = tmp_path / "export"
    destination.mkdir()
    try:
        transport = RecordingTransport()
        with pytest.raises(ValueError, match="queued"):
            submit_outbox_and_audit(store.connection, destination, 999, transport=transport)
        assert transport.calls == []
    finally:
        store.close()
