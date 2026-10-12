"""Explicitly approved dev-only S3 test. Objects are retained and never deleted."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.integration.test_capture_custody_s3 import prepare

from donghak_stock_vision.storage.capture_custody_idempotency import (
    prepare_custody_submission_identity,
)
from donghak_stock_vision.storage.capture_custody_s3 import create_s3_custody_session
from donghak_stock_vision.storage.sqlite import SQLiteStore


@pytest.mark.live
def test_approved_dev_s3_roundtrip(tmp_path: Path) -> None:
    prefix = os.environ.get("DSV_S3_LIVE_PREFIX", "")
    if os.environ.get("DSV_S3_LIVE_APPROVED") != "yes" or not prefix.startswith("dev-tests/"):
        pytest.skip("explicit approval and unique dev-tests prefix required")
    # Factory validates both the dedicated profile configuration and actual STS role
    # before a PutObject can run. No administrator fallback is supported.
    session = create_s3_custody_session(prefix=prefix)
    db = tmp_path / "market.sqlite3"
    store = SQLiteStore(db)
    destination = tmp_path / "exports"
    try:
        raw_id = prepare(store, destination)
        started = datetime.now(UTC)
        first = session.execute(store.connection, destination, raw_id, operation="submit")
        assert first.observation.status == "claim_received_unverified"
        store.close()
        store = SQLiteStore(db)
        session = create_s3_custody_session(prefix=prefix)
        replay = session.execute(store.connection, destination, raw_id, operation="submit")
        lookup = session.execute(store.connection, destination, raw_id, operation="lookup")
        assert replay.observation.status == lookup.observation.status == first.observation.status
        readback = session.verify_readback(store.connection, destination, raw_id)
        assert readback.status == "content_consistent_unverified"
        assert readback.receipt_sha256 == readback.retrieved_sha256
        assert not readback.independent_custody_verified and not readback.inference_authorized
        identity = prepare_custody_submission_identity(store.connection, destination, raw_id)
        obj = session.transport.inspect(raw_id=raw_id, idempotency_key=identity.idempotency_key)
        assert obj is not None
        assert started + timedelta(hours=23, minutes=55) <= obj.retain_until
        assert obj.retain_until <= datetime.now(UTC) + timedelta(days=1, minutes=5)
        claims = store.connection.execute(
            "SELECT observation_json FROM capture_custody_s3_versions ORDER BY id"
        ).fetchall()
        assert len(claims) == 3
        assert {json.loads(row[0])["versionId"] for row in claims} == {obj.version_id}
        # Persist only public storage verification results, never credential material.
        report_path = os.environ.get("DSV_S3_REPORT_PATH")
        if report_path:
            report = json.loads(obj.observation())
            report.update(
                profile="donghak-custody",
                region="ap-southeast-2",
                readbackSha256=readback.retrieved_sha256,
                readbackMatched=True,
                versions=1,
                restartReplaySameVersion=True,
                observedAt=datetime.now(UTC).isoformat(),
            )
            Path(report_path).write_text(json.dumps(report, indent=2) + "\n")
    finally:
        store.close()
