"""Runtime custody bearer credentials remain isolated from stored evidence."""

from urllib.request import Request

import pytest

from donghak_stock_vision.storage.capture_custody_approved_session import (
    create_approved_custody_session,
)
from donghak_stock_vision.storage.capture_custody_https import HTTPSCustodyTransport

KEY = "a" * 64


def test_approved_session_attaches_runtime_token_to_put_and_get() -> None:
    observed: list[tuple[str, str | None]] = []

    def fake(request: Request, timeout: float) -> tuple[int, bytes]:
        observed.append((request.get_method(), request.get_header("Authorization")))
        return 200, b"untrusted-claim"

    session = create_approved_custody_session(
        "https://custodian.example/receipts",
        approved_hostname="custodian.example",
        bearer_token="runtime-secret",
        execute=fake,
    )
    assert session.transport.submit(b"receipt", raw_id=1, idempotency_key=KEY)
    assert session.transport.lookup(raw_id=1, idempotency_key=KEY)
    assert observed == [
        ("PUT", "Bearer runtime-secret"),
        ("GET", "Bearer runtime-secret"),
    ]
    assert "runtime-secret" not in repr(session)
    assert "runtime-secret" not in repr(session.transport)


@pytest.mark.parametrize("token", ["", "space token", "abc\\r\\nX: injected", "한글"])
def test_malformed_runtime_token_rejected(token: str) -> None:
    with pytest.raises(ValueError, match="bearer token"):
        HTTPSCustodyTransport("https://custodian.example/receipts", bearer_token=token)


def test_runtime_auth_is_opt_in() -> None:
    observed: list[str | None] = []

    def fake(request: Request, timeout: float) -> tuple[int, bytes]:
        observed.append(request.get_header("Authorization"))
        return 200, b"untrusted-claim"

    transport = HTTPSCustodyTransport("https://custodian.example/receipts", execute=fake)
    assert transport.lookup(raw_id=1, idempotency_key=KEY)
    assert observed == [None]
