"""Synthetic, offline raw source linkage, bounds, identity and recovery contracts."""

import hashlib
import sqlite3
import traceback
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.custody_s3_fake import MemoryS3

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_idempotency import (
    prepare_custody_submission_identity,
)
from donghak_stock_vision.storage.capture_custody_s3 import S3CustodyTransport
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.capture_raw_source_contract import (
    MAX_RAW_SOURCE_BYTES,
    RawSourceRejected,
    prepare_raw_source_custody,
    verify_raw_source_readback,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore

BODY = b' { "value" : 1, "secret" : "SYNTHETIC_BODY_SECRET" }\r\n\xff\x00'
NAMESPACE = "synthetic-archive-01"


def archive(store: SQLiteStore, body: bytes = BODY) -> int:
    return store.archive(
        "run-synthetic",
        "synthetic",
        "@market:KOSPI",
        RawPage(body, datetime(2026, 10, 12, 9, 1, tzinfo=UTC), date(2026, 10, 9)),
    )


def test_exact_binary_bytes_metadata_and_receipt_link(store: SQLiteStore, tmp_path: Path) -> None:
    raw_id = archive(store)
    changes = store.connection.total_changes
    contract = prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)
    assert contract.body == BODY  # Whitespace, invalid UTF-8 and NUL stay untouched.
    assert contract.raw_id == raw_id
    assert contract.raw_sha256 == hashlib.sha256(BODY).hexdigest()
    assert contract.byte_length == len(BODY)
    assert (
        contract.run_id,
        contract.provider,
        contract.scope,
        contract.collected_at,
        contract.source_date,
    ) == ("run-synthetic", "synthetic", "@market:KOSPI", "2026-10-12T09:01:00+00:00", "2026-10-09")
    destination = tmp_path / "exports"
    destination.mkdir()
    exported = export_capture_receipt(store.connection, raw_id, destination)
    identity = prepare_custody_submission_identity(store.connection, destination, raw_id)
    assert contract.receipt_sha256 == exported.sha256 == identity.receipt_sha256
    assert contract.receipt_idempotency_key == identity.idempotency_key
    receipt_adapter = S3CustodyTransport(MemoryS3(), f"evidence/{NAMESPACE}/")
    assert contract.receipt_object_key == receipt_adapter.object_key(
        raw_id=raw_id, idempotency_key=identity.idempotency_key
    )
    assert contract.source_object_key != contract.receipt_object_key
    assert "/raw-source-v1/" in contract.source_object_key
    assert contract.source_object_key.endswith(f"/{contract.identifier}.bin")
    assert "SYNTHETIC_BODY_SECRET" not in repr(contract)
    assert "SYNTHETIC_BODY_SECRET" not in str(contract.object_metadata())
    assert contract.object_metadata()["raw-sha256"] == contract.raw_sha256
    assert store.connection.total_changes == changes


def test_identity_survives_restart_and_namespace_separates_databases(tmp_path: Path) -> None:
    path = tmp_path / "one.sqlite3"
    first = SQLiteStore(path)
    raw_id = archive(first)
    expected = prepare_raw_source_custody(first.connection, raw_id, archive_namespace="archive-a")
    assert (
        prepare_raw_source_custody(first.connection, raw_id, archive_namespace="archive-a")
        == expected
    )
    first.close()
    first = SQLiteStore(path)
    second = SQLiteStore(tmp_path / "two.sqlite3")
    try:
        second_id = archive(second)
        assert raw_id == second_id
        reopened = prepare_raw_source_custody(
            first.connection, raw_id, archive_namespace="archive-a"
        )
        other = prepare_raw_source_custody(
            second.connection, second_id, archive_namespace="archive-b"
        )
        assert reopened.identifier == expected.identifier
        assert reopened.source_object_key == expected.source_object_key
        assert other.identifier != expected.identifier
        assert other.source_object_key != expected.source_object_key
        assert other.receipt_object_key != expected.receipt_object_key
        assert other.receipt_sha256 == expected.receipt_sha256
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("body", [b"", b"x" * 65537, b"x" * MAX_RAW_SOURCE_BYTES])
def test_empty_large_and_exact_maximum_are_supported(store: SQLiteStore, body: bytes) -> None:
    raw_id = archive(store, body)
    contract = prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)
    assert contract.body == body
    assert contract.byte_length == len(body)
    assert (
        verify_raw_source_readback(contract, body, version_id="synthetic-version").raw_sha256
        == contract.raw_sha256
    )


def test_oversized_blob_is_not_materialized(store: SQLiteStore) -> None:
    raw_id = archive(store)
    store.connection.execute(
        "UPDATE raw_pages SET body=zeroblob(?) WHERE id=?", (MAX_RAW_SOURCE_BYTES + 1, raw_id)
    )

    def bounded_row(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
        assert row[7] == MAX_RAW_SOURCE_BYTES + 1
        assert row[8] is None  # CASE suppresses transfer of the oversized BLOB.
        return row

    store.connection.row_factory = bounded_row
    with pytest.raises(RawSourceRejected, match="maximum size"):
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)


@pytest.mark.parametrize(
    "namespace",
    ["", "A", " ../a", "a/b", "a.b", "a%2fb", "a\\b", "a\n", "한글", "x" * 129, None, 1],
)
def test_invalid_namespace_fails_before_sql(store: SQLiteStore, namespace: Any) -> None:
    # A closed connection proves the namespace is checked before reading the DB.
    store.connection.close()
    with pytest.raises(RawSourceRejected, match="namespace"):
        prepare_raw_source_custody(store.connection, 1, archive_namespace=namespace)


@pytest.mark.parametrize("raw_id", [0, -1, True, "1", None])
def test_invalid_raw_id_is_rejected(store: SQLiteStore, raw_id: Any) -> None:
    with pytest.raises(RawSourceRejected, match="source ID"):
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)


def test_missing_raw_or_receipt_fails_closed(store: SQLiteStore) -> None:
    with pytest.raises(RawSourceRejected, match="source missing"):
        prepare_raw_source_custody(store.connection, 999, archive_namespace=NAMESPACE)
    raw_id = archive(store)
    store.connection.execute("DROP TRIGGER raw_capture_receipts_no_delete")
    store.connection.execute("DELETE FROM raw_capture_receipts WHERE raw_id=?", (raw_id,))
    with pytest.raises(RawSourceRejected, match="receipt missing"):
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)


@pytest.mark.parametrize(
    "column",
    ["schema_version", "run_id", "provider", "scope", "collected_at", "source_date", "raw_sha256"],
)
def test_receipt_metadata_mismatch_is_rejected(store: SQLiteStore, column: str) -> None:
    raw_id = archive(store)
    store.connection.execute("DROP TRIGGER raw_capture_receipts_no_update")
    store.connection.execute(
        f"UPDATE raw_capture_receipts SET {column}=? WHERE raw_id=?", ("corrupt", raw_id)
    )
    with pytest.raises(RawSourceRejected, match="metadata mismatch"):
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)


@pytest.mark.parametrize("value", ["secret-text-not-blob", 123, 1.5])
def test_wrong_sqlite_body_type_is_rejected(store: SQLiteStore, value: Any) -> None:
    raw_id = archive(store)
    store.connection.execute("UPDATE raw_pages SET body=? WHERE id=?", (value, raw_id))
    with pytest.raises(RawSourceRejected, match="SQLite BLOB"):
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)


def test_mutated_bytes_and_matching_but_false_db_hash_are_rejected(store: SQLiteStore) -> None:
    raw_id = archive(store)
    store.connection.execute("UPDATE raw_pages SET body=? WHERE id=?", (b"tampered", raw_id))
    with pytest.raises(RawSourceRejected, match="checksum mismatch"):
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)
    store.connection.execute("DROP TRIGGER raw_capture_receipts_no_update")
    # Even if both recorded hashes agree, hash actual bytes independently.
    store.connection.execute("UPDATE raw_pages SET sha256=? WHERE id=?", ("0" * 64, raw_id))
    store.connection.execute(
        "UPDATE raw_capture_receipts SET raw_sha256=? WHERE raw_id=?", ("0" * 64, raw_id)
    )
    with pytest.raises(RawSourceRejected, match="checksum mismatch"):
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)


def test_null_source_date_preserved(store: SQLiteStore) -> None:
    raw_id = store.archive("run", "synthetic", "scope", RawPage(b"body", datetime.now(UTC), None))
    assert (
        prepare_raw_source_custody(
            store.connection, raw_id, archive_namespace=NAMESPACE
        ).source_date
        is None
    )


def test_readback_rejects_conflicting_content_and_preserves_unverified_authority(
    store: SQLiteStore,
) -> None:
    contract = prepare_raw_source_custody(
        store.connection, archive(store), archive_namespace=NAMESPACE
    )
    review = verify_raw_source_readback(contract, BODY, version_id="offline-version")
    assert review.source_identifier == contract.identifier
    assert review.version_id == "offline-version"
    assert review.status == "content_consistent_unverified"
    assert not review.independent_custody_verified and not review.inference_authorized
    with pytest.raises(RawSourceRejected, match="checksum mismatch"):
        verify_raw_source_readback(contract, b"other", version_id="same-version")
    with pytest.raises(RawSourceRejected, match="checksum mismatch"):
        replace(contract, body=b"other")
    changed = replace(contract, body=b"other", raw_sha256=hashlib.sha256(b"other").hexdigest())
    assert changed.identifier != contract.identifier


@pytest.mark.parametrize("value", ["", " ", "null", None, 123, "x" * 1025])
def test_invalid_readback_version_is_rejected(store: SQLiteStore, value: Any) -> None:
    contract = prepare_raw_source_custody(
        store.connection, archive(store), archive_namespace=NAMESPACE
    )
    with pytest.raises(RawSourceRejected, match="VersionId"):
        verify_raw_source_readback(contract, BODY, version_id=value)


@pytest.mark.parametrize("value", ["secret-not-bytes", bytearray(BODY), memoryview(BODY), None])
def test_wrong_readback_and_contract_body_types_are_rejected(
    store: SQLiteStore, value: Any
) -> None:
    contract = prepare_raw_source_custody(
        store.connection, archive(store), archive_namespace=NAMESPACE
    )
    with pytest.raises(RawSourceRejected, match="must be bytes"):
        verify_raw_source_readback(contract, value, version_id="version")
    with pytest.raises(RawSourceRejected, match="must be bytes"):
        replace(contract, body=value)


def test_secret_body_never_appears_in_exception_traceback(store: SQLiteStore) -> None:
    raw_id = archive(store)
    store.connection.execute("UPDATE raw_pages SET sha256='wrong' WHERE id=?", (raw_id,))
    with pytest.raises(RawSourceRejected) as error:
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)
    assert "SYNTHETIC_BODY_SECRET" not in "".join(traceback.format_exception(error.value))
    store.connection.close()
    with pytest.raises(RawSourceRejected, match="database read failed") as db_error:
        prepare_raw_source_custody(store.connection, raw_id, archive_namespace=NAMESPACE)
    assert "closed database" not in "".join(traceback.format_exception(db_error.value))


def test_namespace_maximum_and_oversized_in_memory_bytes(store: SQLiteStore) -> None:
    contract = prepare_raw_source_custody(
        store.connection, archive(store), archive_namespace="a" * 128
    )
    assert contract.archive_namespace == "a" * 128
    oversized = b"x" * (MAX_RAW_SOURCE_BYTES + 1)
    with pytest.raises(RawSourceRejected, match="maximum size"):
        replace(contract, body=oversized)
    with pytest.raises(RawSourceRejected, match="maximum size"):
        verify_raw_source_readback(contract, oversized, version_id="version")


def test_metadata_change_changes_source_identifier(store: SQLiteStore) -> None:
    contract = prepare_raw_source_custody(
        store.connection, archive(store), archive_namespace=NAMESPACE
    )
    changed = replace(contract, run_id="different-run")
    assert changed.raw_sha256 == contract.raw_sha256
    assert changed.identifier != contract.identifier
    assert changed.receipt_sha256 != contract.receipt_sha256


def test_readonly_connection_uses_one_snapshot_and_no_writes(tmp_path: Path) -> None:
    path = tmp_path / "read-only.sqlite3"
    store = SQLiteStore(path)
    raw_id = archive(store)
    store.close()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        contract = prepare_raw_source_custody(connection, raw_id, archive_namespace=NAMESPACE)
        assert contract.body == BODY
        assert connection.total_changes == 0
        assert len(statements) == 1 and statements[0].startswith("SELECT")
        assert not connection.in_transaction
    finally:
        connection.close()


@pytest.mark.parametrize(
    "updates", [{"provider": b"not-text"}, {"source_date": 123}, {"raw_sha256": 123}]
)
def test_bad_contract_metadata_types_are_rejected(
    store: SQLiteStore, updates: dict[str, Any]
) -> None:
    contract = prepare_raw_source_custody(
        store.connection, archive(store), archive_namespace=NAMESPACE
    )
    with pytest.raises(RawSourceRejected):
        replace(contract, **updates)
