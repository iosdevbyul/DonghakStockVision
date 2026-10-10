"""Stable submission identity without remote custody authorization."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_idempotency import (
    prepare_custody_submission_identity,
)
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


def test_identity_stable_across_retries(tmp_path: Path) -> None:
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
        first = prepare_custody_submission_identity(store.connection, destination, raw_id)
        second = prepare_custody_submission_identity(store.connection, destination, raw_id)
        assert first == second
        assert len(first.idempotency_key) == 64
        assert first.receipt_sha256 != first.idempotency_key
        assert first.raw_id == raw_id
    finally:
        store.close()


def test_missing_or_tampered_export_is_rejected(tmp_path: Path) -> None:
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
        with pytest.raises(ValueError, match="verified"):
            prepare_custody_submission_identity(store.connection, destination, raw_id)
        export_capture_receipt(store.connection, raw_id, destination)
        (destination / f"raw-{raw_id}.json").write_bytes(b"changed")
        with pytest.raises(ValueError, match="verified"):
            prepare_custody_submission_identity(store.connection, destination, raw_id)
    finally:
        store.close()


def test_invalid_id_is_rejected(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    destination = tmp_path / "exports"
    destination.mkdir()
    try:
        with pytest.raises(ValueError, match="verified"):
            prepare_custody_submission_identity(store.connection, destination, 0)
    finally:
        store.close()
