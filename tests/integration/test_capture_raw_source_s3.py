"""Raw custody SQLite audit/restart recovery isolated from receipt custody."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.custody_s3_fake import MemoryS3

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_outbox import enqueue_capture_receipt
from donghak_stock_vision.storage.capture_raw_source_s3 import (
    RAW_AUDIT_SCHEMA,
    RawSourceS3Adapter,
    RawSourceS3Error,
    RawSourceS3Session,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore


def prepare(store: SQLiteStore) -> int:
    raw_id = store.archive(
        "synthetic-run",
        "synthetic",
        "synthetic-scope",
        RawPage(b"non-sensitive\x00\xff" * 5000, datetime.now(UTC), None),
    )
    enqueue_capture_receipt(store.connection, raw_id)
    return raw_id


def session(client: MemoryS3) -> RawSourceS3Session:
    return RawSourceS3Session(RawSourceS3Adapter(client, "dev-tests/raw-integration/"))


def test_submit_restart_readback_and_separate_immutable_audit(tmp_path: Path) -> None:
    db = tmp_path / "market.sqlite3"
    store = SQLiteStore(db)
    client = MemoryS3()
    raw_id = prepare(store)
    first = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="submit"
    )
    store.close()
    store = SQLiteStore(db)
    try:
        for operation in ("lookup", "submit", "readback"):
            result = session(client).execute(
                store.connection, raw_id, archive_namespace="one", operation=operation
            )
            assert result.status == first.status == "content_consistent_unverified"
            assert result.observation == first.observation
            assert not result.independent_custody_verified and not result.inference_authorized
        assert len(client.objects) == 1
        assert store.connection.execute(
            "SELECT state,attempt_count FROM capture_custody_outbox"
        ).fetchone() == ("pending", 0)
        rows = store.connection.execute(
            "SELECT source_identifier,object_key,version_id,raw_sha256,retain_until "
            "FROM capture_raw_source_s3_events"
        ).fetchall()
        assert len(rows) == 4
        assert len(set(rows)) == 1
        assert (
            store.connection.execute(
                "SELECT name FROM sqlite_master WHERE name='capture_custody_s3_versions'"
            ).fetchone()
            is None
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute("DELETE FROM capture_raw_source_s3_events")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute("UPDATE capture_raw_source_s3_events SET status='blocked'")
    finally:
        store.close()


def test_lost_response_audited_blocked_then_restart_lookup_recovers(tmp_path: Path) -> None:
    db = tmp_path / "market.sqlite3"
    store = SQLiteStore(db)
    client = MemoryS3()
    client.lost_response = True
    raw_id = prepare(store)
    failed = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="submit"
    )
    assert failed.status == "blocked" and failed.observation is None
    store.close()
    store = SQLiteStore(db)
    try:
        recovered = session(client).execute(
            store.connection, raw_id, archive_namespace="one", operation="lookup"
        )
        replay = session(client).execute(
            store.connection, raw_id, archive_namespace="one", operation="submit"
        )
        assert recovered.observation == replay.observation
        assert recovered.status == "content_consistent_unverified"
        assert sum(op == "put" for op, _ in client.calls) == 1
        assert store.connection.execute(
            "SELECT status FROM capture_raw_source_s3_events ORDER BY id"
        ).fetchall() == [
            ("blocked",),
            ("content_consistent_unverified",),
            ("content_consistent_unverified",),
        ]
    finally:
        store.close()


def test_manual_review_never_submits_or_changes_outbox(store: SQLiteStore) -> None:
    raw_id = prepare(store)
    with store.connection:
        store.connection.execute("UPDATE capture_custody_outbox SET state='manual_review'")
    client = MemoryS3()
    result = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="submit"
    )
    assert result.status == "blocked" and "manual review" in str(result.blocker)
    assert client.calls == []
    missing = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="lookup"
    )
    assert missing.status == "not_found_unverified"
    assert store.connection.execute(
        "SELECT state,attempt_count FROM capture_custody_outbox"
    ).fetchone() == ("manual_review", 0)


def test_audit_insert_failure_does_not_claim_success_and_lookup_recovers(
    store: SQLiteStore,
) -> None:
    raw_id = prepare(store)
    store.connection.executescript(RAW_AUDIT_SCHEMA)
    store.connection.execute(
        "CREATE TRIGGER fail_raw_audit BEFORE INSERT ON capture_raw_source_s3_events "
        "BEGIN SELECT RAISE(ABORT,'synthetic-secret-body'); END"
    )
    client = MemoryS3()
    with pytest.raises(RawSourceS3Error, match="audit persistence failed") as error:
        session(client).execute(
            store.connection, raw_id, archive_namespace="one", operation="submit"
        )
    assert "synthetic-secret-body" not in str(error.value)
    assert len(client.objects) == 1
    assert (
        store.connection.execute("SELECT COUNT(*) FROM capture_raw_source_s3_events").fetchone()[0]
        == 0
    )
    store.connection.execute("DROP TRIGGER fail_raw_audit")
    recovered = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="lookup"
    )
    assert recovered.status == "content_consistent_unverified"
    assert sum(op == "put" for op, _ in client.calls) == 1


def test_prior_audited_version_change_or_expiry_is_not_success(store: SQLiteStore) -> None:
    raw_id = prepare(store)
    client = MemoryS3()
    session(client).execute(store.connection, raw_id, archive_namespace="one", operation="submit")
    next(iter(client.objects.values()))["VersionId"] = "replacement"
    result = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="readback"
    )
    assert result.status == "blocked" and result.observation is None
    assert "VersionId changed" in str(result.blocker)


def test_active_local_transaction_not_committed_by_remote_operation(store: SQLiteStore) -> None:
    raw_id = prepare(store)
    store.connection.execute("UPDATE raw_pages SET provider=provider WHERE id=?", (raw_id,))
    assert store.connection.in_transaction
    client = MemoryS3()
    with pytest.raises(RawSourceS3Error, match="committed local archive"):
        session(client).execute(
            store.connection, raw_id, archive_namespace="one", operation="submit"
        )
    assert store.connection.in_transaction and client.calls == []


def test_readonly_audit_initialization_failure_never_uploads(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    store = SQLiteStore(db)
    raw_id = prepare(store)
    store.close()
    connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    client = MemoryS3()
    try:
        with pytest.raises(RawSourceS3Error, match="audit initialization failed"):
            session(client).execute(connection, raw_id, archive_namespace="one", operation="submit")
        assert client.calls == []
    finally:
        connection.close()


def test_raw_and_receipt_storage_status_and_audits_remain_independent(
    store: SQLiteStore, tmp_path: Path
) -> None:
    from donghak_stock_vision.storage.capture_custody_s3 import S3CustodySession, S3CustodyTransport
    from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt

    raw_id = prepare(store)
    exports = tmp_path / "exports"
    exports.mkdir()
    exported = export_capture_receipt(store.connection, raw_id, exports)
    original_receipt = exported.path.read_bytes()
    client = MemoryS3()
    receipt = S3CustodySession(S3CustodyTransport(client, "dev-tests/raw-integration/"))
    missing_raw = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="lookup"
    )
    assert missing_raw.status == "not_found_unverified"
    submitted_receipt = receipt.execute(store.connection, exports, raw_id, operation="submit")
    assert submitted_receipt.observation.status == "claim_received_unverified"
    assert (
        session(client)
        .execute(store.connection, raw_id, archive_namespace="one", operation="lookup")
        .status
        == "not_found_unverified"
    )
    raw_result = session(client).execute(
        store.connection, raw_id, archive_namespace="one", operation="submit"
    )
    assert raw_result.observation is not None
    receipt_readback = receipt.verify_readback(store.connection, exports, raw_id)
    assert receipt_readback.status == "content_consistent_unverified"
    assert receipt_readback.receipt_sha256 == exported.sha256
    assert receipt_readback.receipt_sha256 != raw_result.observation.raw_sha256
    assert exported.path.read_bytes() == original_receipt
    assert len(client.objects) == 2
    assert (
        store.connection.execute("SELECT COUNT(*) FROM capture_custody_s3_versions").fetchone()[0]
        == 1
    )
    assert store.connection.execute(
        "SELECT state,attempt_count FROM capture_custody_outbox"
    ).fetchone() == ("pending", 0)
