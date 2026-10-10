"""Explicit, fail-closed filesystem export for captured receipts.

This is a separate local storage location, not an independent trust authority.
"""

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CaptureExportResult:
    raw_id: int
    path: Path
    sha256: str


def _receipt_bytes(connection: sqlite3.Connection, raw_id: int) -> bytes:
    row = connection.execute(
        "SELECT c.schema_version,c.run_id,c.provider,c.scope,c.collected_at,"
        "c.source_date,c.raw_sha256,r.body,r.run_id,r.provider,r.ticker,"
        "r.collected_at,r.source_date,r.sha256 "
        "FROM raw_capture_receipts c JOIN raw_pages r ON r.id=c.raw_id "
        "WHERE c.raw_id=?",
        (raw_id,),
    ).fetchone()
    if row is None:
        raise ValueError("capture receipt missing")
    version, run, provider, scope, observed, source_day, digest = row[:7]
    body, raw_run, raw_provider, raw_scope, raw_time, raw_day, raw_digest = row[7:]
    if (
        version != "raw_capture_receipt_v1"
        or (run, provider, scope, observed, source_day, digest)
        != (raw_run, raw_provider, raw_scope, raw_time, raw_day, raw_digest)
        or hashlib.sha256(body).hexdigest() != digest
    ):
        raise ValueError("capture receipt mismatch")
    payload = {
        "schemaVersion": version,
        "rawId": raw_id,
        "runId": run,
        "provider": provider,
        "scope": scope,
        "collectedAt": observed,
        "sourceDate": source_day,
        "rawSha256": digest,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def export_capture_receipt(
    connection: sqlite3.Connection, raw_id: int, destination: Path
) -> CaptureExportResult:
    """Create one non-overwritable receipt file; caller owns retries and delivery policy."""
    if raw_id <= 0:
        raise ValueError("raw ID must be positive")
    data = _receipt_bytes(connection, raw_id)
    digest = hashlib.sha256(data).hexdigest()
    # The directory must be provisioned independently. Do not silently create it.
    if not destination.is_dir() or destination.is_symlink():
        raise ValueError("capture evidence destination must be an existing real directory")
    path = destination / f"raw-{raw_id}.json"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError("capture receipt already exported") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Ensure the new directory entry is flushed on POSIX filesystems.
        directory_descriptor = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError:
        # Retain the file if fsync fails: do not assert durable delivery.
        raise
    return CaptureExportResult(raw_id, path, digest)


def verify_exported_capture_receipt(
    connection: sqlite3.Connection, raw_id: int, destination: Path
) -> bool:
    """Verify the exported bytes against current local archive; not provenance authentication."""
    if raw_id <= 0 or not destination.is_dir() or destination.is_symlink():
        return False
    path = destination / f"raw-{raw_id}.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return path.read_bytes() == _receipt_bytes(connection, raw_id)
    except (OSError, ValueError, sqlite3.Error):
        return False
