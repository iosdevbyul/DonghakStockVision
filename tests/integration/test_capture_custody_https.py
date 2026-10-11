"""HTTPS custody adapter never interprets remote claims as accepted custody."""

import pytest

from donghak_stock_vision.storage.capture_custody_https import (
    MAX_RESPONSE_BYTES,
    HTTPSCustodyTransport,
    create_approved_https_custody_transport,
)

KEY = "a" * 64


def test_https_adapter_passes_stable_identity_for_put_and_get() -> None:
    seen: list[tuple[str, str, str | None, bytes | None]] = []

    def fake(request: object, timeout: float) -> tuple[int, bytes]:
        from urllib.request import Request

        assert isinstance(request, Request)
        seen.append(
            (
                request.get_method(),
                request.full_url,
                request.get_header("Idempotency-key"),
                bytes(request.data) if isinstance(request.data, bytes) else None,
            )
        )
        return 200, b"unverified-claim"

    adapter = HTTPSCustodyTransport("https://custodian.example/receipts", execute=fake)
    assert adapter.submit(b"receipt", raw_id=8, idempotency_key=KEY) == b"unverified-claim"
    assert adapter.lookup(raw_id=8, idempotency_key=KEY) == b"unverified-claim"
    assert seen == [
        ("PUT", "https://custodian.example/receipts/8", KEY, b"receipt"),
        ("GET", "https://custodian.example/receipts/8", KEY, None),
    ]


def test_lookup_not_found_is_not_acceptance() -> None:
    adapter = HTTPSCustodyTransport(
        "https://custodian.example/receipts",
        execute=lambda request, timeout: (404, b"missing"),
    )
    assert adapter.lookup(raw_id=1, idempotency_key=KEY) is None
    with pytest.raises(ValueError, match="valid response"):
        adapter.submit(b"receipt", raw_id=1, idempotency_key=KEY)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://custodian.example/receipts",
        "https://user:pass@custodian.example/receipts",
        "https://custodian.example/receipts?token=secret",
        "https://custodian.example/receipts/",
    ],
)
def test_invalid_endpoint_is_rejected(endpoint: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HTTPSCustodyTransport(endpoint)


def test_oversized_response_is_rejected() -> None:
    adapter = HTTPSCustodyTransport(
        "https://custodian.example/receipts",
        execute=lambda request, timeout: (200, b"x" * (MAX_RESPONSE_BYTES + 1)),
    )
    with pytest.raises(ValueError, match="size limit"):
        adapter.lookup(raw_id=1, idempotency_key=KEY)


def test_approved_hostname_requires_exact_match() -> None:
    transport = create_approved_https_custody_transport(
        "https://custodian.example/receipts", approved_hostname="custodian.example"
    )
    assert transport.endpoint == "https://custodian.example/receipts"
    for endpoint in (
        "https://custodian.example.attacker.com/receipts",
        "https://other.example/receipts",
        "https://custodian.example:8443/receipts",
        "https://localhost/receipts",
        "https://127.0.0.1/receipts",
    ):
        with pytest.raises(ValueError, match="approved"):
            create_approved_https_custody_transport(endpoint, approved_hostname="custodian.example")


def test_unapproved_hostname_rejected_before_network() -> None:
    called = False

    def fake(request: object, timeout: float) -> tuple[int, bytes]:
        nonlocal called
        called = True
        return 200, b"response"

    with pytest.raises(ValueError, match="approved"):
        create_approved_https_custody_transport(
            "https://attacker.example/receipts",
            approved_hostname="custodian.example",
            execute=fake,
        )
    assert not called
