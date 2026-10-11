"""Explicit operator-approved custody HTTPS submission and lookup workflow."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.request import Request

from donghak_stock_vision.storage.capture_custody_https import (
    HTTPSCustodyTransport,
    create_approved_https_custody_transport,
)
from donghak_stock_vision.storage.capture_custody_lookup_audit import (
    AuditedCustodyLookup,
    lookup_outbox_and_audit,
)
from donghak_stock_vision.storage.capture_custody_submission_audit import (
    AuditedCustodySubmission,
    submit_outbox_and_audit,
)


@dataclass(frozen=True)
class ApprovedCustodySession:
    """Approved destination is caller-supplied, not fetched from an untrusted response."""

    transport: HTTPSCustodyTransport

    def execute(
        self,
        connection: sqlite3.Connection,
        destination: Path,
        raw_id: int,
        *,
        operation: Literal["submit", "lookup"],
    ) -> AuditedCustodySubmission | AuditedCustodyLookup:
        if operation == "submit":
            return submit_outbox_and_audit(
                connection, destination, raw_id, transport=self.transport
            )
        if operation == "lookup":
            return lookup_outbox_and_audit(
                connection, destination, raw_id, transport=self.transport
            )
        raise ValueError("unsupported custody operation")


def create_approved_custody_session(
    endpoint: str,
    *,
    approved_hostname: str,
    timeout: float = 10.0,
    bearer_token: str | None = None,
    execute: Callable[[Request, float], tuple[int, bytes]] | None = None,
) -> ApprovedCustodySession:
    """Build a session through the explicit destination approval gate."""
    if execute is None:
        transport = create_approved_https_custody_transport(
            endpoint,
            approved_hostname=approved_hostname,
            timeout=timeout,
            bearer_token=bearer_token,
        )
    else:
        transport = create_approved_https_custody_transport(
            endpoint,
            approved_hostname=approved_hostname,
            timeout=timeout,
            bearer_token=bearer_token,
            execute=execute,
        )
    return ApprovedCustodySession(transport)


def create_authenticated_custody_session(
    endpoint: str,
    *,
    approved_hostname: str,
    bearer_token: str,
    timeout: float = 10.0,
    execute: Callable[[Request, float], tuple[int, bytes]] | None = None,
) -> ApprovedCustodySession:
    """Require an explicit runtime bearer credential before session construction.

    The credential is supplied by the caller, never read from source,
    configuration defaults, or the custody server's response.
    """
    if not bearer_token:
        raise ValueError("explicit custody bearer credential required")
    return create_approved_custody_session(
        endpoint,
        approved_hostname=approved_hostname,
        timeout=timeout,
        bearer_token=bearer_token,
        execute=execute,
    )
