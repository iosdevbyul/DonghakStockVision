"""Archived evidence export stays read-only and cannot invent trusted claims."""

import hashlib
import sqlite3
from datetime import UTC, date, datetime

import pytest

from donghak_stock_vision.api.archived_source_evidence import build_archived_source_evidence
from donghak_stock_vision.api.source_collection_evidence import SourceCollectionEvidence


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE raw_pages (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, "
        "provider TEXT NOT NULL, ticker TEXT NOT NULL, collected_at TEXT NOT NULL, "
        "source_date TEXT, sha256 TEXT NOT NULL, body BLOB NOT NULL);"
    )
    body = b'{"rows":[]}'
    connection.execute(
        "INSERT INTO raw_pages VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
        (
            "run-1",
            "krx",
            "@krx_market:KOSPI",
            datetime(2026, 10, 9, tzinfo=UTC).isoformat(),
            date(2026, 10, 8).isoformat(),
            hashlib.sha256(body).hexdigest(),
            body,
        ),
    )
    return connection


def _export(connection: sqlite3.Connection) -> SourceCollectionEvidence:
    result = build_archived_source_evidence(
        connection,
        raw_id=1,
        snapshot_id="snapshot-1",
        collector_name="dsv",
        collector_version="2.0.0a3",
        observation_kind="initial",
    )
    assert result.sha256 == hashlib.sha256(result.evidence_bytes).hexdigest()
    return SourceCollectionEvidence.model_validate_json(result.evidence_bytes)


def test_export_existing_raw_without_database_mutation() -> None:
    connection = _database()
    before = connection.total_changes
    evidence = _export(connection)
    assert evidence.provider == "krx"
    assert evidence.scope == "@krx_market:KOSPI"
    assert evidence.runId == "run-1"
    assert evidence.sourceDate == date(2026, 10, 8)
    assert connection.total_changes == before


def test_checksum_mismatch_is_fail_closed() -> None:
    connection = _database()
    connection.execute("UPDATE raw_pages SET body=? WHERE id=1", (b"modified",))
    with pytest.raises(ValueError, match="checksum"):
        _export(connection)


def test_missing_row_is_rejected() -> None:
    connection = _database()
    with pytest.raises(ValueError, match="not found"):
        build_archived_source_evidence(
            connection,
            raw_id=5,
            snapshot_id="snapshot-1",
            collector_name="dsv",
            collector_version="2.0.0a3",
            observation_kind="initial",
        )


def test_revision_requires_explicit_predecessor() -> None:
    connection = _database()
    with pytest.raises(ValueError, match="invalid archived collection metadata"):
        build_archived_source_evidence(
            connection,
            raw_id=1,
            snapshot_id="snapshot-1",
            collector_name="dsv",
            collector_version="2.0.0a3",
            observation_kind="revision",
        )


def test_timestamp_without_timezone_is_rejected() -> None:
    connection = _database()
    connection.execute(
        "UPDATE raw_pages SET collected_at='2026-10-09T00:00:00' WHERE id=1"
    )
    with pytest.raises(ValueError, match="invalid archived collection metadata"):
        _export(connection)
