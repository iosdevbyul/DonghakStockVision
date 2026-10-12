"""Offline raw S3 write, version, bounded stream and retention verification."""

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError
from tests.custody_s3_fake import MemoryS3, checksum

from donghak_stock_vision.storage.capture_raw_source_contract import (
    MAX_RAW_SOURCE_BYTES,
    RawSourceCustodyContract,
)
from donghak_stock_vision.storage.capture_raw_source_s3 import (
    READ_CHUNK_BYTES,
    RawSourceS3Adapter,
    RawSourceS3Error,
    create_raw_source_s3_session,
)


def source(
    body: bytes = b"synthetic\x00\xff", namespace: str = "offline-archive"
) -> RawSourceCustodyContract:
    import hashlib

    return RawSourceCustodyContract(
        namespace,
        1,
        hashlib.sha256(body).hexdigest(),
        "synthetic-run",
        "synthetic",
        "synthetic-scope",
        "2026-10-12T09:00:00+00:00",
        None,
        body,
    )


def adapter(client: MemoryS3) -> RawSourceS3Adapter:
    return RawSourceS3Adapter(client, "dev-tests/offline-raw/")


def test_conditional_upload_exact_bytes_and_duplicate_without_another_put() -> None:
    client = MemoryS3()
    raw = source()
    first = adapter(client).submit(raw)
    second = adapter(client).submit(raw, expected_version_id=first.version_id)
    assert first == second
    assert len(client.objects) == 1
    assert sum(op == "put" for op, _ in client.calls) == 1
    put = next(args for op, args in client.calls if op == "put")
    assert put["Body"] is raw.body
    assert put["IfNoneMatch"] == "*"
    assert put["ChecksumAlgorithm"] == "SHA256"
    assert put["ChecksumSHA256"] == checksum(raw.body)
    assert put["Metadata"] == raw.object_metadata()
    assert not any(k.startswith("ObjectLock") for k in put)
    assert not first.independent_custody_verified and not first.inference_authorized
    assert client.body is not None and client.body.closed
    for op, args in client.calls:
        if op in ("get", "retention"):
            assert args["VersionId"] == first.version_id


@pytest.mark.parametrize("body", [b"", b"x" * 65537, b"x" * MAX_RAW_SOURCE_BYTES])
def test_empty_large_and_maximum_source_roundtrip(body: bytes) -> None:
    client = MemoryS3()
    obj = adapter(client).submit(source(body))
    assert obj.byte_length == len(body)


def test_namespace_and_test_boundary_preserve_original_contract_key() -> None:
    client = MemoryS3()
    one = source(namespace="archive-one")
    two = source(namespace="archive-two")
    production = RawSourceS3Adapter(client)
    assert production.object_key(one) == one.source_object_key
    assert adapter(client).object_key(one) == "dev-tests/offline-raw/" + one.source_object_key
    assert production.object_key(one) != production.object_key(two)
    assert production.object_key(one) != one.receipt_object_key
    adapter(client).submit(one)
    adapter(client).submit(two)
    assert len(client.objects) == 2


def test_lost_response_is_error_then_lookup_recovers_same_version() -> None:
    client = MemoryS3()
    client.lost_response = True
    with pytest.raises(RawSourceS3Error, match="reconcile"):
        adapter(client).submit(source())
    recovered = adapter(client).inspect(source())
    assert recovered is not None
    assert adapter(client).submit(source()) == recovered
    assert len(client.objects) == 1
    assert sum(op == "put" for op, _ in client.calls) == 1


def test_conditional_race_412_requires_exact_existing_content() -> None:
    class RacingClient(MemoryS3):
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            super().put_object(**kwargs)
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")

    client = RacingClient()
    assert adapter(client).submit(source()).version_id == "version-1"


@pytest.mark.parametrize("code", ["AccessDenied", "SlowDown", "ConditionalRequestConflict"])
def test_storage_errors_are_blocked_and_redacted(code: str) -> None:
    client = MemoryS3()
    client.fail_put = code
    with pytest.raises(RawSourceS3Error, match="upload failed") as error:
        adapter(client).submit(source())
    assert code not in str(error.value)
    assert client.objects == {}


@pytest.mark.parametrize("condition", ["extra_version", "delete_marker", "truncated"])
def test_ambiguous_existing_state_never_writes(condition: str) -> None:
    client = MemoryS3()
    adapter(client).submit(source())
    puts = sum(op == "put" for op, _ in client.calls)
    setattr(client, condition, True)
    with pytest.raises(RawSourceS3Error, match="ambiguous"):
        adapter(client).submit(source())
    assert sum(op == "put" for op, _ in client.calls) == puts


@pytest.mark.parametrize("mutation", ["bytes", "checksum", "metadata", "length", "null_version"])
def test_corrupt_existing_object_never_overwrites(mutation: str) -> None:
    client = MemoryS3()
    adapter(client).submit(source())
    obj = next(iter(client.objects.values()))
    if mutation == "bytes":
        obj["Body"] = b"x" * len(obj["Body"])
    elif mutation == "checksum":
        obj["ChecksumSHA256"] = "wrong"
    elif mutation == "metadata":
        obj["Metadata"]["archive-namespace"] = "other"
    elif mutation == "length":
        obj["Body"] = b"short"
    else:
        obj["VersionId"] = "null"
    with pytest.raises(RawSourceS3Error):
        adapter(client).submit(source())
    assert sum(op == "put" for op, _ in client.calls) == 1


@pytest.mark.parametrize("condition", ["missing", "expired", "naive", "wrong_mode"])
def test_active_governance_required(condition: str) -> None:
    client = MemoryS3()
    adapter(client).submit(source())
    if condition == "missing":
        client.mode = ""
    elif condition == "expired":
        client.until = datetime.now(UTC) - timedelta(seconds=1)
    elif condition == "naive":
        client.until = datetime(2026, 1, 1)
    else:
        client.mode = "COMPLIANCE"
    with pytest.raises(RawSourceS3Error, match="GOVERNANCE"):
        adapter(client).inspect(source())


def test_previous_version_missing_or_changed_is_blocked() -> None:
    client = MemoryS3()
    previous = adapter(client).submit(source())
    obj = next(iter(client.objects.values()))
    obj["VersionId"] = "changed-version"
    with pytest.raises(RawSourceS3Error, match="VersionId changed"):
        adapter(client).inspect(source(), expected_version_id=previous.version_id)
    client.objects.clear()
    with pytest.raises(RawSourceS3Error, match="version missing"):
        adapter(client).submit(source(), expected_version_id=previous.version_id)
    assert sum(op == "put" for op, _ in client.calls) == 1


def test_put_response_version_mismatch_is_blocked() -> None:
    class WrongVersion(MemoryS3):
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            super().put_object(**kwargs)
            return {"VersionId": "wrong-version"}

    with pytest.raises(RawSourceS3Error, match="VersionId changed"):
        adapter(WrongVersion()).submit(source())


def test_version_changed_during_readback_is_blocked() -> None:
    class ChangedDuringRead(MemoryS3):
        mutate = False

        def get_object_retention(self, **kwargs: Any) -> dict[str, Any]:
            result = super().get_object_retention(**kwargs)
            if self.mutate:
                self.objects[kwargs["Key"]]["VersionId"] = "other-version"
            return result

    client = ChangedDuringRead()
    adapter(client).submit(source())
    client.mutate = True
    with pytest.raises(RawSourceS3Error, match="changed during readback"):
        adapter(client).inspect(source())


def test_get_response_version_mismatch_closes_stream() -> None:
    class WrongGetVersion(MemoryS3):
        mutate = False

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            result = super().get_object(**kwargs)
            if self.mutate:
                result["VersionId"] = "wrong-version"
            return result

    client = WrongGetVersion()
    adapter(client).submit(source())
    client.mutate = True
    with pytest.raises(RawSourceS3Error, match="version mismatch"):
        adapter(client).inspect(source())
    assert client.body is not None and client.body.closed


class ChunkStream(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.requests: list[int] = []

    def read(self, size: int | None = -1) -> bytes:
        assert size is not None and 0 < size <= READ_CHUNK_BYTES
        self.requests.append(size)
        # Simulate legal short reads; do not confuse one short read with EOF.
        return super().read(min(size, 17))


def test_chunked_read_is_bounded_handles_short_reads_and_closes() -> None:
    class ChunkClient(MemoryS3):
        stream: ChunkStream

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            response = super().get_object(**kwargs)
            response["Body"].close()
            self.stream = ChunkStream(self.objects[kwargs["Key"]]["Body"])
            response["Body"] = self.stream
            return response

    client = ChunkClient()
    adapter(client).submit(source(b"x" * 70000))
    assert client.stream.closed
    assert len(client.stream.requests) > 1


@pytest.mark.parametrize(
    "prefix", ["evidence/a/", "dev-tests/", "dev-tests/../", "dev-tests/a/b/", None]
)
def test_invalid_test_prefix_never_creates_aws_session(prefix: Any) -> None:
    with pytest.raises(RawSourceS3Error, match="prefix"):
        create_raw_source_s3_session(test_prefix=prefix)


def test_factory_reuses_only_existing_sts_profile_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def unavailable(*, prefix: str) -> None:
        calls.append(prefix)
        raise RuntimeError("synthetic-secret-token")

    monkeypatch.setattr(
        "donghak_stock_vision.storage.capture_raw_source_s3.create_s3_custody_session", unavailable
    )
    with pytest.raises(RawSourceS3Error) as error:
        create_raw_source_s3_session(test_prefix="dev-tests/offline-raw/")
    assert calls == ["dev-tests/offline-raw/"]
    assert "synthetic-secret-token" not in str(error.value)


def test_read_error_or_unexpected_extra_bytes_close_stream() -> None:
    class BrokenStream(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise RuntimeError("synthetic-private-body")

    class BrokenClient(MemoryS3):
        broken = False

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            result = super().get_object(**kwargs)
            if self.broken:
                result["Body"].close()
                self.body = BrokenStream(self.objects[kwargs["Key"]]["Body"])
                result["Body"] = self.body
            return result

    client = BrokenClient()
    adapter(client).submit(source())
    client.broken = True
    with pytest.raises(RawSourceS3Error, match="readback failed") as error:
        adapter(client).inspect(source())
    assert "synthetic-private-body" not in str(error.value)
    assert client.body is not None and client.body.closed


def test_extra_stream_bytes_and_premature_eof_are_rejected() -> None:
    class WrongStreamClient(MemoryS3):
        suffix = b""
        trim = False

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            result = super().get_object(**kwargs)
            data = self.objects[kwargs["Key"]]["Body"]
            result["Body"].close()
            self.body = io.BytesIO(data[:-1] if self.trim else data + self.suffix)
            result["Body"] = self.body
            return result

    client = WrongStreamClient()
    adapter(client).submit(source())
    client.suffix = b"unexpected"
    with pytest.raises(RawSourceS3Error, match="exceeds expected size"):
        adapter(client).inspect(source())
    assert client.body is not None and client.body.closed
    client.suffix = b""
    client.trim = True
    with pytest.raises(RawSourceS3Error, match="length mismatch"):
        adapter(client).inspect(source())


def test_list_permission_denial_and_get_failure_are_not_absence() -> None:
    class DeniedList(MemoryS3):
        def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectVersions")

    with pytest.raises(RawSourceS3Error, match="version lookup failed"):
        adapter(DeniedList()).inspect(source())
    client = MemoryS3()
    adapter(client).submit(source())
    client.fail_get = "synthetic-secret-sdk-message"
    with pytest.raises(RawSourceS3Error, match="readback failed") as error:
        adapter(client).inspect(source())
    assert "synthetic-secret" not in str(error.value)


def test_missing_upload_version_is_blocked_and_can_be_reconciled() -> None:
    class MissingVersion(MemoryS3):
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            super().put_object(**kwargs)
            return {}

    client = MissingVersion()
    with pytest.raises(RawSourceS3Error, match="VersionId required"):
        adapter(client).submit(source())
    assert adapter(client).inspect(source()) is not None
    assert sum(op == "put" for op, _ in client.calls) == 1


def test_actual_sdk_request_shapes_are_validated_offline() -> None:
    import boto3
    from botocore.stub import Stubber

    from donghak_stock_vision.storage.capture_custody_s3 import BUCKET

    # Dummy credentials only; Stubber prevents network and validates SDK models.
    client = boto3.client(
        "s3",
        region_name="ap-southeast-2",
        aws_access_key_id="offline",
        aws_secret_access_key="offline",
    )
    raw = source()
    transport = RawSourceS3Adapter(client, "dev-tests/stubbed-raw/")
    key = transport.object_key(raw)
    listed = {"Bucket": BUCKET, "Prefix": key, "MaxKeys": 2}
    version_listing = {"Versions": [{"Key": key, "VersionId": "stub-version", "IsLatest": True}]}
    with Stubber(client) as stub:
        stub.add_response("list_object_versions", {}, listed)
        stub.add_response(
            "put_object",
            {"VersionId": "stub-version"},
            {
                "Bucket": BUCKET,
                "Key": key,
                "Body": raw.body,
                "ContentType": "application/octet-stream",
                "ContentLength": raw.byte_length,
                "ChecksumAlgorithm": "SHA256",
                "ChecksumSHA256": checksum(raw.body),
                "Metadata": raw.object_metadata(),
                "IfNoneMatch": "*",
            },
        )
        stub.add_response("list_object_versions", version_listing, listed)
        stub.add_response(
            "get_object",
            {
                "Body": io.BytesIO(raw.body),
                "VersionId": "stub-version",
                "ContentLength": raw.byte_length,
                "ChecksumSHA256": checksum(raw.body),
                "Metadata": raw.object_metadata(),
            },
            {"Bucket": BUCKET, "Key": key, "VersionId": "stub-version", "ChecksumMode": "ENABLED"},
        )
        stub.add_response(
            "get_object_retention",
            {
                "Retention": {
                    "Mode": "GOVERNANCE",
                    "RetainUntilDate": datetime.now(UTC) + timedelta(days=1),
                }
            },
            {"Bucket": BUCKET, "Key": key, "VersionId": "stub-version"},
        )
        stub.add_response("list_object_versions", version_listing, listed)
        assert transport.submit(raw).version_id == "stub-version"
        stub.assert_no_pending_responses()
