"""Offline recovery: no silent overwrite, authorization or legacy backfill."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_evidence_delivery import reconcile_capture_receipts
from donghak_stock_vision.storage.sqlite import SQLiteStore


def _setup(tmp_path: Path) -> tuple[SQLiteStore, Path]:
    store = SQLiteStore(tmp_path / "market.db")
    destination = tmp_path / "exports"
    destination.mkdir()
    return store, destination


def _archive(store: SQLiteStore, run_id: str) -> int:
    return store.archive(
        run_id,
        "krx",
        "005930",
        RawPage(b"raw response", datetime(2026, 10, 9, tzinfo=UTC), date(2026, 10, 8)),
    )


def test_missing_receipt_file_is_recovered_and_idempotent(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        raw_id = _archive(store, "run-1")
        before = store.connection.total_changes
        assert [
            (r.raw_id, r.status) for r in reconcile_capture_receipts(store.connection, dest)
        ] == [(raw_id, "recovered")]
        assert [
            (r.raw_id, r.status) for r in reconcile_capture_receipts(store.connection, dest)
        ] == [(raw_id, "delivered")]
        assert store.connection.total_changes == before
    finally:
        store.close()


def test_tampered_export_is_blocked_without_overwrite(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        raw_id = _archive(store, "run-1")
        file = dest / f"raw-{raw_id}.json"
        file.write_bytes(b"tampered")
        result = reconcile_capture_receipts(store.connection, dest)
        assert result[0].status == "blocked"
        assert result[0].reason == "export_conflict"
        assert file.read_bytes() == b"tampered"
    finally:
        store.close()


def test_invalid_receipt_is_not_re_exported(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        raw_id = _archive(store, "run-1")
        store.connection.execute("UPDATE raw_pages SET body=? WHERE id=?", (b"tampered", raw_id))
        result = reconcile_capture_receipts(store.connection, dest)
        assert result[0].status == "blocked"
        assert not (dest / f"raw-{raw_id}.json").exists()
    finally:
        store.close()


def test_export_missing_directory_fails_closed(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        _archive(store, "run-1")
        dest.rmdir()
        try:
            reconcile_capture_receipts(store.connection, dest)
        except ValueError as error:
            assert "existing real directory" in str(error)
        else:
            raise AssertionError("expected destination rejection")
    finally:
        store.close()


def test_limit_is_bounded(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        _archive(store, "run-1")
        _archive(store, "run-2")
        assert len(reconcile_capture_receipts(store.connection, dest, limit=1)) == 1
    finally:
        store.close()


def test_cursor_pagination_reaches_every_receipt(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        ids = [_archive(store, f"run-{i}") for i in range(5)]
        first = reconcile_capture_receipts(store.connection, dest, limit=2)
        second = reconcile_capture_receipts(
            store.connection, dest, limit=2, after_raw_id=first[-1].raw_id
        )
        third = reconcile_capture_receipts(
            store.connection, dest, limit=2, after_raw_id=second[-1].raw_id
        )
        assert [r.raw_id for r in (*first, *second, *third)] == ids
        assert all(r.status == "recovered" for r in (*first, *second, *third))
        assert reconcile_capture_receipts(
            store.connection, dest, limit=2, after_raw_id=third[-1].raw_id
        ) == ()
    finally:
        store.close()


def test_blocked_receipt_does_not_hide_later_pages(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        first_id, second_id = _archive(store, "run-1"), _archive(store, "run-2")
        (dest / f"raw-{first_id}.json").write_bytes(b"conflicting")
        first = reconcile_capture_receipts(store.connection, dest, limit=1)
        assert first[0].status == "blocked"
        second = reconcile_capture_receipts(
            store.connection, dest, limit=1, after_raw_id=first[0].raw_id
        )
        assert [(r.raw_id, r.status) for r in second] == [(second_id, "recovered")]
    finally:
        store.close()


def test_negative_cursor_is_rejected(tmp_path: Path) -> None:
    store, dest = _setup(tmp_path)
    try:
        with pytest.raises(ValueError, match="after_raw_id"):
            reconcile_capture_receipts(store.connection, dest, after_raw_id=-1)
    finally:
        store.close()
