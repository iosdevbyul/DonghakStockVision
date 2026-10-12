"""In-memory versioned S3 API double; no credentials or network."""

import base64
import hashlib
import io
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError


class MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_put: str | None = None
        self.fail_get: str | None = None
        self.lost_response = False
        self.mode = "GOVERNANCE"
        self.until = datetime.now(UTC) + timedelta(days=1)
        self.truncated = False
        self.extra_version = False
        self.delete_marker = False
        self.body: io.BytesIO | None = None

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put", kwargs))
        if self.fail_put:
            raise ClientError({"Error": {"Code": self.fail_put}}, "PutObject")
        key = kwargs["Key"]
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[key] = dict(kwargs, VersionId=f"version-{len(self.objects) + 1}")
        if self.lost_response:
            self.lost_response = False
            raise RuntimeError("private credential details")
        return {"VersionId": self.objects[key]["VersionId"]}

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list", kwargs))
        versions = [
            {"Key": k, "VersionId": v["VersionId"], "IsLatest": True}
            for k, v in self.objects.items()
            if k.startswith(kwargs["Prefix"])
        ]
        if self.extra_version and versions:
            versions.append(dict(versions[0], VersionId="older", IsLatest=False))
        return {
            "Versions": versions,
            "IsTruncated": self.truncated,
            "DeleteMarkers": [dict(versions[0])] if versions and self.delete_marker else [],
        }

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get", kwargs))
        if self.fail_get:
            raise RuntimeError(self.fail_get)
        obj = self.objects[kwargs["Key"]]
        self.body = io.BytesIO(obj["Body"])
        return {
            "Body": self.body,
            "VersionId": obj["VersionId"],
            "ContentLength": len(obj["Body"]),
            "Metadata": obj["Metadata"],
            "ChecksumSHA256": obj["ChecksumSHA256"],
        }

    def get_object_retention(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("retention", kwargs))
        return {"Retention": {"Mode": self.mode, "RetainUntilDate": self.until}}


def checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()
