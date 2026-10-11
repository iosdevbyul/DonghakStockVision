"""Content readback never authorizes independent remote custody."""

from datetime import UTC, date, datetime
from pathlib import Path

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_readback import verify_custody_readback
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


class FakeReadback:
    def __init__(self, result: bytes | None) -> None:
        self.result = result
        self.calls = 0

    def retrieve(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        self.calls += 1
        return self.result


def test_readback_detects_matching_missing_and_changed_bytes(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    destination = tmp_path / "export"
    destination.mkdir()
    try:
        raw_id = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, destination)
        exported = (destination / f"raw-{raw_id}.json").read_bytes()
        matching = verify_custody_readback(
            store.connection, destination, raw_id, transport=FakeReadback(exported)
        )
        assert matching.status == "content_consistent_unverified"
        assert matching.receipt_sha256 == matching.retrieved_sha256
        assert not matching.independent_custody_verified
        assert not matching.inference_authorized
        missing = verify_custody_readback(
            store.connection, destination, raw_id, transport=FakeReadback(None)
        )
        assert missing.blocker == "remote_absence_unverified"
        mismatch = verify_custody_readback(
            store.connection, destination, raw_id, transport=FakeReadback(b"tampered")
        )
        assert mismatch.blocker == "readback_digest_mismatch"
    finally:
        store.close()


def test_unverified_local_export_prevents_remote_request(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    destination = tmp_path / "export"
    destination.mkdir()
    try:
        transport = FakeReadback(b"data")
        result = verify_custody_readback(store.connection, destination, 999, transport=transport)
        assert result.blocker == "export_not_verified"
        assert transport.calls == 0
    finally:
        store.close()
