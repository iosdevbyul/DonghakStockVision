"""Opt-in idempotent remote submission and status lookup boundary."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from donghak_stock_vision.storage.capture_custody_idempotency import (
    prepare_custody_submission_identity,
)


class IdempotentCustodyTransport(Protocol):
    """External implementer must enforce deduplication and secure status lookup."""

    def submit(self, receipt_bytes: bytes, *, raw_id: int, idempotency_key: str) -> bytes: ...

    def lookup(self, *, raw_id: int, idempotency_key: str) -> bytes | None: ...


@dataclass(frozen=True)
class CustodyTransportObservation:
    raw_id: int
    idempotency_key: str | None
    operation: Literal["submit", "lookup"]
    status: Literal["claim_received_unverified", "not_found_unverified", "blocked"]
    blockers: tuple[str, ...]
    custodian_authenticated: bool = False
    independent_custody_verified: bool = False
    inference_authorized: bool = False


def observe_idempotent_custody(
    connection: sqlite3.Connection,
    destination: Path,
    raw_id: int,
    *,
    transport: IdempotentCustodyTransport,
    operation: Literal["submit", "lookup"],
) -> CustodyTransportObservation:
    """Pass a stable key to an explicit adapter; never infer authenticated acceptance."""
    if operation not in ("submit", "lookup"):
        raise ValueError("unsupported custody operation")
    try:
        identity = prepare_custody_submission_identity(connection, destination, raw_id)
    except (OSError, ValueError, sqlite3.Error):
        return CustodyTransportObservation(
            raw_id, None, operation, "blocked", ("export_not_verified",)
        )
    try:
        response: bytes | None
        if operation == "submit":
            receipt_bytes = (destination / f"raw-{raw_id}.json").read_bytes()
            if hashlib.sha256(receipt_bytes).hexdigest() != identity.receipt_sha256:
                return CustodyTransportObservation(
                    raw_id,
                    identity.idempotency_key,
                    operation,
                    "blocked",
                    ("export_changed_during_submission",),
                )
            response = transport.submit(
                receipt_bytes, raw_id=raw_id, idempotency_key=identity.idempotency_key
            )
        else:
            response = transport.lookup(raw_id=raw_id, idempotency_key=identity.idempotency_key)
    except Exception:
        return CustodyTransportObservation(
            raw_id, identity.idempotency_key, operation, "blocked", ("custody_transport_failed",)
        )
    if operation == "lookup" and response is None:
        return CustodyTransportObservation(
            raw_id,
            identity.idempotency_key,
            operation,
            "not_found_unverified",
            ("remote_absence_not_authenticated",),
        )
    if not isinstance(response, bytes) or not response:
        return CustodyTransportObservation(
            raw_id, identity.idempotency_key, operation, "blocked", ("invalid_custody_response",)
        )
    return CustodyTransportObservation(
        raw_id,
        identity.idempotency_key,
        operation,
        "claim_received_unverified",
        ("custodian_identity_not_authenticated", "independent_custody_not_verified"),
    )
