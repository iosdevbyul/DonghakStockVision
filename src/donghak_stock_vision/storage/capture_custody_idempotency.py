"""Deterministic identity for retrying an unchanged exported capture receipt."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from donghak_stock_vision.storage.capture_evidence_sink import verify_exported_capture_receipt


@dataclass(frozen=True)
class CustodySubmissionIdentity:
    raw_id: int
    receipt_sha256: str
    idempotency_key: str


def prepare_custody_submission_identity(
    connection: sqlite3.Connection,
    destination: Path,
    raw_id: int,
) -> CustodySubmissionIdentity:
    """Return a stable deduplication key only for verified, unchanged receipt bytes.

    This key is merely a proposed transport contract. A real custodian must
    independently enforce deduplication and expose an authenticated status API.
    """
    if raw_id <= 0 or not verify_exported_capture_receipt(connection, raw_id, destination):
        raise ValueError("capture receipt export must be verified before submission")
    receipt_bytes = (destination / f"raw-{raw_id}.json").read_bytes()
    # A concurrent file replacement cannot silently change the identity.
    if not verify_exported_capture_receipt(connection, raw_id, destination):
        raise ValueError("capture receipt changed during preparation")
    digest = hashlib.sha256(receipt_bytes).hexdigest()
    payload = json.loads(receipt_bytes)
    if payload.get("rawId") != raw_id:
        raise ValueError("capture receipt identity mismatch")
    key_material = f"capture-custody-v1:{raw_id}:{digest}".encode()
    return CustodySubmissionIdentity(
        raw_id=raw_id,
        receipt_sha256=digest,
        idempotency_key=hashlib.sha256(key_material).hexdigest(),
    )
