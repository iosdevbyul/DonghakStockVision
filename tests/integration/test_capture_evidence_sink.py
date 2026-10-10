"""Offline tests for explicit export, tampering, duplicate and failure handling."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_evidence_sink import (
    export_capture_receipt,
    verify_exported_capture_receipt,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore


def _store(path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(path)
    raw_id = store.archive(
        "run-1",
        "krx",
        "005930",
        RawPage(b"original", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
    )
    return store, raw_id


def test_export_and_verify_without_modifying_archive(tmp_path: Path) -> None:
    store, raw_id = _store(tmp_path / "market.db")
    destination = tmp_path / "separate"
    destination.mkdir()
    try:
        changes = store.connection.total_changes
        exported = export_capture_receipt(store.connection, raw_id, destination)
        assert exported.path.name == f"raw-{raw_id}.json"
        assert verify_exported_capture_receipt(store.connection, raw_id, destination)
        assert store.connection.total_changes == changes
    finally:
        store.close()


def test_duplicate_export_never_overwrites(tmp_path: Path) -> None:
    store, raw_id = _store(tmp_path / "market.db")
    destination = tmp_path / "separate"
    destination.mkdir()
    try:
        first = export_capture_receipt(store.connection, raw_id, destination)
        original = first.path.read_bytes()
        with pytest.raises(ValueError, match="already exported"):
            export_capture_receipt(store.connection, raw_id, destination)
        assert first.path.read_bytes() == original
    finally:
        store.close()


def test_tampering_is_detected(tmp_path: Path) -> None:
    store, raw_id = _store(tmp_path / "market.db")
    destination = tmp_path / "separate"
    destination.mkdir()
    try:
        result = export_capture_receipt(store.connection, raw_id, destination)
        result.path.write_text("tampered")
        assert not verify_exported_capture_receipt(store.connection, raw_id, destination)
    finally:
        store.close()


def test_missing_sink_fails_without_data_loss(tmp_path: Path) -> None:
    store, raw_id = _store(tmp_path / "market.db")
    try:
        with pytest.raises(ValueError, match="existing real directory"):
            export_capture_receipt(store.connection, raw_id, tmp_path / "missing")
        assert store.connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == 1
    finally:
        store.close()


def test_missing_receipt_is_rejected(tmp_path: Path) -> None:
    store, _ = _store(tmp_path / "market.db")
    destination = tmp_path / "separate"
    destination.mkdir()
    try:
        with pytest.raises(ValueError, match="missing"):
            export_capture_receipt(store.connection, 99, destination)
    finally:
        store.close()


def test_mutated_raw_is_rejected_before_delivery(tmp_path: Path) -> None:
    store, raw_id = _store(tmp_path / "market.db")
    destination = tmp_path / "separate"
    destination.mkdir()
    try:
        store.connection.execute("UPDATE raw_pages SET body=? WHERE id=?", (b"evil", raw_id))
        with pytest.raises(ValueError, match="mismatch"):
            export_capture_receipt(store.connection, raw_id, destination)
        assert list(destination.iterdir()) == []
    finally:
        store.close()
