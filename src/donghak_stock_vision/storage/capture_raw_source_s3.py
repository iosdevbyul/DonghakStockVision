"""Explicit raw-byte S3 storage and separate append-only local observations."""

import base64
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from donghak_stock_vision.storage.capture_custody_s3 import (
    BUCKET,
    S3Client,
    create_s3_custody_session,
)
from donghak_stock_vision.storage.capture_raw_source_contract import (
    RawSourceCustodyContract,
    prepare_raw_source_custody,
)

READ_CHUNK_BYTES = 64 * 1024
Operation = Literal["submit", "lookup", "readback"]
Status = Literal["content_consistent_unverified", "not_found_unverified", "blocked"]


class RawSourceS3Error(ValueError):
    """Redacted failures never include SDK errors, credentials or source bodies."""


def _test_prefix(prefix: str) -> None:
    if not isinstance(prefix, str) or (
        prefix and not re.fullmatch(r"dev-tests/[a-z0-9][a-z0-9_-]{0,127}/", prefix)
    ):
        raise RawSourceS3Error("invalid raw source test prefix")


def _version(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value == "null" or len(value) > 1024:
        raise RawSourceS3Error("valid raw source VersionId required")
    return value


@dataclass(frozen=True)
class RawSourceS3Observation:
    source_identifier: str
    archive_namespace: str
    raw_id: int
    key: str
    raw_sha256: str
    byte_length: int
    version_id: str
    retain_until: datetime
    bucket: str = field(default=BUCKET, init=False)
    retention_mode: str = field(default="GOVERNANCE", init=False)
    independent_custody_verified: bool = field(default=False, init=False)
    inference_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True)
class RawSourceS3Adapter:
    client: S3Client = field(repr=False)
    test_prefix: str = ""

    def __post_init__(self) -> None:
        _test_prefix(self.test_prefix)

    def object_key(self, source: RawSourceCustodyContract) -> str:
        # Production uses the PR #93 key verbatim. Tests isolate the complete
        # unchanged contract path under an explicit dev-tests run boundary.
        return self.test_prefix + source.source_object_key

    def _listed_version(self, key: str) -> str | None:
        try:
            listing = self.client.list_object_versions(Bucket=BUCKET, Prefix=key, MaxKeys=2)
            versions = [v for v in listing.get("Versions", []) if v["Key"] == key]
            markers = [v for v in listing.get("DeleteMarkers", []) if v["Key"] == key]
            if listing.get("IsTruncated") or markers or len(versions) > 1:
                raise RawSourceS3Error("ambiguous raw source version history")
            if not versions:
                return None
            if versions[0].get("IsLatest") is not True:
                raise RawSourceS3Error("latest raw source version required")
            return _version(versions[0].get("VersionId"))
        except RawSourceS3Error:
            raise
        except Exception:
            raise RawSourceS3Error("raw source version lookup failed") from None

    def inspect(
        self, source: RawSourceCustodyContract, *, expected_version_id: str | None = None
    ) -> RawSourceS3Observation | None:
        key = self.object_key(source)
        if expected_version_id is not None:
            _version(expected_version_id)
        version = self._listed_version(key)
        if version is None:
            if expected_version_id is not None:
                raise RawSourceS3Error("previously observed raw source version missing")
            return None
        if expected_version_id is not None and version != expected_version_id:
            raise RawSourceS3Error("raw source VersionId changed")
        try:
            response = self.client.get_object(
                Bucket=BUCKET, Key=key, VersionId=version, ChecksumMode="ENABLED"
            )
            stream = response["Body"]
            try:
                checksum = base64.b64encode(bytes.fromhex(source.raw_sha256)).decode()
                if (
                    response.get("VersionId") != version
                    or response.get("ContentLength") != source.byte_length
                    or response.get("Metadata") != source.object_metadata()
                    or response.get("ChecksumSHA256") != checksum
                ):
                    raise RawSourceS3Error("raw source object metadata or version mismatch")
                digest = hashlib.sha256()
                offset = 0
                while True:
                    requested = min(READ_CHUNK_BYTES, source.byte_length - offset + 1)
                    chunk = stream.read(requested)
                    if not isinstance(chunk, bytes) or len(chunk) > requested:
                        raise RawSourceS3Error("invalid raw source read stream")
                    if not chunk:
                        break
                    if offset + len(chunk) > source.byte_length:
                        raise RawSourceS3Error("raw source readback exceeds expected size")
                    if chunk != source.body[offset : offset + len(chunk)]:
                        raise RawSourceS3Error("raw source readback bytes mismatch")
                    digest.update(chunk)
                    offset += len(chunk)
                if offset != source.byte_length or digest.hexdigest() != source.raw_sha256:
                    raise RawSourceS3Error("raw source readback checksum or length mismatch")
            finally:
                stream.close()
            retention = self.client.get_object_retention(
                Bucket=BUCKET, Key=key, VersionId=version
            ).get("Retention", {})
            until = retention.get("RetainUntilDate")
            if (
                retention.get("Mode") != "GOVERNANCE"
                or not isinstance(until, datetime)
                or until.utcoffset() is None
                or until <= datetime.now(UTC)
            ):
                raise RawSourceS3Error("active raw source GOVERNANCE retention required")
            # Detect history changes during readback; no observation claims a
            # transaction spanning S3 reads or protects against later mutations.
            if self._listed_version(key) != version:
                raise RawSourceS3Error("raw source VersionId changed during readback")
            return RawSourceS3Observation(
                source.identifier,
                source.archive_namespace,
                source.raw_id,
                key,
                source.raw_sha256,
                source.byte_length,
                version,
                until,
            )
        except RawSourceS3Error:
            raise
        except Exception:
            raise RawSourceS3Error("raw source S3 readback failed") from None

    def submit(
        self, source: RawSourceCustodyContract, *, expected_version_id: str | None = None
    ) -> RawSourceS3Observation:
        # Resolve duplicates/lost responses before another PUT. Delete markers,
        # corrupt existing data and expired protection must never be resurrected.
        existing = self.inspect(source, expected_version_id=expected_version_id)
        if existing is not None:
            return existing
        uploaded_version: str | None = None
        try:
            response = self.client.put_object(
                Bucket=BUCKET,
                Key=self.object_key(source),
                Body=source.body,
                ContentType="application/octet-stream",
                ContentLength=source.byte_length,
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(source.raw_sha256)).decode(),
                Metadata=source.object_metadata(),
                IfNoneMatch="*",
            )
            uploaded_version = _version(response.get("VersionId"))
        except RawSourceS3Error:
            raise
        except Exception as error:
            response = getattr(error, "response", {})
            if response.get("Error", {}).get("Code") != "PreconditionFailed":
                raise RawSourceS3Error(
                    "raw source S3 upload failed; reconcile before replay"
                ) from None
        stored = self.inspect(source, expected_version_id=uploaded_version)
        if stored is None:
            raise RawSourceS3Error("uploaded raw source not found; reconciliation required")
        return stored


RAW_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_raw_source_s3_events (
    id INTEGER PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES raw_capture_receipts(raw_id),
    archive_namespace TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('submit','lookup','readback')),
    status TEXT NOT NULL CHECK (status IN
        ('content_consistent_unverified','not_found_unverified','blocked')),
    version_id TEXT,
    retention_mode TEXT,
    retain_until TEXT,
    blocker TEXT,
    observed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS capture_raw_source_s3_no_update
BEFORE UPDATE ON capture_raw_source_s3_events
BEGIN SELECT RAISE(ABORT, 'raw source S3 audit immutable'); END;
CREATE TRIGGER IF NOT EXISTS capture_raw_source_s3_no_delete
BEFORE DELETE ON capture_raw_source_s3_events
BEGIN SELECT RAISE(ABORT, 'raw source S3 audit immutable'); END;
"""


@dataclass(frozen=True)
class AuditedRawSourceS3Result:
    event_id: int
    operation: Operation
    status: Status
    observation: RawSourceS3Observation | None
    blocker: str | None
    independent_custody_verified: bool = field(default=False, init=False)
    inference_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True)
class RawSourceS3Session:
    adapter: RawSourceS3Adapter

    def execute(
        self,
        connection: sqlite3.Connection,
        raw_id: int,
        *,
        archive_namespace: str,
        operation: Operation,
    ) -> AuditedRawSourceS3Result:
        if operation not in ("submit", "lookup", "readback"):
            raise RawSourceS3Error("unsupported raw source operation")
        source = prepare_raw_source_custody(connection, raw_id, archive_namespace=archive_namespace)
        if connection.in_transaction:
            raise RawSourceS3Error("raw source operation requires a committed local archive")
        try:
            connection.executescript(RAW_AUDIT_SCHEMA)
            versions = connection.execute(
                "SELECT DISTINCT version_id FROM capture_raw_source_s3_events "
                "WHERE archive_namespace=? AND source_identifier=? AND object_key=? "
                "AND status='content_consistent_unverified'",
                (archive_namespace, source.identifier, self.adapter.object_key(source)),
            ).fetchall()
            if len(versions) > 1:
                raise RawSourceS3Error("conflicting local raw source version observations")
            expected_version = versions[0][0] if versions else None
            # The raw workflow does not enqueue or mutate the receipt Outbox.
            outbox_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_custody_outbox'"
            ).fetchone()
            manual_review = (
                outbox_exists
                and connection.execute(
                    "SELECT 1 FROM capture_custody_outbox WHERE raw_id=? AND state='manual_review'",
                    (raw_id,),
                ).fetchone()
            )
        except sqlite3.Error:
            raise RawSourceS3Error("raw source audit initialization failed") from None
        observation = None
        blocker = None
        status: Status
        try:
            if operation == "submit" and manual_review:
                raise RawSourceS3Error("manual review requires separate replay authorization")
            if operation == "submit":
                observation = self.adapter.submit(source, expected_version_id=expected_version)
            else:
                observation = self.adapter.inspect(source, expected_version_id=expected_version)
            status = "content_consistent_unverified" if observation else "not_found_unverified"
            if observation is None:
                blocker = "remote raw source absence unverified"
        except RawSourceS3Error as error:
            status = "blocked"
            blocker = str(error)
        try:
            with connection:
                cursor = connection.execute(
                    "INSERT INTO capture_raw_source_s3_events "
                    "(raw_id,archive_namespace,source_identifier,bucket,object_key,raw_sha256,"
                    "byte_length,operation,status,version_id,retention_mode,retain_until,"
                    "blocker,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        raw_id,
                        archive_namespace,
                        source.identifier,
                        BUCKET,
                        self.adapter.object_key(source),
                        source.raw_sha256,
                        source.byte_length,
                        operation,
                        status,
                        observation.version_id if observation else None,
                        observation.retention_mode if observation else None,
                        observation.retain_until.isoformat() if observation else None,
                        blocker,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            if cursor.lastrowid is None:
                raise RawSourceS3Error("raw source audit event not recorded")
        except sqlite3.Error:
            raise RawSourceS3Error(
                "raw source audit persistence failed; reconcile remote state"
            ) from None
        return AuditedRawSourceS3Result(cursor.lastrowid, operation, status, observation, blocker)


def create_raw_source_s3_session(*, test_prefix: str = "") -> RawSourceS3Session:
    """Reuse dedicated-profile STS gate; never fall back to administrator credentials."""
    _test_prefix(test_prefix)
    try:
        receipt_session = create_s3_custody_session(
            prefix=test_prefix or "evidence/raw-source-runtime/"
        )
        return RawSourceS3Session(RawSourceS3Adapter(receipt_session.transport.client, test_prefix))
    except Exception:
        raise RawSourceS3Error("raw source dedicated AWS profile unavailable") from None
