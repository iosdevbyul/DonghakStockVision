"""Explicit custody transport boundary; receipt claims never imply verified custody."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from donghak_stock_vision.storage.capture_custody_ack import inspect_custody_acknowledgement
from donghak_stock_vision.storage.capture_evidence_sink import verify_exported_capture_receipt


class CustodyTransport(Protocol):
    """Caller-provided transport. Implementations must not be inferred as trusted."""

    def submit(self, receipt_bytes: bytes, *, raw_id: int) -> bytes: ...


@dataclass(frozen=True)
class CustodySubmissionReview:
    raw_id: int
    status: str
    acknowledgement_sha256: str | None
    blockers: tuple[str, ...]
    custodian_authenticated: bool = False
    independent_custody_verified: bool = False
    inference_authorized: bool = False


def submit_capture_receipt(
    connection: sqlite3.Connection,
    destination: Path,
    raw_id: int,
    *,
    transport: CustodyTransport,
    expected_custodian_id: str,
    independently_pinned_ack_sha256: str,
) -> CustodySubmissionReview:
    """Submit preverified bytes; do not persist or trust an unverified acknowledgement."""
    if not verify_exported_capture_receipt(connection, raw_id, destination):
        return CustodySubmissionReview(raw_id, "blocked", None, ("export_not_verified",))
    receipt_bytes = (destination / f"raw-{raw_id}.json").read_bytes()
    try:
        acknowledgement_bytes = transport.submit(receipt_bytes, raw_id=raw_id)
    except Exception:
        # Never include remote exception details; transport errors may leak credentials.
        return CustodySubmissionReview(raw_id, "blocked", None, ("custody_transport_failed",))
    if not isinstance(acknowledgement_bytes, bytes):
        return CustodySubmissionReview(raw_id, "blocked", None, ("invalid_custody_response",))
    review = inspect_custody_acknowledgement(
        acknowledgement_bytes,
        receipt_bytes,
        expected_raw_id=raw_id,
        expected_custodian_id=expected_custodian_id,
        independently_pinned_ack_sha256=independently_pinned_ack_sha256,
    )
    return CustodySubmissionReview(
        raw_id,
        "claim_consistent_unverified" if review.content_consistent else "blocked",
        hashlib.sha256(acknowledgement_bytes).hexdigest(),
        review.blockers,
    )
