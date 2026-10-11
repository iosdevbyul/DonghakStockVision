"""Approved HTTPS custody workflow integration without network side effects."""

from datetime import UTC, date, datetime
from pathlib import Path
from urllib.request import Request

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_approved_session import (
    create_approved_custody_session,
)
from donghak_stock_vision.storage.capture_custody_outbox import enqueue_capture_receipt
from donghak_stock_vision.storage.capture_evidence_sink import export_capture_receipt
from donghak_stock_vision.storage.sqlite import SQLiteStore


def test_approved_submit_and_lookup_audit_without_acceptance(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.sqlite3")
    destination = tmp_path / "exports"
    destination.mkdir()
    seen: list[str] = []

    def fake(request: Request, timeout: float) -> tuple[int, bytes]:
        seen.append(request.get_method())
        return 200, b"unauthenticated-status"

    try:
        raw_id = store.archive(
            "run",
            "krx",
            "005930",
            RawPage(b"body", datetime(2026, 10, 10, tzinfo=UTC), date(2026, 10, 9)),
        )
        export_capture_receipt(store.connection, raw_id, destination)
        enqueue_capture_receipt(store.connection, raw_id)
        session = create_approved_custody_session(
            "https://custodian.example/receipts",
            approved_hostname="custodian.example",
            execute=fake,
        )
        submitted = session.execute(store.connection, destination, raw_id, operation="submit")
        looked_up = session.execute(store.connection, destination, raw_id, operation="lookup")
        assert seen == ["PUT", "GET"]
        assert submitted.observation.idempotency_key == looked_up.observation.idempotency_key
        assert not submitted.observation.independent_custody_verified
        assert not looked_up.observation.inference_authorized
        assert store.connection.execute(
            "SELECT state, attempt_count FROM capture_custody_outbox WHERE raw_id=?",
            (raw_id,),
        ).fetchone() == ("pending", 0)
    finally:
        store.close()


def test_unapproved_session_cannot_contact_endpoint() -> None:
    called = False

    def fake(request: Request, timeout: float) -> tuple[int, bytes]:
        nonlocal called
        called = True
        return 200, b"response"

    with pytest.raises(ValueError, match="approved"):
        create_approved_custody_session(
            "https://other.example/receipts",
            approved_hostname="custodian.example",
            execute=fake,
        )
    assert not called
