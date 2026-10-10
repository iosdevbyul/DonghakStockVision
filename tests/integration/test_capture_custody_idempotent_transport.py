"""Explicit custody transport idempotency boundary tests."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage import capture_custody_idempotent_transport as custody_transport_module
from donghak_stock_vision.storage.capture_custody_idempotency import CustodySubmissionIdentity
from donghak_stock_vision.storage.capture_custody_idempotent_transport import (
    observe_idempotent_custody,
)
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


class MemoryTransport:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.responses: dict[str, bytes] = {}

    def submit(self, receipt_bytes: bytes, *, raw_id: int, idempotency_key: str) -> bytes:
        self.keys.append(idempotency_key)
        self.responses[idempotency_key] = b"untrusted-receipt"
        return self.responses[idempotency_key]

    def lookup(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        self.keys.append(idempotency_key)
        return self.responses.get(idempotency_key)


def test_submit_and_lookup_share_stable_key_without_authority(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    dest = tmp_path / "exports"
    dest.mkdir()
    try:
        raw_id = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, dest)
        transport = MemoryTransport()
        missing = observe_idempotent_custody(
            store.connection, dest, raw_id, transport=transport, operation="lookup"
        )
        submit = observe_idempotent_custody(
            store.connection, dest, raw_id, transport=transport, operation="submit"
        )
        found = observe_idempotent_custody(
            store.connection, dest, raw_id, transport=transport, operation="lookup"
        )
        assert missing.status == "not_found_unverified"
        assert submit.status == found.status == "claim_received_unverified"
        assert len(set(transport.keys)) == 1
        assert not found.independent_custody_verified
        assert not found.inference_authorized
    finally:
        store.close()


def test_missing_export_does_not_contact_transport(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    dest = tmp_path / "exports"
    dest.mkdir()
    try:
        transport = MemoryTransport()
        result = observe_idempotent_custody(
            store.connection, dest, 999, transport=transport, operation="submit"
        )
        assert result.status == "blocked"
        assert result.blockers == ("export_not_verified",)
        assert transport.keys == []
    finally:
        store.close()


def test_changed_export_after_identity_check_never_submits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    dest = tmp_path / "exports"
    dest.mkdir()
    try:
        raw_id = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, dest)
        original = custody_transport_module.prepare_custody_submission_identity

        def replace_after_check(
            connection: sqlite3.Connection, destination: Path, receipt_id: int
        ) -> CustodySubmissionIdentity:
            identity = original(connection, destination, receipt_id)
            (destination / f"raw-{receipt_id}.json").write_bytes(b"replaced")
            return identity

        monkeypatch.setattr(
            custody_transport_module,
            "prepare_custody_submission_identity",
            replace_after_check,
        )
        transport = MemoryTransport()
        result = observe_idempotent_custody(
            store.connection, dest, raw_id, transport=transport, operation="submit"
        )
        assert result.status == "blocked"
        assert result.blockers == ("export_changed_during_submission",)
        assert transport.keys == []
    finally:
        store.close()
