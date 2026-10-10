"""Local delivery journal tests exercise interrupted export and tampering."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_delivery_journal import reconcile_and_journal
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


def test_recovery_after_export_before_journal(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    dest = tmp_path / "evidence"
    dest.mkdir()
    try:
        raw = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 9, tzinfo=UTC), date(2026, 10, 8)),
        )
        export_capture_receipt(store.connection, raw, dest)
        first = reconcile_and_journal(store.connection, dest)
        assert [(v.raw_id, v.status) for v in first.results] == [(raw, "delivered")]
        assert first.next_raw_id == raw
        second = reconcile_and_journal(store.connection, dest)
        assert second.results[0].status == "delivered"
        assert (
            store.connection.execute("SELECT COUNT(*) FROM capture_delivery_attempts").fetchone()[0]
            == 2
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute("DELETE FROM capture_delivery_attempts")
    finally:
        store.close()


def test_conflict_is_journaled_not_overwritten(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    dest = tmp_path / "evidence"
    dest.mkdir()
    try:
        raw = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 9, tzinfo=UTC), date(2026, 10, 8)),
        )
        (dest / f"raw-{raw}.json").write_bytes(b"corrupted")
        batch = reconcile_and_journal(store.connection, dest)
        assert batch.results[0].status == "blocked"
        assert batch.results[0].reason == "export_conflict"
        assert (dest / f"raw-{raw}.json").read_bytes() == b"corrupted"
    finally:
        store.close()
