"""Explicitly approved synthetic raw-only S3 test; never deletes remote objects."""

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_custody_outbox import enqueue_capture_receipt
from donghak_stock_vision.storage.capture_custody_s3 import S3Client
from donghak_stock_vision.storage.capture_raw_source_s3 import (
    RawSourceS3Adapter,
    RawSourceS3Session,
    create_raw_source_s3_session,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore


class LostApplicationResponse:
    """Drop one successful application's response; delegate to its STS-checked client."""

    def __init__(self, client: S3Client) -> None:
        self.client = client

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.client.put_object(**kwargs)
        raise RuntimeError("synthetic lost application response")

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        return self.client.list_object_versions(**kwargs)

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        return self.client.get_object(**kwargs)

    def get_object_retention(self, **kwargs: Any) -> dict[str, Any]:
        return self.client.get_object_retention(**kwargs)


@pytest.mark.live
def test_approved_raw_only_roundtrip_and_response_loss(tmp_path: Path) -> None:
    prefix = os.environ.get("DSV_RAW_S3_LIVE_PREFIX", "")
    if os.environ.get("DSV_RAW_S3_LIVE_APPROVED") != "yes" or not prefix.startswith("dev-tests/"):
        pytest.skip("explicit approval and unique raw-source dev-tests prefix required")
    factory = create_raw_source_s3_session(test_prefix=prefix)
    path = tmp_path / "synthetic.sqlite3"
    store = SQLiteStore(path)
    observations = []
    try:
        ids = []
        for index in range(2):
            # Non-sensitive arbitrary binary data, >64 KiB, no KRX source data.
            body = (f"synthetic-raw-source-{index}\x00".encode() + b"\xff") * 4000
            body = body[:71680]
            raw_id = store.archive(
                "synthetic-raw-source-test",
                "synthetic",
                "synthetic-scope",
                RawPage(body, datetime.now(UTC), None),
            )
            enqueue_capture_receipt(store.connection, raw_id)
            ids.append(raw_id)
        started = datetime.now(UTC)
        first = factory.execute(
            store.connection, ids[0], archive_namespace="synthetic-archive", operation="submit"
        )
        assert first.status == "content_consistent_unverified" and first.observation is not None
        drop = RawSourceS3Session(
            RawSourceS3Adapter(LostApplicationResponse(factory.adapter.client), prefix)
        )
        failed = drop.execute(
            store.connection, ids[1], archive_namespace="synthetic-archive", operation="submit"
        )
        assert failed.status == "blocked" and failed.observation is None
        store.close()
        store = SQLiteStore(path)
        restarted = create_raw_source_s3_session(test_prefix=prefix)
        for raw_id in ids:
            lookup = restarted.execute(
                store.connection, raw_id, archive_namespace="synthetic-archive", operation="lookup"
            )
            replay = restarted.execute(
                store.connection, raw_id, archive_namespace="synthetic-archive", operation="submit"
            )
            readback = restarted.execute(
                store.connection,
                raw_id,
                archive_namespace="synthetic-archive",
                operation="readback",
            )
            assert (
                lookup.status == replay.status == readback.status == "content_consistent_unverified"
            )
            assert lookup.observation == replay.observation == readback.observation
            assert readback.observation is not None
            obj = readback.observation
            if raw_id == ids[0]:
                assert obj.version_id == first.observation.version_id
            assert started + timedelta(hours=23, minutes=55) <= obj.retain_until
            assert obj.retain_until <= datetime.now(UTC) + timedelta(days=1, minutes=5)
            record = asdict(obj)
            record["retain_until"] = obj.retain_until.isoformat()
            record.update(readbackMatched=True, versions=1, restartReplaySameVersion=True)
            observations.append(record)
        report_path = os.environ.get("DSV_RAW_S3_REPORT_PATH")
        if report_path:
            Path(report_path).write_text(
                json.dumps(
                    {
                        "profile": "donghak-custody",
                        "region": "ap-southeast-2",
                        "objects": observations,
                        "lostApplicationResponseRecovered": True,
                        "receiptObjectsUploaded": 0,
                        "syntheticOnly": True,
                        "observedAt": datetime.now(UTC).isoformat(),
                    },
                    indent=2,
                )
                + "\n"
            )
    finally:
        store.close()
