"""Offline contract regression tests for archived raw source evidence."""

import hashlib
import json
from datetime import date, datetime, timezone

from donghak_stock_vision.api.source_collection_evidence import inspect_source_collection
from donghak_stock_vision.providers.base import RawPage


def _fixture() -> tuple[dict[str, object], RawPage]:
    observed = datetime(2026, 10, 9, 9, tzinfo=timezone.utc)
    raw = RawPage(b'{"rows":[]}', observed, date(2026, 10, 8))
    evidence: dict[str, object] = {
        "schemaVersion": "source_collection_evidence_v1",
        "provider": "krx",
        "scope": "@krx_market:KOSPI",
        "sourceDate": "2026-10-08",
        "collectedAt": observed.isoformat(),
        "runId": "run-1",
        "collectorName": "dsv",
        "collectorVersion": "2.0.0a3",
        "rawSha256": hashlib.sha256(raw.body).hexdigest(),
        "observationKind": "initial",
        "previousRawSha256": None,
        "snapshotId": "snapshot-1",
    }
    return evidence, raw


def _inspect(
    evidence: dict[str, object], raw: RawPage, *, pin_override: str | None = None
) -> tuple[bool, bool, bool, tuple[str, ...]]:
    document = json.dumps(evidence).encode()
    assessment = inspect_source_collection(
        document,
        raw,
        expected_provider="krx",
        expected_scope="@krx_market:KOSPI",
        expected_run_id="run-1",
        expected_snapshot_id="snapshot-1",
        independently_trusted_evidence_sha256=(
            pin_override if pin_override is not None else hashlib.sha256(document).hexdigest()
        ),
    )
    return (
        assessment.content_consistent,
        assessment.provenance_authenticated,
        assessment.inference_authorized,
        assessment.blockers,
    )


def test_matching_evidence_never_grants_authorization() -> None:
    evidence, raw = _fixture()
    consistent, authenticated, authorized, blockers = _inspect(evidence, raw)
    assert consistent
    assert not authenticated
    assert not authorized
    assert "independent_historical_availability_not_attested" in blockers


def test_modified_raw_content_is_rejected() -> None:
    evidence, raw = _fixture()
    modified = RawPage(b'{"rows":[1]}', raw.collected_at, raw.source_date)
    assert "archived_raw_content_mismatch" in _inspect(evidence, modified)[3]


def test_wrong_out_of_band_pin_is_rejected() -> None:
    evidence, raw = _fixture()
    assert "source_collection_evidence_pin_mismatch" in _inspect(
        evidence, raw, pin_override="0" * 64
    )[3]


def test_wrong_snapshot_and_run_are_rejected() -> None:
    evidence, raw = _fixture()
    evidence["snapshotId"] = "wrong"
    evidence["runId"] = "wrong"
    assert "source_collection_identity_mismatch" in _inspect(evidence, raw)[3]


def test_missing_revision_predecessor_is_rejected() -> None:
    evidence, raw = _fixture()
    evidence["observationKind"] = "revision"
    assert "invalid_source_collection_evidence" in _inspect(evidence, raw)[3]


def test_naive_collection_timestamp_is_rejected() -> None:
    evidence, raw = _fixture()
    evidence["collectedAt"] = "2026-10-09T09:00:00"
    assert "invalid_source_collection_evidence" in _inspect(evidence, raw)[3]


def test_page_metadata_mismatch_is_rejected() -> None:
    evidence, raw = _fixture()
    different = RawPage(raw.body, raw.collected_at, date(2026, 10, 7))
    assert "source_collection_page_metadata_mismatch" in _inspect(evidence, different)[3]
