"""Real pinned capture replay and raw custody validators; no remote authority fixtures."""

import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_custody_versions import original

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.capture_evidence_sink import _receipt_bytes
from donghak_stock_vision.storage.capture_raw_source_contract import (
    MAX_RAW_SOURCE_BYTES,
    prepare_raw_source_custody,
)
from donghak_stock_vision.storage.capture_raw_source_s3 import RawSourceS3Observation
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.forward_raw_source_custody import (
    SuppliedRawSourceEvidence,
    assess_forward_raw_source,
    link_forward_raw_source,
    observation_document,
    verify_forward_raw_source_assessment,
)
from donghak_stock_vision.storage.prospective_operational_readiness import CaptureSource

NS = "synthetic-archive"


def assess(c: CaptureSource, **kwargs: Any) -> FrozenJSON:
    return assess_forward_raw_source(c, archive_namespace=NS, **kwargs)


def evidence(c: CaptureSource) -> SuppliedRawSourceEvidence:
    source = link_forward_raw_source(c, archive_namespace=NS).source
    observation = RawSourceS3Observation(
        source.identifier,
        NS,
        source.raw_id,
        source.source_object_key,
        source.raw_sha256,
        source.byte_length,
        "synthetic-version-1",
        datetime(2030, 1, 1, tzinfo=UTC),
    )
    reference = FrozenJSON.freeze(
        {
            "version": "forward_raw_source_object_reference_v1",
            "source_identifier": source.identifier,
            "bucket": observation.bucket,
            "key": source.source_object_key,
            "version_id": observation.version_id,
        }
    )
    return SuppliedRawSourceEvidence(
        observation,
        observation_document(observation).identifier,
        reference,
        reference.identifier,
        source.body,
        source.raw_sha256,
    )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_original_link_contract_identifiers_and_read_only(tmp_path: Path, version: int) -> None:
    c, _ = original(tmp_path, version)
    before = {
        p.relative_to(c.root): sha256(p.read_bytes()).hexdigest()
        for p in c.root.rglob("*")
        if p.is_file()
    }
    with (
        patch("httpx.Client.send", side_effect=AssertionError("provider forbidden")),
        patch(
            "donghak_stock_vision.storage.capture_raw_source_s3.create_s3_custody_session",
            side_effect=AssertionError("AWS forbidden"),
        ),
    ):
        link = link_forward_raw_source(c, archive_namespace=NS)
        with closing(sqlite3.connect(c.root / DB)) as connection:
            expected = prepare_raw_source_custody(
                connection, link.source.raw_id, archive_namespace=NS
            )
            receipt = _receipt_bytes(connection, link.source.raw_id)
        assert link.source == expected
        assert sha256(receipt).hexdigest() == expected.receipt_sha256
        assert link.binding.to_dict()["capture_plan_version"] == f"forward_capture_plan_v{version}"
        assert link.binding.to_dict()["receipt_idempotency_key"] == expected.receipt_idempotency_key
        report = assess(c)
        verify_forward_raw_source_assessment(report, report.identifier, c, archive_namespace=NS)
        doc = report.to_dict()
        assert doc["original_raw_bytes_locally_verified"]
        assert doc["original_capture_manifest_verified"]
        assert doc["original_receipt_identity_verified"]
        assert doc["raw_source_contract_compatible"]
        assert not doc["supplied_object_metadata_consistent"]
        assert doc["supplied_external_readback"] is None
        assert "external_raw_source_readback_missing" in doc["reason_codes"]
        assert doc["network_requests"] == 0 and doc["performance_metrics"] is None
        assert not any(
            doc[k]
            for k in (
                "remote_object_existence_verified",
                "external_upload_verified",
                "independent_custody_verified",
                "publication_authenticated",
                "prediction_existence_time_authenticated",
                "genuine_forward_evidence",
                "pit_qualified",
                "prospective_performance_approved",
                "execution_authorized",
            )
        )
    assert before == {
        p.relative_to(c.root): sha256(p.read_bytes()).hexdigest()
        for p in c.root.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("version", [1, 2, 3])
def test_supplied_consistent_readback_never_promotes_trust(tmp_path: Path, version: int) -> None:
    c, _ = original(tmp_path, version)
    supplied = evidence(c)
    report = assess(c, supplied=supplied, submission_reference="operator-claim-only")
    d = report.to_dict()
    assert d["supplied_object_metadata_consistent"]
    assert d["supplied_external_readback"]["status"] == "content_consistent_unverified"
    assert d["external_submission_configured_or_claimed"]
    assert not d["external_upload_verified"] and not d["independent_custody_verified"]
    verify_forward_raw_source_assessment(
        report,
        report.identifier,
        c,
        archive_namespace=NS,
        supplied=supplied,
        submission_reference="operator-claim-only",
    )
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_forward_raw_source_assessment(report, report.identifier, c, archive_namespace=NS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_id", 999),
        ("archive_namespace", "wrong"),
        ("source_identifier", "0" * 64),
        ("key", "wrong.bin"),
        ("raw_sha256", "0" * 64),
        ("byte_length", 1),
        ("version_id", "wrong-version"),
        ("version_id", "null"),
    ],
)
def test_rehashed_wrong_object_claims(tmp_path: Path, field: str, value: Any) -> None:
    c, _ = original(tmp_path, 1)
    supplied = evidence(c)
    assert supplied.observation is not None
    observation = replace(supplied.observation, **{field: value})
    supplied = replace(
        supplied,
        observation=observation,
        observation_pin=observation_document(observation).identifier,
    )
    with pytest.raises(ValueError):
        assess(c, supplied=supplied)


def test_forged_readback_and_missing_independent_references(tmp_path: Path) -> None:
    c, _ = original(tmp_path, 1)
    supplied = evidence(c)
    with pytest.raises(ValueError, match="checksum mismatch"):
        assess(
            c,
            supplied=replace(
                supplied, readback=b"forged", readback_pin=sha256(b"forged").hexdigest()
            ),
        )
    with pytest.raises(ValueError, match="raw_readback_pin"):
        replace(supplied, readback_pin="0" * 64)
    with pytest.raises(ValueError, match="reference_required"):
        replace(supplied, reference=None, reference_pin=None)
    with pytest.raises(ValueError, match="observation_pin_mismatch"):
        replace(supplied, observation_pin="0" * 64)
    with pytest.raises(ValueError, match="object_reference_pin_mismatch"):
        replace(supplied, reference_pin="0" * 64)
    with pytest.raises(ValueError, match="byte_budget"):
        replace(supplied, readback=b"x" * (MAX_RAW_SOURCE_BYTES + 1))
    assert (
        assess(c, supplied=replace(supplied, readback=None, readback_pin=None)).to_dict()[
            "supplied_external_readback"
        ]
        is None
    )


@pytest.mark.parametrize(
    "sql,args",
    [
        ("UPDATE raw_pages SET body=?", (b"changed",)),
        ("UPDATE raw_pages SET body=zeroblob(?)", (MAX_RAW_SOURCE_BYTES + 1,)),
        ("UPDATE raw_pages SET body='text'", ()),
        ("UPDATE raw_pages SET run_id='wrong:0'", ()),
        ("UPDATE raw_pages SET provider='wrong'", ()),
        ("UPDATE raw_pages SET ticker='wrong'", ()),
        ("UPDATE raw_capture_receipts SET raw_sha256=?", ("0" * 64,)),
        ("UPDATE raw_capture_receipts SET schema_version='unsupported'", ()),
        ("DELETE FROM raw_capture_receipts", ()),
        ("UPDATE forward_responses SET raw_id=999", ()),
        ("UPDATE forward_responses SET sequence=1", ()),
    ],
)
def test_original_integrity_rejections(tmp_path: Path, sql: str, args: tuple[Any, ...]) -> None:
    c, _ = original(tmp_path, 1)
    with closing(sqlite3.connect(c.root / DB)) as connection, connection:
        for trigger in (
            "raw_capture_receipts_no_update",
            "raw_capture_receipts_no_delete",
            "forward_responses_no_UPDATE",
            "forward_responses_no_DELETE",
        ):
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
        connection.execute(sql, args)
    with pytest.raises(ValueError):
        assess(c)


@pytest.mark.parametrize("kind", ["plan", "attempt", "sequence", "pin", "namespace", "root"])
def test_cross_source_substitutions(tmp_path: Path, kind: str) -> None:
    c, _ = original(tmp_path, 1)
    report = assess(c)
    namespace = NS
    if kind == "plan":
        plan = replace(c.plan, market="KOSDAQ")
        c = replace(c, plan=plan, plan_pin=plan.document.identifier)
    elif kind == "attempt":
        c = replace(c, attempt="wrong")
    elif kind == "sequence":
        c = replace(c, sequence=1)
    elif kind == "pin":
        c = replace(c, capture_pin="0" * 64)
    elif kind == "namespace":
        namespace = "wrong-archive"
    else:
        shutil.copytree(c.root, tmp_path / "other")
        c = replace(c, root=tmp_path / "other")
    with pytest.raises(ValueError):
        verify_forward_raw_source_assessment(
            report, report.identifier, c, archive_namespace=namespace
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "unsupported"),
        ("external_upload_verified", True),
        ("independent_custody_verified", True),
        ("performance_metrics", {"return": 1}),
        ("binding_pin", "0" * 64),
        ("original_raw_bytes_locally_verified", False),
    ],
)
def test_rehashed_report_rejected(tmp_path: Path, field: str, value: Any) -> None:
    c, _ = original(tmp_path, 1)
    report = assess(c)
    forged = FrozenJSON.freeze(report.to_dict() | {field: value})
    with pytest.raises(ValueError):
        verify_forward_raw_source_assessment(forged, forged.identifier, c, archive_namespace=NS)
    with pytest.raises(ValueError, match="raw_assessment_pin"):
        verify_forward_raw_source_assessment(forged, report.identifier, c, archive_namespace=NS)


def test_cross_version_plan_and_symlink_rejected(tmp_path: Path) -> None:
    c, _ = original(tmp_path, 1)
    (tmp_path / "v2").mkdir()
    other, _ = original(tmp_path / "v2", 2)
    with pytest.raises(ValueError):
        assess(replace(c, plan=other.plan, plan_pin=other.plan_pin))
    (tmp_path / "alias").symlink_to(c.root, target_is_directory=True)
    with pytest.raises(ValueError, match="pilot_archive_missing"):
        assess(replace(c, root=tmp_path / "alias"))


def test_source_changed_between_capture_verification_and_contract(tmp_path: Path) -> None:
    c, _ = original(tmp_path, 1)
    from donghak_stock_vision.storage.forward_custody import _capture

    def changed(*args: Any, **kwargs: Any) -> Any:
        result = _capture(*args, **kwargs)
        with closing(sqlite3.connect(c.root / DB)) as connection, connection:
            connection.execute("UPDATE raw_pages SET body=?", (b"changed after replay",))
        return result

    with patch("donghak_stock_vision.storage.forward_raw_source_custody._capture", changed):
        with pytest.raises(ValueError, match="checksum mismatch"):
            assess(c)


def test_multiple_original_response_owners_cannot_be_exchanged(tmp_path: Path) -> None:
    import httpx
    from tests.integration.test_forward_capture import execute

    from donghak_stock_vision.forward_capture_cli import fixture_transport, offline_plan

    plan = replace(offline_plan(), max_requests=2)
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, content=b"busy", headers={"Retry-After": "2"})
        return fixture_transport(plan).handle_request(request)

    owner = execute(tmp_path, plan, transport=httpx.MockTransport(respond))
    c = CaptureSource(tmp_path, plan, plan.document.identifier, "a", owner.identifier)
    first = link_forward_raw_source(c, archive_namespace=NS)
    second = link_forward_raw_source(replace(c, sequence=1), archive_namespace=NS)
    assert first.source.body == b"busy" and second.source.body != first.source.body
    assert first.source.raw_id != second.source.raw_id
    report = assess(c)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_forward_raw_source_assessment(
            report,
            report.identifier,
            replace(c, sequence=1),
            archive_namespace=NS,
        )
    with pytest.raises(ValueError, match="source_mismatch"):
        assess(c, supplied=evidence(replace(c, sequence=1)))


def test_compressed_full_market_body_stays_original(tmp_path: Path) -> None:
    import gzip

    from tests.integration.test_krx_recurring import execute, fixture

    plan, ref = fixture(tmp_path)
    owner = execute(tmp_path / "capture", plan, ref, count=3, zipped=True)
    c = CaptureSource(tmp_path / "capture", plan, plan.document.identifier, "a", owner.identifier)
    source = link_forward_raw_source(c, archive_namespace=NS).source
    assert source.body.startswith(b"\x1f\x8b")
    assert gzip.decompress(source.body) != source.body
    assert (
        sha256(source.body).hexdigest() == owner.to_dict()["responses"][0]["receipt"]["rawSha256"]
    )
    assert assess(c, supplied=evidence(c)).to_dict()["supplied_external_readback"]


def test_pin_sequence_and_namespace_validation(tmp_path: Path) -> None:
    c, _ = original(tmp_path, 1)
    for sequence in (True, -1, 999):
        with pytest.raises(ValueError, match="unknown_response"):
            assess(replace(c, sequence=sequence))
    with pytest.raises(ValueError, match="original_plan_pin"):
        assess(replace(c, plan_pin="0" * 64))
    with pytest.raises(ValueError, match="invalid archive namespace"):
        assess_forward_raw_source(c, archive_namespace="../bad")
