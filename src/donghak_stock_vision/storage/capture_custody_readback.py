"""Byte-for-byte readback checks; content consistency is not authenticated custody."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from donghak_stock_vision.storage.capture_custody_idempotency import (
    prepare_custody_submission_identity,
)


class CustodyReadbackTransport(Protocol):
    """Provider adapter fetches stored bytes rather than an unverified status claim."""

    def retrieve(self, *, raw_id: int, idempotency_key: str) -> bytes | None: ...


@dataclass(frozen=True)
class CustodyReadbackReview:
    raw_id: int
    status: str
    blocker: str | None
    receipt_sha256: str | None
    retrieved_sha256: str | None
    independent_custody_verified: bool = False
    inference_authorized: bool = False


def verify_custody_readback(
    connection: sqlite3.Connection,
    destination: Path,
    raw_id: int,
    *,
    transport: CustodyReadbackTransport,
) -> CustodyReadbackReview:
    """Compare returned bytes with a verified export; never authenticate provider."""
    try:
        identity = prepare_custody_submission_identity(connection, destination, raw_id)
    except (OSError, ValueError, sqlite3.Error):
        return CustodyReadbackReview(raw_id, "blocked", "export_not_verified", None, None)
    try:
        received = transport.retrieve(raw_id=raw_id, idempotency_key=identity.idempotency_key)
    except Exception:
        return CustodyReadbackReview(
            raw_id, "blocked", "custody_readback_failed", identity.receipt_sha256, None
        )
    if received is None:
        return CustodyReadbackReview(
            raw_id, "blocked", "remote_absence_unverified", identity.receipt_sha256, None
        )
    if not isinstance(received, bytes):
        return CustodyReadbackReview(
            raw_id, "blocked", "invalid_readback", identity.receipt_sha256, None
        )
    retrieved_digest = hashlib.sha256(received).hexdigest()
    if retrieved_digest != identity.receipt_sha256:
        return CustodyReadbackReview(
            raw_id,
            "blocked",
            "readback_digest_mismatch",
            identity.receipt_sha256,
            retrieved_digest,
        )
    return CustodyReadbackReview(
        raw_id,
        "content_consistent_unverified",
        "custodian_not_authenticated",
        identity.receipt_sha256,
        retrieved_digest,
    )
