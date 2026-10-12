"""Explicit S3 custody adapter; storage verification never promotes A-2 authority."""

import base64
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from donghak_stock_vision.storage.capture_custody_lookup_audit import (
    AuditedCustodyLookup,
    lookup_outbox_and_audit,
)
from donghak_stock_vision.storage.capture_custody_readback import (
    CustodyReadbackReview,
    verify_custody_readback,
)
from donghak_stock_vision.storage.capture_custody_submission_audit import (
    AuditedCustodySubmission,
    submit_outbox_and_audit,
)

BUCKET = "donghak-custody-dev"
REGION = "ap-southeast-2"
PROFILE = "donghak-custody"
ROLE_ARN = "arn:aws:iam::101180464666:role/DonghakCustodyWriterRole"
MAX_RECEIPT_BYTES = 65536


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object_retention(self, **kwargs: Any) -> dict[str, Any]: ...


class S3CustodyError(ValueError):
    """Fixed public error text, never SDK exception or credential content."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(raw_id: int, digest: str) -> str:
    return _digest(f"capture-custody-v1:{raw_id}:{digest}".encode())


@dataclass(frozen=True)
class S3CustodyObject:
    bucket: str
    key: str
    version_id: str
    receipt_sha256: str
    retain_until: datetime
    content: bytes = field(repr=False)

    def observation(self) -> bytes:
        return json.dumps(
            {
                "schemaVersion": "s3_custody_observation_v1",
                "bucket": self.bucket,
                "key": self.key,
                "versionId": self.version_id,
                "receiptSha256": self.receipt_sha256,
                "retentionMode": "GOVERNANCE",
                "retainUntil": self.retain_until.isoformat(),
                "independentCustodyVerified": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True)
class S3CustodyTransport:
    """One bounded receipt per deterministic key, with exact-version readback."""

    client: S3Client = field(repr=False)
    prefix: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"(?:evidence|dev-tests)/[A-Za-z0-9/_-]+/", self.prefix)
            or "//" in self.prefix
            or len(self.prefix) > 256
        ):
            raise ValueError("explicit evidence or dev-tests namespace required")

    def object_key(self, *, raw_id: int, idempotency_key: str) -> str:
        if (
            type(raw_id) is not int
            or raw_id <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", idempotency_key)
        ):
            raise ValueError("invalid custody submission identity")
        return f"{self.prefix}raw-{raw_id}/{idempotency_key}.json"

    def inspect(self, *, raw_id: int, idempotency_key: str) -> S3CustodyObject | None:
        key = self.object_key(raw_id=raw_id, idempotency_key=idempotency_key)
        try:
            # Listing gives authenticated API absence without granting ListBucket.
            # Multiple versions or a delete marker indicate an external mutation.
            listing = self.client.list_object_versions(Bucket=BUCKET, Prefix=key, MaxKeys=2)
            versions = [v for v in listing.get("Versions", []) if v["Key"] == key]
            markers = [v for v in listing.get("DeleteMarkers", []) if v["Key"] == key]
            if markers or listing.get("IsTruncated") or len(versions) > 1:
                raise S3CustodyError("ambiguous custody object versions")
            if not versions:
                return None
            version = versions[0]
            version_id = version.get("VersionId")
            if not version_id or version_id == "null" or not version.get("IsLatest"):
                raise S3CustodyError("valid latest object version required")
            response = self.client.get_object(
                Bucket=BUCKET, Key=key, VersionId=version_id, ChecksumMode="ENABLED"
            )
            body = response["Body"]
            try:
                content = body.read(MAX_RECEIPT_BYTES + 1)
            finally:
                body.close()
            digest = _digest(content)
            metadata = response.get("Metadata", {})
            checksum = base64.b64encode(hashlib.sha256(content).digest()).decode()
            if (
                not content
                or len(content) > MAX_RECEIPT_BYTES
                or response.get("ContentLength") != len(content)
                or response.get("VersionId") != version_id
                or response.get("ChecksumSHA256") != checksum
                or metadata.get("receipt-sha256") != digest
                or metadata.get("raw-id") != str(raw_id)
                or metadata.get("idempotency-key") != idempotency_key
                or _identity(raw_id, digest) != idempotency_key
                or json.loads(content).get("rawId") != raw_id
            ):
                raise S3CustodyError("custody object integrity mismatch")
            retention = self.client.get_object_retention(
                Bucket=BUCKET, Key=key, VersionId=version_id
            )["Retention"]
            until = retention.get("RetainUntilDate")
            if (
                retention.get("Mode") != "GOVERNANCE"
                or not isinstance(until, datetime)
                or until.utcoffset() is None
                or until <= datetime.now(UTC)
            ):
                raise S3CustodyError("active GOVERNANCE retention required")
            return S3CustodyObject(BUCKET, key, version_id, digest, until, content)
        except S3CustodyError:
            raise
        except Exception:
            raise S3CustodyError("S3 custody inspection failed") from None

    def submit(self, receipt_bytes: bytes, *, raw_id: int, idempotency_key: str) -> bytes:
        key = self.object_key(raw_id=raw_id, idempotency_key=idempotency_key)
        if (
            not isinstance(receipt_bytes, bytes)
            or not receipt_bytes
            or len(receipt_bytes) > MAX_RECEIPT_BYTES
            or _identity(raw_id, _digest(receipt_bytes)) != idempotency_key
        ):
            raise S3CustodyError("receipt does not match submission identity")
        try:
            if json.loads(receipt_bytes).get("rawId") != raw_id:
                raise S3CustodyError("receipt raw ID mismatch")
        except (ValueError, AttributeError):
            raise S3CustodyError("invalid custody receipt") from None
        try:
            self.client.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=receipt_bytes,
                ContentType="application/json",
                ChecksumSHA256=base64.b64encode(hashlib.sha256(receipt_bytes).digest()).decode(),
                Metadata={
                    "receipt-sha256": _digest(receipt_bytes),
                    "raw-id": str(raw_id),
                    "idempotency-key": idempotency_key,
                },
                IfNoneMatch="*",
            )
        except Exception as exc:
            # A lost response can be retried: 412 leads to the same pinned readback.
            response = getattr(exc, "response", {})
            if response.get("Error", {}).get("Code") != "PreconditionFailed":
                raise S3CustodyError("S3 custody upload failed") from None
        stored = self.inspect(raw_id=raw_id, idempotency_key=idempotency_key)
        if stored is None or stored.content != receipt_bytes:
            raise S3CustodyError("uploaded custody receipt not verified")
        return stored.observation()

    def lookup(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        stored = self.inspect(raw_id=raw_id, idempotency_key=idempotency_key)
        return stored.observation() if stored is not None else None

    def retrieve(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        stored = self.inspect(raw_id=raw_id, idempotency_key=idempotency_key)
        return stored.content if stored is not None else None


S3_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_custody_s3_versions (
    id INTEGER PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES capture_custody_outbox(raw_id),
    operation TEXT NOT NULL CHECK (operation IN ('submit','lookup')),
    observation_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS capture_custody_s3_no_update
BEFORE UPDATE ON capture_custody_s3_versions
BEGIN SELECT RAISE(ABORT, 'S3 custody audit immutable'); END;
CREATE TRIGGER IF NOT EXISTS capture_custody_s3_no_delete
BEFORE DELETE ON capture_custody_s3_versions
BEGIN SELECT RAISE(ABORT, 'S3 custody audit immutable'); END;
"""


@dataclass(frozen=True)
class S3CustodySession:
    transport: S3CustodyTransport

    def execute(
        self,
        connection: sqlite3.Connection,
        destination: Path,
        raw_id: int,
        *,
        operation: Literal["submit", "lookup"],
    ) -> AuditedCustodySubmission | AuditedCustodyLookup:
        # Save the exact version observation from the audited transport call, never
        # re-query a potentially different version after recording the audit event.
        observations: list[bytes] = []
        base = self.transport

        class RecordingTransport:
            def submit(self, data: bytes, *, raw_id: int, idempotency_key: str) -> bytes:
                result = base.submit(data, raw_id=raw_id, idempotency_key=idempotency_key)
                observations.append(result)
                return result

            def lookup(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
                result = base.lookup(raw_id=raw_id, idempotency_key=idempotency_key)
                if result is not None:
                    observations.append(result)
                return result

        result: AuditedCustodySubmission | AuditedCustodyLookup
        if operation == "submit":
            result = submit_outbox_and_audit(
                connection, destination, raw_id, transport=RecordingTransport()
            )
        elif operation == "lookup":
            result = lookup_outbox_and_audit(
                connection, destination, raw_id, transport=RecordingTransport()
            )
        else:
            raise ValueError("unsupported custody operation")
        connection.executescript(S3_AUDIT_SCHEMA)
        with connection:
            for observation in observations:
                connection.execute(
                    "INSERT INTO capture_custody_s3_versions "
                    "(raw_id,operation,observation_json,observed_at) VALUES (?,?,?,?)",
                    (raw_id, operation, observation.decode(), datetime.now(UTC).isoformat()),
                )
        return result

    def verify_readback(
        self, connection: sqlite3.Connection, destination: Path, raw_id: int
    ) -> CustodyReadbackReview:
        return verify_custody_readback(connection, destination, raw_id, transport=self.transport)


def create_s3_custody_session(*, prefix: str) -> S3CustodySession:
    """Use only the dedicated AssumeRole profile; never default/admin fallback."""
    # Validate before credentials or network access. Optional SDK remains lazy.
    S3CustodyTransport(client=None, prefix=prefix)  # type: ignore[arg-type]
    try:
        import boto3
        from botocore.config import Config

        session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        profile = session._session.full_config["profiles"][PROFILE]
        if profile.get("role_arn") != ROLE_ARN or profile.get("source_profile") != "default":
            raise S3CustodyError("dedicated custody AssumeRole profile required")
        config = Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"mode": "standard", "total_max_attempts": 3},
        )
        identity = session.client("sts", config=config).get_caller_identity()
        if not identity["Arn"].startswith(
            "arn:aws:sts::101180464666:assumed-role/DonghakCustodyWriterRole/"
        ):
            raise S3CustodyError("unexpected custody runtime identity")
        client = session.client("s3", config=config)
        return S3CustodySession(S3CustodyTransport(client=client, prefix=prefix))
    except S3CustodyError:
        raise
    except Exception:
        raise S3CustodyError("custody AWS profile unavailable") from None
