"""Offline operator replay audit and safety tests."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_outbox import (
    enqueue_capture_receipt,
    process_outbox,
)
from donghak_stock_vision.storage.capture_custody_replay import (
    requeue_after_operator_review,
)
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


class FailedTransport:
    def submit(self, receipt_bytes: bytes, *, raw_id: int) -> bytes:
        raise OSError("sensitive transport failure")


def test_operator_replay_is_audited_and_no_acceptance(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    directory = tmp_path / "exports"
    directory.mkdir()
    try:
        raw_id = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, directory)
        enqueue_capture_receipt(store.connection, raw_id)
        result = process_outbox(
            store.connection,
            directory,
            transport=FailedTransport(),
            expected_custodian_id="external-vault",
            independently_pinned_ack_sha256="0" * 64,
            max_attempts=1,
        )
        assert result[0].state == "manual_review"
        replay = requeue_after_operator_review(
            store.connection,
            raw_id,
            operator_id="reviewer-1",
            reason="external custodian status checked separately",
        )
        assert replay.previous_attempt_count == 1
        state = store.connection.execute(
            "SELECT state,attempt_count FROM capture_custody_outbox WHERE raw_id=?",
            (raw_id,),
        ).fetchone()
        assert state == ("pending", 0)
        audit = store.connection.execute(
            "SELECT operator_id,reason,previous_attempt_count "
            "FROM capture_custody_replay_events WHERE id=?",
            (replay.replay_event_id,),
        ).fetchone()
        assert audit == (
            "reviewer-1",
            "external custodian status checked separately",
            1,
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute("DELETE FROM capture_custody_replay_events")
        with pytest.raises(ValueError, match="only quarantined"):
            requeue_after_operator_review(
                store.connection,
                raw_id,
                operator_id="reviewer-1",
                reason="duplicate review",
            )
    finally:
        store.close()


def test_replay_requires_explicit_operator_and_reason(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    try:
        with pytest.raises(ValueError, match="required"):
            requeue_after_operator_review(
                store.connection, 1, operator_id=" ", reason="reviewed"
            )
        with pytest.raises(ValueError, match="required"):
            requeue_after_operator_review(
                store.connection, 1, operator_id="reviewer", reason=""
            )
    finally:
        store.close()
