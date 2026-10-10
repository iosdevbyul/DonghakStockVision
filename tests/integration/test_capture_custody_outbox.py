"""Offline durability and bounded-retry tests for custody outbox."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_outbox import (
    enqueue_capture_receipt,
    process_outbox,
)
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


class BrokenTransport:
    def submit(self, receipt_bytes: bytes, *, raw_id: int) -> bytes:
        raise RuntimeError("secret-error")


class UnverifiedTransport:
    def submit(self, receipt_bytes: bytes, *, raw_id: int) -> bytes:
        return b"not-an-ack"


def _prepare(tmp_path: Path) -> tuple[SQLiteStore, Path, int]:
    store = SQLiteStore(tmp_path / "market.db")
    destination = tmp_path / "exports"
    destination.mkdir()
    raw_id = store.archive(
        "run",
        "krx",
        "005930",
        RawPage(b"raw", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
    )
    export_capture_receipt(store.connection, raw_id, destination)
    return store, destination, raw_id


def _run(
    store: SQLiteStore,
    destination: Path,
    transport: BrokenTransport | UnverifiedTransport,
    max_attempts: int = 3,
) -> tuple:
    return process_outbox(
        store.connection,
        destination,
        transport=transport,
        expected_custodian_id="vault",
        independently_pinned_ack_sha256="0" * 64,
        max_attempts=max_attempts,
    )


def test_retries_are_bounded_and_durable(tmp_path: Path) -> None:
    store, destination, raw_id = _prepare(tmp_path)
    try:
        enqueue_capture_receipt(store.connection, raw_id)
        enqueue_capture_receipt(store.connection, raw_id)
        assert _run(store, destination, BrokenTransport())[0].state == "retry"
        assert _run(store, destination, BrokenTransport())[0].attempt_count == 2
        third = _run(store, destination, BrokenTransport())
        assert third[0].state == "manual_review"
        assert third[0].attempt_count == 3
        assert _run(store, destination, BrokenTransport()) == ()
        assert (
            store.connection.execute(
                "SELECT attempt_count FROM capture_custody_outbox WHERE raw_id=?",
                (raw_id,),
            ).fetchone()[0]
            == 3
        )
    finally:
        store.close()


def test_invalid_response_cannot_complete_custody(tmp_path: Path) -> None:
    store, destination, raw_id = _prepare(tmp_path)
    try:
        enqueue_capture_receipt(store.connection, raw_id)
        result = _run(store, destination, UnverifiedTransport())
        assert result[0].state == "manual_review"
        assert result[0].blocker == "invalid_custody_acknowledgement"
    finally:
        store.close()


def test_missing_receipt_rejected(tmp_path: Path) -> None:
    store, _, _ = _prepare(tmp_path)
    try:
        with pytest.raises(ValueError, match="does not exist"):
            enqueue_capture_receipt(store.connection, 999)
    finally:
        store.close()
