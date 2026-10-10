"""Offline custody transport tests do not exercise live services or grant authority."""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_transport import submit_capture_receipt
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


class MockCustodian:
    def submit(self, receipt_bytes: bytes, *, raw_id: int) -> bytes:
        return json.dumps(
            {
                "schemaVersion": "capture_custody_ack_v1",
                "custodianId": "vault-test",
                "receiptId": "test-1",
                "rawId": raw_id,
                "acceptedAt": "2026-10-10T12:00:00+00:00",
                "receiptSha256": hashlib.sha256(receipt_bytes).hexdigest(),
            }
        ).encode()


class FailingCustodian:
    def submit(self, receipt_bytes: bytes, *, raw_id: int) -> bytes:
        raise RuntimeError("credential-must-not-appear")


def test_consistent_claim_still_unverified(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.db")
    destination = tmp_path / "exports"
    destination.mkdir()
    try:
        raw_id = store.archive(
            "run", "krx", "005930",
            RawPage(b"raw", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, destination)
        exported = (destination / f"raw-{raw_id}.json").read_bytes()
        expected_ack = MockCustodian().submit(exported, raw_id=raw_id)
        result = submit_capture_receipt(
            store.connection, destination, raw_id,
            transport=MockCustodian(),
            expected_custodian_id="vault-test",
            independently_pinned_ack_sha256=hashlib.sha256(expected_ack).hexdigest(),
        )
        assert result.status == "claim_consistent_unverified"
        assert not result.custodian_authenticated
        assert not result.independent_custody_verified
        assert not result.inference_authorized
    finally:
        store.close()


def test_missing_export_never_calls_transport(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.db")
    destination = tmp_path / "exports"
    destination.mkdir()
    try:
        result = submit_capture_receipt(
            store.connection, destination, 999,
            transport=FailingCustodian(),
            expected_custodian_id="vault-test",
            independently_pinned_ack_sha256="0" * 64,
        )
        assert result.blockers == ("export_not_verified",)
    finally:
        store.close()


def test_transport_error_is_redacted(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.db")
    destination = tmp_path / "exports"
    destination.mkdir()
    try:
        raw_id = store.archive(
            "run", "krx", "005930",
            RawPage(b"raw", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, destination)
        result = submit_capture_receipt(
            store.connection, destination, raw_id,
            transport=FailingCustodian(),
            expected_custodian_id="vault-test",
            independently_pinned_ack_sha256="0" * 64,
        )
        assert result.blockers == ("custody_transport_failed",)
        assert "credential" not in str(result)
    finally:
        store.close()
