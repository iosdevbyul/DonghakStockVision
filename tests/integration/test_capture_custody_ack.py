"""Offline tests for a fail-closed custody acknowledgement review."""

import hashlib
import json
from datetime import UTC, datetime

from donghak_stock_vision.storage.capture_custody_ack import inspect_custody_acknowledgement


def _sample() -> tuple[bytes, bytes, str]:
    receipt = b'{"rawId":1}\n'
    ack = json.dumps(
        {
            "schemaVersion": "capture_custody_ack_v1",
            "custodianId": "external-vault",
            "receiptId": "external-1",
            "rawId": 1,
            "acceptedAt": datetime(2026, 10, 10, tzinfo=UTC).isoformat(),
            "receiptSha256": hashlib.sha256(receipt).hexdigest(),
        }
    ).encode()
    return ack, receipt, hashlib.sha256(ack).hexdigest()


def test_matching_claim_remains_unauthenticated() -> None:
    ack, receipt, pin = _sample()
    review = inspect_custody_acknowledgement(
        ack, receipt, expected_raw_id=1,
        expected_custodian_id="external-vault", independently_pinned_ack_sha256=pin,
    )
    assert review.content_consistent
    assert not review.custodian_authenticated
    assert not review.independent_custody_verified
    assert not review.inference_authorized


def test_changed_receipt_is_rejected() -> None:
    ack, _, pin = _sample()
    review = inspect_custody_acknowledgement(
        ack, b"tampered", expected_raw_id=1,
        expected_custodian_id="external-vault", independently_pinned_ack_sha256=pin,
    )
    assert "custody_ack_receipt_digest_mismatch" in review.blockers


def test_wrong_independent_pin_is_rejected() -> None:
    ack, receipt, _ = _sample()
    review = inspect_custody_acknowledgement(
        ack, receipt, expected_raw_id=1,
        expected_custodian_id="external-vault", independently_pinned_ack_sha256="0" * 64,
    )
    assert not review.content_consistent


def test_wrong_custodian_is_rejected() -> None:
    ack, receipt, pin = _sample()
    review = inspect_custody_acknowledgement(
        ack, receipt, expected_raw_id=1,
        expected_custodian_id="wrong", independently_pinned_ack_sha256=pin,
    )
    assert "custody_ack_identity_mismatch" in review.blockers


def test_naive_acceptance_time_is_invalid() -> None:
    ack, receipt, _ = _sample()
    invalid = ack.replace(b"+00:00", b"")
    review = inspect_custody_acknowledgement(
        invalid, receipt, expected_raw_id=1,
        expected_custodian_id="external-vault",
        independently_pinned_ack_sha256=hashlib.sha256(invalid).hexdigest(),
    )
    assert "invalid_custody_acknowledgement" in review.blockers
