"""Read-only raw-source custody values and offline integrity checks; no external I/O or writes."""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field

MAX_RAW_SOURCE_BYTES = 16 * 1024 * 1024
SCHEMA_VERSION = "capture_raw_source_custody_v1"


class RawSourceRejected(ValueError):
    """Fixed rejection messages never contain response bodies or database errors."""


def _namespace(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", value) is None:
        raise RawSourceRejected("invalid archive namespace")


def _body(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise RawSourceRejected("raw source must be bytes")
    if len(value) > MAX_RAW_SOURCE_BYTES:
        raise RawSourceRejected("raw source exceeds maximum size")
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class RawSourceCustodyContract:
    """Verified local bytes bound to an explicit archive and existing receipt."""

    archive_namespace: str
    raw_id: int
    raw_sha256: str
    run_id: str
    provider: str
    scope: str
    collected_at: str
    source_date: str | None
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _namespace(self.archive_namespace)
        if type(self.raw_id) is not int or self.raw_id <= 0:
            raise RawSourceRejected("invalid raw source ID")
        if any(
            not isinstance(value, str)
            for value in (self.run_id, self.provider, self.scope, self.collected_at)
        ) or (self.source_date is not None and not isinstance(self.source_date, str)):
            raise RawSourceRejected("invalid raw source metadata types")
        computed_digest = _body(self.body)
        if (
            not isinstance(self.raw_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.raw_sha256) is None
            or computed_digest != self.raw_sha256
        ):
            raise RawSourceRejected("raw source checksum mismatch")

    @property
    def byte_length(self) -> int:
        return len(self.body)

    def receipt_payload(self) -> dict[str, str | int | None]:
        """Existing receipt fields, with no source body embedded or transformed."""
        return {
            "schemaVersion": "raw_capture_receipt_v1",
            "rawId": self.raw_id,
            "runId": self.run_id,
            "provider": self.provider,
            "scope": self.scope,
            "collectedAt": self.collected_at,
            "sourceDate": self.source_date,
            "rawSha256": self.raw_sha256,
        }

    @property
    def receipt_sha256(self) -> str:
        # Identical serialization to capture_evidence_sink; source bytes are never JSON.
        data = (
            json.dumps(self.receipt_payload(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        return hashlib.sha256(data).hexdigest()

    @property
    def receipt_idempotency_key(self) -> str:
        material = f"capture-custody-v1:{self.raw_id}:{self.receipt_sha256}".encode()
        return hashlib.sha256(material).hexdigest()

    @property
    def identifier(self) -> str:
        envelope = {
            "schemaVersion": SCHEMA_VERSION,
            "archiveNamespace": self.archive_namespace,
            "byteLength": self.byte_length,
            "receipt": self.receipt_payload(),
        }
        return hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def source_object_key(self) -> str:
        return (
            f"evidence/{self.archive_namespace}/raw-source-v1/"
            f"raw-{self.raw_id}/{self.identifier}.bin"
        )

    @property
    def receipt_object_key(self) -> str:
        """Existing receipt key for the same evidence namespace, unchanged."""
        return (
            f"evidence/{self.archive_namespace}/raw-{self.raw_id}/"
            f"{self.receipt_idempotency_key}.json"
        )

    def object_metadata(self) -> dict[str, str]:
        """Proposed future adapter metadata; contains no raw response body."""
        return {
            "schema-version": SCHEMA_VERSION,
            "archive-namespace": self.archive_namespace,
            "raw-id": str(self.raw_id),
            "raw-sha256": self.raw_sha256,
            "byte-length": str(self.byte_length),
            "source-identifier": self.identifier,
            "receipt-sha256": self.receipt_sha256,
            "receipt-idempotency-key": self.receipt_idempotency_key,
        }


def prepare_raw_source_custody(
    connection: sqlite3.Connection, raw_id: int, *, archive_namespace: str
) -> RawSourceCustodyContract:
    """Read an existing BLOB and matching receipt in one bounded SQLite snapshot."""
    _namespace(archive_namespace)
    if type(raw_id) is not int or raw_id <= 0:
        raise RawSourceRejected("invalid raw source ID")
    try:
        row = connection.execute(
            "SELECT r.run_id,r.provider,r.ticker,r.collected_at,r.source_date,r.sha256,"
            "typeof(r.body),length(r.body),"
            "CASE WHEN typeof(r.body)='blob' AND length(r.body)<=? THEN r.body END,"
            "c.schema_version,c.run_id,c.provider,c.scope,c.collected_at,"
            "c.source_date,c.raw_sha256 "
            "FROM raw_pages r LEFT JOIN raw_capture_receipts c ON c.raw_id=r.id WHERE r.id=?",
            (MAX_RAW_SOURCE_BYTES, raw_id),
        ).fetchone()
    except sqlite3.Error:
        raise RawSourceRejected("raw source database read failed") from None
    if row is None:
        raise RawSourceRejected("raw source missing")
    run, provider, scope, observed, day, digest, kind, size, body = row[:9]
    if kind != "blob":
        raise RawSourceRejected("raw source must be a SQLite BLOB")
    if type(size) is not int or size > MAX_RAW_SOURCE_BYTES:
        raise RawSourceRejected("raw source exceeds maximum size")
    if row[9] is None:
        raise RawSourceRejected("capture receipt missing")
    if tuple(row[9:]) != ("raw_capture_receipt_v1", run, provider, scope, observed, day, digest):
        raise RawSourceRejected("capture receipt metadata mismatch")
    return RawSourceCustodyContract(
        archive_namespace, raw_id, digest, run, provider, scope, observed, day, body
    )


@dataclass(frozen=True)
class RawSourceReadbackReview:
    source_identifier: str
    version_id: str
    raw_sha256: str
    byte_length: int
    status: str = field(default="content_consistent_unverified", init=False)
    independent_custody_verified: bool = field(default=False, init=False)
    inference_authorized: bool = field(default=False, init=False)


def verify_raw_source_readback(
    expected: RawSourceCustodyContract, received: bytes, *, version_id: str
) -> RawSourceReadbackReview:
    """Compare supplied bytes offline; a supplied VersionId is not remote proof."""
    if (
        not isinstance(version_id, str)
        or not version_id.strip()
        or version_id == "null"
        or len(version_id) > 1024
    ):
        raise RawSourceRejected("valid raw source VersionId required")
    digest = _body(received)
    if digest != expected.raw_sha256 or len(received) != expected.byte_length:
        raise RawSourceRejected("raw source readback checksum mismatch")
    return RawSourceReadbackReview(expected.identifier, version_id, digest, len(received))
