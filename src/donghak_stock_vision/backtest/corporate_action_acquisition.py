"""Deterministic corporate-action acquisition planning and offline source review."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from donghak_stock_vision.backtest.dart_evidence import EVENT_CLASSES, security_code_valid
from donghak_stock_vision.backtest.dart_transport import (
    DartTransferLimits,
    read_cached_body,
    request_identity,
    zip_response_valid,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest


def artifact(
    wrapped: dict[str, Any],
    *,
    version: str,
) -> dict[str, Any]:
    """Validate one checked-in wrapped artifact before deriving new evidence."""

    require(type(wrapped) is dict, "wrapped_artifact_required")
    value_object = wrapped.get("artifact")
    require(type(value_object) is dict, "artifact_object_required")
    value = cast(dict[str, Any], value_object)
    require(wrapped.get("fingerprint") == digest(value), "artifact_fingerprint_mismatch")
    require(value.get("version") == version, "artifact_version_mismatch")
    return dict(value)


def build_document_plan(corporate_actions: dict[str, Any]) -> FrozenJSON:
    """Create a stable list of candidate DART documents without approving events."""

    parent = artifact(corporate_actions, version="dart_document_review_queue_v1")
    candidates_object = parent.get("candidates")
    require(type(candidates_object) is list, "corporate_action_candidates_required")
    candidates = cast(list[Any], candidates_object)

    documents: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        require(type(candidate) is dict, "candidate_object_required")
        filing = candidate.get("filing")
        require(type(filing) is dict, "candidate_filing_required")
        receipt = filing.get("rcept_no")
        require(
            isinstance(receipt, str) and re.fullmatch(r"[0-9]{14}", receipt) is not None,
            "invalid_candidate_receipt",
        )

        categories = candidate.get("review_categories")
        require(type(categories) is list, "candidate_review_categories_required")
        require(
            all(category in EVENT_CLASSES for category in categories),
            "unsupported_candidate_review_category",
        )
        ticker = candidate.get("repository_ticker")
        require(
            ticker is None or security_code_valid(ticker),
            "invalid_candidate_repository_ticker",
        )
        require(
            type(candidate.get("lifecycle_review")) is bool,
            "candidate_lifecycle_flag_required",
        )

        current = documents.setdefault(
            receipt,
            {
                "rcept_no": receipt,
                "repository_tickers": set(),
                "review_categories": set(),
                "lifecycle_review": False,
            },
        )
        if ticker is not None:
            current["repository_tickers"].add(ticker)
        current["review_categories"].update(categories)
        current["lifecycle_review"] = current["lifecycle_review"] or candidate["lifecycle_review"]

    rows = []
    for receipt in sorted(documents):
        current = documents[receipt]
        rows.append(
            {
                "rcept_no": receipt,
                "repository_tickers": sorted(current["repository_tickers"]),
                "review_categories": sorted(current["review_categories"]),
                "lifecycle_review": current["lifecycle_review"],
                "status": "DOCUMENT_SOURCE_REQUIRED",
            }
        )

    return FrozenJSON.freeze(
        {
            "version": "corporate_action_document_plan_v1",
            "parent_corporate_action_fingerprint": corporate_actions["fingerprint"],
            "candidate_count": len(candidates),
            "document_count": len(rows),
            "documents": rows,
            "event_approval_performed": False,
        }
    )


def inspect_document_sources(
    plan: FrozenJSON, source_dir: Path, *, limits: DartTransferLimits | None = None
) -> FrozenJSON:
    """Verify cached DART document bytes and receipts without parsing event meaning."""

    value = plan.to_dict()
    require(
        value.get("version") == "corporate_action_document_plan_v1",
        "document_plan_version_mismatch",
    )
    require(source_dir.is_dir(), "document_source_directory_required")
    expected = {row["rcept_no"] for row in value["documents"]}
    preserved: dict[str, str] = {}

    for receipt_path in sorted(source_dir.glob("*.json")):
        meta = json.loads(receipt_path.read_text())
        require(type(meta) is dict, "document_receipt_object_required")
        params = meta.get("params")
        require(type(params) is dict, "document_receipt_params_required")
        identity = request_identity("document.xml", params)
        require(digest(identity) == receipt_path.stem, "document_request_identity_mismatch")
        require(meta.get("endpoint") == identity["endpoint"], "document_endpoint_mismatch")
        require(meta.get("http_status") == 200, "document_http_failure")

        rcept_no = params.get("rcept_no")
        require(
            isinstance(rcept_no, str) and re.fullmatch(r"[0-9]{14}", rcept_no) is not None,
            "invalid_document_receipt_number",
        )
        body_path = receipt_path.with_suffix(".gz")
        require(body_path.is_file(), "document_body_missing")
        raw = read_cached_body(body_path, limits=limits)
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        require(raw_sha256 == meta.get("raw_sha256"), "document_body_hash_mismatch")
        require(zip_response_valid("document.xml", raw, limits=limits), "document_archive_required")
        require(rcept_no not in preserved, "duplicate_document_source")
        preserved[rcept_no] = raw_sha256

    missing = sorted(expected - set(preserved))
    unexpected = sorted(set(preserved) - expected)
    return FrozenJSON.freeze(
        {
            "version": "corporate_action_document_sources_v1",
            "plan_fingerprint": plan.identifier,
            "expected_document_count": len(expected),
            "preserved_document_count": len(expected & set(preserved)),
            "missing_document_count": len(missing),
            "unexpected_document_count": len(unexpected),
            "missing_documents": missing,
            "unexpected_documents": unexpected,
            "source_sha256": {
                receipt: preserved[receipt] for receipt in sorted(expected & set(preserved))
            },
            "complete": not missing and not unexpected,
            "documents_parsed_for_event_meaning": False,
        }
    )


def build_security_review_queue(
    mapping: dict[str, Any],
    historical_identities: dict[str, Any],
    plan: FrozenJSON,
) -> FrozenJSON:
    """Build one fail-closed review row for every locked-universe security."""

    mapping_value = artifact(mapping, version="dart_corporation_mapping_v1")
    historical = artifact(historical_identities, version="baseline_security_lifecycle_v1")
    require(
        historical.get("current_corporation_mapping_fingerprint") == mapping["fingerprint"],
        "historical_identity_parent_mapping_mismatch",
    )

    securities_object = mapping_value.get("securities")
    require(type(securities_object) is list, "mapping_securities_required")
    securities = cast(list[Any], securities_object)
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in securities:
        require(type(row) is dict, "mapping_security_object_required")
        ticker = row.get("ticker")
        require(
            security_code_valid(ticker),
            "invalid_mapping_ticker",
        )
        require(ticker not in by_ticker, "duplicate_mapping_ticker")
        by_ticker[ticker] = row
    require(len(by_ticker) == 993, "locked_universe_count_mismatch")

    boundary_rows_object = historical.get("boundary_identities")
    require(type(boundary_rows_object) is list, "boundary_identity_rows_required")
    boundary_rows = cast(list[Any], boundary_rows_object)
    boundaries: dict[str, list[dict[str, Any]]] = {}
    for row in boundary_rows:
        require(type(row) is dict, "boundary_identity_object_required")
        ticker = row.get("ticker")
        require(ticker in by_ticker, "boundary_identity_outside_universe")
        boundaries.setdefault(ticker, []).append(row)

    plan_value = plan.to_dict()
    documents: dict[str, list[str]] = {}
    for row in plan_value["documents"]:
        for ticker in row["repository_tickers"]:
            require(ticker in by_ticker, "document_ticker_outside_universe")
            documents.setdefault(ticker, []).append(row["rcept_no"])

    rows = []
    current_verified = 0
    whole_period_verified = 0
    for ticker in sorted(by_ticker):
        mapping_row = by_ticker[ticker]
        matches_object = mapping_row.get("matches")
        require(type(matches_object) is list, "mapping_matches_required")
        matches = cast(list[dict[str, Any]], matches_object)
        current_status = mapping_row.get("current_status")
        corp_code = None
        if current_status == "CURRENT_IDENTITY_VERIFIED":
            require(len(matches) == 1, "verified_current_mapping_shape")
            corp_code = matches[0].get("corp_code")
            require(
                isinstance(corp_code, str) and re.fullmatch(r"[0-9]{8}", corp_code) is not None,
                "invalid_current_corp_code",
            )
            current_verified += 1

        boundary = boundaries.get(ticker, [])
        whole_period = any(row.get("whole_period_continuity_verified") is True for row in boundary)
        if whole_period:
            whole_period_verified += 1

        required_actions = []
        if current_status != "CURRENT_IDENTITY_VERIFIED":
            required_actions.append("resolve_exact_security_to_current_issuer")
        if not whole_period:
            required_actions.append("verify_full_period_historical_security_identity")
        required_actions.append("verify_complete_event_class_query_semantics")
        if documents.get(ticker):
            required_actions.append("review_candidate_documents_and_effective_dates")
        else:
            required_actions.append("certify_no_relevant_event_semantics")

        rows.append(
            {
                "ticker": ticker,
                "current_identity_status": current_status,
                "corp_code": corp_code,
                "boundary_identity_count": len(boundary),
                "whole_period_historical_identity_verified": whole_period,
                "candidate_document_receipts": sorted(documents.get(ticker, [])),
                "required_event_classes": sorted(EVENT_CLASSES),
                "required_actions": required_actions,
                "status": "PENDING_REVIEW",
            }
        )

    require(
        current_verified == historical.get("current_corporations_verified"),
        "current_identity_count_mismatch",
    )
    require(
        whole_period_verified == historical.get("whole_period_historical_identities_verified"),
        "historical_identity_count_mismatch",
    )
    return FrozenJSON.freeze(
        {
            "version": "corporate_action_security_review_queue_v1",
            "mapping_fingerprint": mapping["fingerprint"],
            "historical_identity_fingerprint": historical_identities["fingerprint"],
            "document_plan_fingerprint": plan.identifier,
            "universe_count": len(rows),
            "current_identity_verified_count": current_verified,
            "whole_period_historical_identity_verified_count": whole_period_verified,
            "candidate_linked_security_count": len(documents),
            "securities": rows,
            "coverage_records_approved": 0,
            "corporate_action_gate_verified": False,
        }
    )


def build_acquisition_manifest(
    dart_manifest: dict[str, Any],
    lifecycle_manifest: dict[str, Any],
    integrated_lifecycle: dict[str, Any],
    plan: FrozenJSON,
    sources: FrozenJSON,
    queue: FrozenJSON,
) -> FrozenJSON:
    """Link acquisition outputs to the current evidence lineage without clearing gates."""

    dart = artifact(dart_manifest, version="baseline_dart_evidence_v1")
    lifecycle = artifact(
        lifecycle_manifest,
        version="baseline_security_lifecycle_v1",
    )
    integrated = artifact(
        integrated_lifecycle,
        version="baseline_security_lifecycle_v1",
    )
    require(
        integrated.get("manifest_fingerprint") == lifecycle_manifest["fingerprint"],
        "integrated_lifecycle_manifest_mismatch",
    )
    require(
        integrated.get("corporate_action") == "BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
        "unexpected_corporate_action_parent_state",
    )
    require(
        lifecycle.get("parent_mapping_fingerprint")
        == dart["artifacts"]["dart-corporation-mapping"],
        "lifecycle_dart_mapping_mismatch",
    )

    source_value = sources.to_dict()
    queue_value = queue.to_dict()
    return FrozenJSON.freeze(
        {
            "version": "corporate_action_acquisition_manifest_v1",
            "parent_dart_manifest_fingerprint": dart_manifest["fingerprint"],
            "parent_lifecycle_manifest_fingerprint": lifecycle_manifest["fingerprint"],
            "parent_integrated_lifecycle_fingerprint": integrated_lifecycle["fingerprint"],
            "document_plan_fingerprint": plan.identifier,
            "document_sources_fingerprint": sources.identifier,
            "security_review_queue_fingerprint": queue.identifier,
            "planned_document_count": source_value["expected_document_count"],
            "preserved_document_count": source_value["preserved_document_count"],
            "missing_document_count": source_value["missing_document_count"],
            "review_security_count": queue_value["universe_count"],
            "ready_for_manual_document_review": source_value["complete"],
            "coverage_records_approved": 0,
            "corporate_action_gate_verified": False,
            "prices_adjusted": False,
            "prediction_or_backtest_invoked": False,
        }
    )
