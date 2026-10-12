"""SQLite outbox, immutable audit and readback integration with S3 transport."""

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from tests.custody_s3_fake import MemoryS3

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_outbox import enqueue_capture_receipt
from donghak_stock_vision.storage.capture_custody_s3 import S3CustodySession, S3CustodyTransport
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


def prepare(store: SQLiteStore, destination: Path) -> int:
    destination.mkdir()
    raw_id = store.archive(
        "s3-dev-test",
        "synthetic",
        "005930",
        RawPage(b"non-sensitive synthetic body", datetime.now(UTC), date(2026, 10, 9)),
    )
    export_capture_receipt(store.connection, raw_id, destination)
    enqueue_capture_receipt(store.connection, raw_id)
    return raw_id


def test_s3_submit_restart_lookup_and_readback_keep_a2_contract(tmp_path: Path) -> None:
    client = MemoryS3()
    db = tmp_path / "market.sqlite3"
    destination = tmp_path / "export"
    store = SQLiteStore(db)
    raw_id = prepare(store, destination)
    session = S3CustodySession(S3CustodyTransport(client, "dev-tests/integration/"))
    first = session.execute(store.connection, destination, raw_id, operation="submit")
    store.close()
    store = SQLiteStore(db)
    try:
        second = session.execute(store.connection, destination, raw_id, operation="submit")
        lookup = session.execute(store.connection, destination, raw_id, operation="lookup")
        readback = session.verify_readback(store.connection, destination, raw_id)
        assert first.observation.status == second.observation.status == lookup.observation.status
        assert first.observation.status == "claim_received_unverified"
        assert readback.status == "content_consistent_unverified"
        assert readback.receipt_sha256 == readback.retrieved_sha256
        assert not readback.independent_custody_verified and not readback.inference_authorized
        assert len(client.objects) == 1
        assert store.connection.execute(
            "SELECT state,attempt_count FROM capture_custody_outbox"
        ).fetchone() == ("pending", 0)
        rows = store.connection.execute(
            "SELECT observation_json FROM capture_custody_s3_versions ORDER BY id"
        ).fetchall()
        assert len(rows) == 3
        assert {json.loads(row[0])["versionId"] for row in rows} == {"version-1"}
        for table in (
            "capture_custody_submission_events",
            "capture_custody_lookup_events",
            "capture_custody_s3_versions",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                store.connection.execute(f"DELETE FROM {table}")
    finally:
        store.close()


def test_failed_s3_is_audited_and_never_creates_version_claim(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    destination = tmp_path / "exports"
    client = MemoryS3()
    client.fail_put = "AccessDenied"
    try:
        raw_id = prepare(store, destination)
        session = S3CustodySession(S3CustodyTransport(client, "dev-tests/integration/"))
        result = session.execute(store.connection, destination, raw_id, operation="submit")
        assert result.observation.status == "blocked"
        assert result.observation.blockers == ("custody_transport_failed",)
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM capture_custody_submission_events"
            ).fetchone()[0]
            == 1
        )
        assert (
            store.connection.execute("SELECT COUNT(*) FROM capture_custody_s3_versions").fetchone()[
                0
            ]
            == 0
        )
    finally:
        store.close()


def test_unqueued_or_quarantined_receipt_never_uploads(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    destination = tmp_path / "exports"
    client = MemoryS3()
    session = S3CustodySession(S3CustodyTransport(client, "dev-tests/integration/"))
    try:
        with pytest.raises(ValueError, match="queued"):
            session.execute(store.connection, destination, 999, operation="submit")
        raw_id = prepare(store, destination)
        with store.connection:
            store.connection.execute("UPDATE capture_custody_outbox SET state='manual_review'")
        with pytest.raises(ValueError, match="replay authorization"):
            session.execute(store.connection, destination, raw_id, operation="submit")
        assert client.calls == []
    finally:
        store.close()
