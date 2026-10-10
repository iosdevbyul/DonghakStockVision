"""Offline capture receipt tests; pre-existing raw rows are not backfilled."""

import hashlib
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.sqlite import SQLiteStore


def _page() -> RawPage:
    return RawPage(b'{"rows":[]}', datetime(2026, 10, 9, tzinfo=UTC), date(2026, 10, 8))


def test_capture_receipt_is_created_with_raw_page(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    try:
        page = _page()
        raw_id = store.archive("run-1", "krx", "@krx_market:KOSPI", page)
        receipt = store.connection.execute(
            "SELECT run_id,provider,scope,raw_sha256 FROM raw_capture_receipts WHERE raw_id=?",
            (raw_id,),
        ).fetchone()
        assert receipt == (
            "run-1",
            "krx",
            "@krx_market:KOSPI",
            hashlib.sha256(page.body).hexdigest(),
        )
        assert store.check_integrity() == []
    finally:
        store.close()


def test_receipt_is_immutable(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    try:
        raw_id = store.archive("run-1", "krx", "005930", _page())
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute(
                "UPDATE raw_capture_receipts SET run_id='fake' WHERE raw_id=?", (raw_id,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute("DELETE FROM raw_capture_receipts WHERE raw_id=?", (raw_id,))
    finally:
        store.close()


def test_receipt_insert_failure_rolls_back_original_page(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    try:
        store.connection.execute(
            "CREATE TRIGGER reject_receipt BEFORE INSERT ON raw_capture_receipts "
            "BEGIN SELECT RAISE(ABORT,'receipt rejected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="receipt rejected"):
            store.archive("run-1", "krx", "005930", _page())
        assert store.connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == 0
    finally:
        store.close()


def test_receipt_metadata_corruption_is_reported(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    try:
        raw_id = store.archive("run-1", "krx", "005930", _page())
        store.connection.execute("DROP TRIGGER raw_capture_receipts_no_update")
        store.connection.execute(
            "UPDATE raw_capture_receipts SET scope='wrong' WHERE raw_id=?", (raw_id,)
        )
        assert f"raw capture receipt mismatch: {raw_id}" in store.check_integrity()
    finally:
        store.close()


def test_existing_archive_without_receipt_is_not_backfilled(tmp_path: Path) -> None:
    path = tmp_path / "market.sqlite3"
    store = SQLiteStore(path)
    try:
        store.connection.execute(
            "INSERT INTO raw_pages "
            "(run_id,provider,ticker,collected_at,source_date,sha256,body) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "legacy",
                "krx",
                "005930",
                _page().collected_at.isoformat(),
                "2026-10-08",
                hashlib.sha256(_page().body).hexdigest(),
                _page().body,
            ),
        )
        store.connection.commit()
    finally:
        store.close()
    reopened = SQLiteStore(path)
    try:
        assert (
            reopened.connection.execute("SELECT COUNT(*) FROM raw_capture_receipts").fetchone()[0]
            == 0
        )
        assert reopened.check_integrity() == []
    finally:
        reopened.close()
