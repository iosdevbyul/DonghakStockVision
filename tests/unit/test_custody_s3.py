"""S3 conditional-write, version, checksum and retention safety boundaries."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.custody_s3_fake import MemoryS3, checksum

from donghak_stock_vision.storage.capture_custody_s3 import (
    BUCKET,
    MAX_RECEIPT_BYTES,
    S3CustodyError,
    S3CustodyTransport,
    create_s3_custody_session,
)

DATA = b'{"rawId":1,"schemaVersion":"raw_capture_receipt_v1"}\n'
DIGEST = hashlib.sha256(DATA).hexdigest()
IDENTITY = hashlib.sha256(f"capture-custody-v1:1:{DIGEST}".encode()).hexdigest()


def adapter(client: MemoryS3) -> S3CustodyTransport:
    return S3CustodyTransport(client, "dev-tests/offline/")


def upload(client: MemoryS3) -> bytes:
    return adapter(client).submit(DATA, raw_id=1, idempotency_key=IDENTITY)


def test_duplicate_put_has_one_version_and_exact_version_readback() -> None:
    client = MemoryS3()
    first = upload(client)
    assert upload(client) == first
    assert len(client.objects) == 1
    record = json.loads(first)
    assert record["versionId"] == "version-1"
    assert record["receiptSha256"] == DIGEST
    assert record["independentCustodyVerified"] is False
    assert adapter(client).retrieve(raw_id=1, idempotency_key=IDENTITY) == DATA
    assert client.body is not None and client.body.closed
    for operation, args in client.calls:
        assert args["Bucket"] == BUCKET
        if operation == "put":
            assert args["IfNoneMatch"] == "*"
            assert args["ChecksumSHA256"] == checksum(DATA)
            assert not any(k.startswith("ObjectLock") for k in args)
        if operation in ("get", "retention"):
            assert args["VersionId"] == "version-1"


def test_missing_lookup_is_no_upload() -> None:
    client = MemoryS3()
    assert adapter(client).lookup(raw_id=1, idempotency_key=IDENTITY) is None
    assert adapter(client).retrieve(raw_id=1, idempotency_key=IDENTITY) is None
    assert all(op == "list" for op, _ in client.calls)


def test_lost_upload_response_retry_reconciles_same_version() -> None:
    client = MemoryS3()
    client.lost_response = True
    with pytest.raises(S3CustodyError, match="upload failed") as error:
        upload(client)
    assert "private" not in str(error.value)
    assert json.loads(upload(client))["versionId"] == "version-1"
    assert len(client.objects) == 1


@pytest.mark.parametrize("code", ["AccessDenied", "ConditionalRequestConflict", "SlowDown"])
def test_non_duplicate_put_errors_are_blocked_and_redacted(code: str) -> None:
    client = MemoryS3()
    client.fail_put = code
    with pytest.raises(S3CustodyError, match="upload failed"):
        upload(client)
    assert [op for op, _ in client.calls] == ["put"]


@pytest.mark.parametrize("mutation", ["bytes", "checksum", "metadata", "version"])
def test_tampered_remote_object_fails_closed(mutation: str) -> None:
    client = MemoryS3()
    upload(client)
    obj = next(iter(client.objects.values()))
    if mutation == "bytes":
        obj["Body"] = b"altered"
    elif mutation == "checksum":
        obj["ChecksumSHA256"] = "wrong"
    elif mutation == "metadata":
        obj["Metadata"]["raw-id"] = "2"
    else:
        obj["VersionId"] = "null"
    with pytest.raises(S3CustodyError):
        adapter(client).retrieve(raw_id=1, idempotency_key=IDENTITY)


@pytest.mark.parametrize("condition", ["mode", "expired", "naive", "missing"])
def test_retention_is_mandatory(condition: str) -> None:
    client = MemoryS3()
    upload(client)
    if condition == "mode":
        client.mode = "COMPLIANCE"
    elif condition == "expired":
        client.until = datetime.now(UTC) - timedelta(seconds=1)
    elif condition == "naive":
        client.until = datetime(2026, 1, 1)
    else:
        client.until = None  # type: ignore[assignment]
    with pytest.raises(S3CustodyError, match="retention required"):
        adapter(client).retrieve(raw_id=1, idempotency_key=IDENTITY)


@pytest.mark.parametrize("condition", ["truncated", "extra_version", "delete_marker"])
def test_ambiguous_history_is_blocked(condition: str) -> None:
    client = MemoryS3()
    upload(client)
    setattr(client, condition, True)
    with pytest.raises(S3CustodyError, match="ambiguous"):
        adapter(client).lookup(raw_id=1, idempotency_key=IDENTITY)


@pytest.mark.parametrize(
    "prefix", ["", "production/", "dev-tests/", "dev-tests/../", "evidence/a//"]
)
def test_unsafe_namespace_never_contacts_aws(prefix: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        create_s3_custody_session(prefix=prefix)


@pytest.mark.parametrize("data", [b"", b"x" * (MAX_RECEIPT_BYTES + 1), b'{"rawId":2}'])
def test_invalid_submission_never_uploads(data: bytes) -> None:
    client = MemoryS3()
    with pytest.raises(S3CustodyError):
        adapter(client).submit(data, raw_id=1, idempotency_key=IDENTITY)
    assert client.calls == []


def test_sdk_error_details_are_not_exposed() -> None:
    client = MemoryS3()
    upload(client)
    client.fail_get = "credential-secret"
    with pytest.raises(S3CustodyError) as error:
        adapter(client).retrieve(raw_id=1, idempotency_key=IDENTITY)
    assert "credential-secret" not in str(error.value)


def test_wrong_profile_cannot_create_admin_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["profile_name"] == "donghak-custody"
            self._session = self
            self.full_config = {"profiles": {"donghak-custody": {"role_arn": "admin"}}}

        def client(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("wrong profile must not contact AWS")

    monkeypatch.setattr("boto3.Session", FakeSession)
    with pytest.raises(S3CustodyError, match="AssumeRole"):
        create_s3_custody_session(prefix="dev-tests/offline/")


def test_wrong_sts_role_never_creates_s3_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            self._session = self
            self.full_config = {
                "profiles": {
                    "donghak-custody": {
                        "role_arn": "arn:aws:iam::101180464666:role/DonghakCustodyWriterRole",
                        "source_profile": "default",
                    }
                }
            }

        def client(self, service: str, **kwargs: Any) -> Any:
            assert service == "sts", "unexpected runtime role must not create S3 client"
            return self

        def get_caller_identity(self) -> dict[str, str]:
            return {"Arn": "arn:aws:sts::101180464666:assumed-role/AccountFullAccessRole/session"}

    monkeypatch.setattr("boto3.Session", FakeSession)
    with pytest.raises(S3CustodyError, match="runtime identity"):
        create_s3_custody_session(prefix="dev-tests/offline/")


def test_policy_contains_only_scoped_minimal_allows() -> None:
    from pathlib import Path

    policy = json.loads(
        (
            Path(__file__).parents[2] / "infrastructure/aws/donghak-custody-writer-policy.json"
        ).read_text()
    )
    statements = policy["Statement"]
    assert len(statements) == 2
    assert all(s["Effect"] == "Allow" and "NotAction" not in s for s in statements)
    assert set(statements[0]["Action"]) == {
        "s3:PutObject",
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:GetObjectRetention",
    }
    assert statements[0]["Resource"] == [
        f"arn:aws:s3:::{BUCKET}/evidence/*",
        f"arn:aws:s3:::{BUCKET}/dev-tests/*",
    ]
    assert statements[1]["Action"] == "s3:ListBucketVersions"
    assert statements[1]["Resource"] == f"arn:aws:s3:::{BUCKET}"
    assert statements[1]["Condition"] == {
        "StringLike": {"s3:prefix": ["evidence/*", "dev-tests/*"]}
    }


def test_botocore_validates_wire_requests_without_network() -> None:
    import io

    import boto3
    from botocore.stub import Stubber

    # Non-secret dummy credentials disable provider discovery. Stubber intercepts
    # every operation, validates actual SDK request shapes and never sends traffic.
    client = boto3.client(
        "s3",
        region_name="ap-southeast-2",
        aws_access_key_id="offline",
        aws_secret_access_key="offline",
    )
    transport = S3CustodyTransport(client, "dev-tests/stubber/")
    key = transport.object_key(raw_id=1, idempotency_key=IDENTITY)
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {"VersionId": "sdk-version"},
            {
                "Bucket": BUCKET,
                "Key": key,
                "Body": DATA,
                "ContentType": "application/json",
                "IfNoneMatch": "*",
                "ChecksumSHA256": checksum(DATA),
                "Metadata": {"receipt-sha256": DIGEST, "raw-id": "1", "idempotency-key": IDENTITY},
            },
        )
        stub.add_response(
            "list_object_versions",
            {"Versions": [{"Key": key, "VersionId": "sdk-version", "IsLatest": True}]},
            {"Bucket": BUCKET, "Prefix": key, "MaxKeys": 2},
        )
        stub.add_response(
            "get_object",
            {
                "Body": io.BytesIO(DATA),
                "VersionId": "sdk-version",
                "ContentLength": len(DATA),
                "ChecksumSHA256": checksum(DATA),
                "Metadata": {"receipt-sha256": DIGEST, "raw-id": "1", "idempotency-key": IDENTITY},
            },
            {"Bucket": BUCKET, "Key": key, "VersionId": "sdk-version", "ChecksumMode": "ENABLED"},
        )
        stub.add_response(
            "get_object_retention",
            {
                "Retention": {
                    "Mode": "GOVERNANCE",
                    "RetainUntilDate": datetime.now(UTC) + timedelta(days=1),
                }
            },
            {"Bucket": BUCKET, "Key": key, "VersionId": "sdk-version"},
        )
        assert (
            json.loads(transport.submit(DATA, raw_id=1, idempotency_key=IDENTITY))["versionId"]
            == "sdk-version"
        )
        stub.assert_no_pending_responses()
