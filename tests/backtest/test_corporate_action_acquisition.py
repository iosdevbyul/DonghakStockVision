"""Corporate-action acquisition planning and replay tests; no live network."""

import gzip
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx

from donghak_stock_vision.backtest.corporate_action_acquisition import (
    build_acquisition_manifest,
    build_document_plan,
    build_security_review_queue,
    inspect_document_sources,
)
from donghak_stock_vision.backtest.dart_transport import DartEvidenceClient
from donghak_stock_vision.data.learning import digest


def wrapped(value: dict[str, Any]) -> dict[str, Any]:
    return {"artifact": value, "fingerprint": digest(value)}


def candidate_document() -> dict[str, Any]:
    artifact = {
        "version": "dart_document_review_queue_v1",
        "candidates": [
            {
                "filing": {"rcept_no": "20240701000001"},
                "repository_ticker": "005930",
                "review_categories": ["split"],
                "lifecycle_review": False,
            },
            {
                "filing": {"rcept_no": "20240701000001"},
                "repository_ticker": "005930",
                "review_categories": ["merger"],
                "lifecycle_review": True,
            },
            {
                "filing": {"rcept_no": "20240702000002"},
                "repository_ticker": None,
                "review_categories": ["division"],
                "lifecycle_review": False,
            },
        ],
    }
    return wrapped(artifact)


def zip_body(text: str = "fixture") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("document.xml", text)
    return output.getvalue()


def mapping_document() -> dict[str, Any]:
    securities: list[dict[str, Any]] = []
    for index in range(993):
        ticker = f"{index:06d}"
        if index == 660:
            ticker = "000660"
        if index == 992:
            ticker = "005930"
        securities.append(
            {
                "ticker": ticker,
                "current_status": "CURRENT_IDENTITY_VERIFIED",
                "matches": [{"corp_code": f"{index + 1:08d}"}],
            }
        )
    unique: dict[str, dict[str, Any]] = {}
    for row in securities:
        unique[str(row["ticker"])] = row
    while len(unique) < 993:
        index = len(unique) + 1000
        ticker = f"{index:06d}"
        unique[ticker] = {
            "ticker": ticker,
            "current_status": "CURRENT_IDENTITY_VERIFIED",
            "matches": [{"corp_code": f"{index + 1:08d}"}],
        }
    artifact = {
        "version": "dart_corporation_mapping_v1",
        "securities": [unique[ticker] for ticker in sorted(unique)],
    }
    return wrapped(artifact)


def historical_document(mapping_fingerprint: str) -> dict[str, Any]:
    artifact = {
        "version": "baseline_security_lifecycle_v1",
        "current_corporation_mapping_fingerprint": mapping_fingerprint,
        "current_corporations_verified": 993,
        "whole_period_historical_identities_verified": 0,
        "boundary_identities": [],
    }
    return wrapped(artifact)


def test_document_plan_deduplicates_receipts_and_merges_review_scope() -> None:
    plan = build_document_plan(candidate_document()).to_dict()

    assert plan["candidate_count"] == 3
    assert plan["document_count"] == 2
    first = plan["documents"][0]
    assert first["rcept_no"] == "20240701000001"
    assert first["repository_tickers"] == ["005930"]
    assert first["review_categories"] == ["merger", "split"]
    assert first["lifecycle_review"] is True
    assert plan["event_approval_performed"] is False


def test_document_source_replay_matches_transport_cache(tmp_path: Path) -> None:
    plan = build_document_plan(candidate_document())

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zip_body())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = DartEvidenceClient("test-only-secret", tmp_path, http)
        for receipt in ("20240701000001", "20240702000002"):
            client.fetch("document.xml", {"rcept_no": receipt})

    result = inspect_document_sources(plan, tmp_path).to_dict()
    assert result["expected_document_count"] == 2
    assert result["preserved_document_count"] == 2
    assert result["missing_document_count"] == 0
    assert result["unexpected_document_count"] == 0
    assert result["complete"] is True
    for path in tmp_path.glob("*.json"):
        assert "test-only-secret" not in path.read_text()


def test_missing_document_source_remains_incomplete(tmp_path: Path) -> None:
    plan = build_document_plan(candidate_document())

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zip_body())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        DartEvidenceClient("test-only-secret", tmp_path, http).fetch(
            "document.xml",
            {"rcept_no": "20240701000001"},
        )

    result = inspect_document_sources(plan, tmp_path).to_dict()
    assert result["preserved_document_count"] == 1
    assert result["missing_documents"] == ["20240702000002"]
    assert result["complete"] is False


def test_security_review_queue_never_promotes_boundary_identity() -> None:
    mapping = mapping_document()
    historical = historical_document(str(mapping["fingerprint"]))
    queue = build_security_review_queue(
        mapping,
        historical,
        build_document_plan(candidate_document()),
    ).to_dict()

    assert queue["universe_count"] == 993
    assert queue["current_identity_verified_count"] == 993
    assert queue["whole_period_historical_identity_verified_count"] == 0
    assert queue["coverage_records_approved"] == 0
    assert queue["corporate_action_gate_verified"] is False
    samsung = next(row for row in queue["securities"] if row["ticker"] == "005930")
    assert "verify_full_period_historical_security_identity" in samsung["required_actions"]
    assert "review_candidate_documents_and_effective_dates" in samsung["required_actions"]


def test_acquisition_manifest_links_parents_but_keeps_gate_closed(
    tmp_path: Path,
) -> None:
    corporate_actions = candidate_document()
    plan = build_document_plan(corporate_actions)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zip_body())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = DartEvidenceClient("test-only-secret", tmp_path, http)
        for receipt in ("20240701000001", "20240702000002"):
            client.fetch("document.xml", {"rcept_no": receipt})
    sources = inspect_document_sources(plan, tmp_path)

    mapping = mapping_document()
    historical = historical_document(str(mapping["fingerprint"]))
    queue = build_security_review_queue(mapping, historical, plan)

    dart_artifact = {
        "version": "baseline_dart_evidence_v1",
        "artifacts": {"dart-corporation-mapping": mapping["fingerprint"]},
    }
    dart_manifest = wrapped(dart_artifact)
    lifecycle_artifact = {
        "version": "baseline_security_lifecycle_v1",
        "parent_mapping_fingerprint": mapping["fingerprint"],
    }
    lifecycle_manifest = wrapped(lifecycle_artifact)
    integrated_artifact = {
        "version": "baseline_security_lifecycle_v1",
        "manifest_fingerprint": lifecycle_manifest["fingerprint"],
        "corporate_action": "BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
    }
    integrated = wrapped(integrated_artifact)

    manifest = build_acquisition_manifest(
        dart_manifest,
        lifecycle_manifest,
        integrated,
        plan,
        sources,
        queue,
    ).to_dict()
    assert manifest["ready_for_manual_document_review"] is True
    assert manifest["coverage_records_approved"] == 0
    assert manifest["corporate_action_gate_verified"] is False


def test_document_receipt_body_hash_is_checked(tmp_path: Path) -> None:
    plan = build_document_plan(candidate_document())
    meta = {
        "publisher": "Financial Supervisory Service / Open DART",
        "endpoint": "https://opendart.fss.or.kr/api/document.xml",
        "params": {"rcept_no": "20240701000001"},
        "credential_used": True,
        "http_status": 200,
        "raw_sha256": "0" * 64,
    }
    identity = {key: meta[key] for key in ("publisher", "endpoint", "params", "credential_used")}
    stem = digest(identity)
    (tmp_path / f"{stem}.json").write_text(json.dumps(meta))
    (tmp_path / f"{stem}.gz").write_bytes(gzip.compress(zip_body(), mtime=0))

    try:
        inspect_document_sources(plan, tmp_path)
    except ValueError as error:
        assert str(error) == "document_body_hash_mismatch"
    else:
        raise AssertionError("tampered document source must fail")
