"""Approved custody sessions must preserve the runtime token secrecy boundary."""

from urllib.request import Request

import pytest

from donghak_stock_vision.storage.capture_custody_approved_session import (
    create_approved_custody_session,
)

KEY = "a" * 64
TOKEN = "sensitive-test-token-123"


def test_approved_session_passes_token_for_both_operations() -> None:
    captured: list[tuple[str, str | None]] = []

    def fake(request: Request, timeout: float) -> tuple[int, bytes]:
        captured.append((request.get_method(), request.get_header("Authorization")))
        return 200, b"unverified-response"

    session = create_approved_custody_session(
        "https://custodian.example/receipts",
        approved_hostname="custodian.example",
        bearer_token=TOKEN,
        execute=fake,
    )
    assert session.transport.submit(b"receipt", raw_id=7, idempotency_key=KEY)
    assert session.transport.lookup(raw_id=7, idempotency_key=KEY)
    assert captured == [("PUT", f"Bearer {TOKEN}"), ("GET", f"Bearer {TOKEN}")]
    assert TOKEN not in repr(session)
    assert TOKEN not in repr(session.transport)


def test_failed_remote_authentication_does_not_echo_token() -> None:
    def unauthorized(request: Request, timeout: float) -> tuple[int, bytes]:
        return 401, b"rejected"

    session = create_approved_custody_session(
        "https://custodian.example/receipts",
        approved_hostname="custodian.example",
        bearer_token=TOKEN,
        execute=unauthorized,
    )
    with pytest.raises(ValueError, match="valid response") as error:
        session.transport.submit(b"receipt", raw_id=7, idempotency_key=KEY)
    assert TOKEN not in str(error.value)


def test_approved_session_rejects_invalid_token_before_request() -> None:
    called = False

    def fake(request: Request, timeout: float) -> tuple[int, bytes]:
        nonlocal called
        called = True
        return 200, b"response"

    with pytest.raises(ValueError, match="invalid custody bearer token"):
        create_approved_custody_session(
            "https://custodian.example/receipts",
            approved_hostname="custodian.example",
            bearer_token="bad token with spaces",
            execute=fake,
        )
    assert not called
