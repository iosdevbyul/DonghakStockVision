"""Offline recovery: no silent overwrite, authorization or legacy backfill."""

from datetime import UTC, date, datetime
from pathlib import Path

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
