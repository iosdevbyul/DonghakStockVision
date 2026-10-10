"""Explicit document decisions only; never final security coverage approval."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any, cast

from donghak_stock_vision.backtest.corporate_action_acquisition import artifact
from donghak_stock_vision.backtest.corporate_action_coverage import CorporateActionEventEvidence
from donghak_stock_vision.backtest.dart_evidence import EVENT_CLASSES, security_code_valid
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import ContractError, fields, require, sha, text
from donghak_stock_vision.data.learning import canonical, digest

INDEX_VERSION = "corporate_action_document_review_index_v1"
DECISIONS_VERSION = "corporate_action_semantic_review_decisions_v1"
REVIEW_VERSION = "corporate_action_semantic_review_v1"
DOCUMENT_ENDPOINT = "https://opendart.fss.or.kr/api/document.xml"


class ReviewDisposition(StrEnum):
    """A reviewer conclusion about one document/security pair, not a period."""

    CONFIRMED_EVENT = "CONFIRMED_EVENT"
    NO_RELEVANT_EVENT_IN_DOCUMENT = "NO_RELEVANT_EVENT_IN_DOCUMENT"
    UNRESOLVED = "UNRESOLVED"


def _array(value: Any) -> list[Any]:
    require(type(value) is list, "review_array_required")
    return cast(list[Any], value)


def _receipt(value: Any) -> str:
    result = text(value)
    require(re.fullmatch(r"[0-9]{14}", result) is not None, "invalid_receipt_number")
    return result


def _effective_date(value: Any) -> date:
    result = text(value)
    require(
        re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", result) is not None, "invalid_effective_date"
    )
    try:
        return date.fromisoformat(result)
    except ValueError:
        raise ContractError("invalid_effective_date") from None


def _documents(wrapped: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index = artifact(wrapped, version=INDEX_VERSION)
    documents: dict[str, dict[str, Any]] = {}
    for value in _array(index.get("documents")):
        require(type(value) is dict, "indexed_document_required")
        receipt = _receipt(value.get("rcept_no"))
        require(receipt not in documents, "duplicate_indexed_document")
        sha(value.get("raw_sha256"))
        status = value.get("parse_status")
        require(
            status in ("REVIEW_INDEXED", "NO_TEXT_ENTRY", "DOCUMENT_ARCHIVE_UNREADABLE"),
            "unsupported_document_parse_status",
        )
        # The existing unreadable-document index has no security links.
        tickers = _array(value.get("repository_tickers", []))
        require(all(security_code_valid(ticker) for ticker in tickers), "invalid_indexed_ticker")
        require(len(tickers) == len(set(tickers)), "duplicate_indexed_ticker")
        entries: dict[str, str] = {}
        for entry in _array(value.get("entries")):
            require(type(entry) is dict, "indexed_entry_required")
            name = text(entry.get("name"))
            require(name not in entries, "duplicate_indexed_entry")
            entries[name] = sha(entry.get("sha256"))
        require(bool(entries) == (status == "REVIEW_INDEXED"), "inconsistent_indexed_entries")
        documents[receipt] = {
            "raw_sha256": value["raw_sha256"],
            "repository_tickers": tuple(tickers),
            "parse_status": status,
            "entries": entries,
        }
    require(
        type(index.get("document_count")) is int and index["document_count"] == len(documents),
        "index_document_count_mismatch",
    )
    return documents


def _validated_review(
    wrapped_index: dict[str, Any], decisions: FrozenJSON
) -> tuple[
    dict[str, dict[str, Any]], list[dict[str, Any]], tuple[CorporateActionEventEvidence, ...]
]:
    documents = _documents(wrapped_index)
    value = fields(decisions.to_dict(), "version document_review_index_fingerprint records")
    require(value["version"] == DECISIONS_VERSION, "unsupported_decisions_version")
    require(
        sha(value["document_review_index_fingerprint"]) == wrapped_index["fingerprint"],
        "decision_index_fingerprint_mismatch",
    )
    pairs: set[tuple[str, str]] = set()
    semantic_events: set[tuple[str, str, str, str]] = set()
    records: list[dict[str, Any]] = []
    approved: list[CorporateActionEventEvidence] = []
    for raw_record in _array(value["records"]):
        record = fields(
            raw_record, "rcept_no ticker raw_sha256 disposition review_reference events"
        )
        receipt = _receipt(record["rcept_no"])
        require(receipt in documents, "unknown_review_document")
        document = documents[receipt]
        require(sha(record["raw_sha256"]) == document["raw_sha256"], "document_hash_mismatch")
        ticker = text(record["ticker"])
        require(security_code_valid(ticker), "exact_security_code_required")
        require(ticker in document["repository_tickers"], "ticker_not_linked_to_document")
        pair = (receipt, ticker)
        require(pair not in pairs, "duplicate_review_decision")
        pairs.add(pair)
        reference = text(record["review_reference"])
        require(record["disposition"] in tuple(ReviewDisposition), "unsupported_review_disposition")
        disposition = ReviewDisposition(record["disposition"])
        raw_events = _array(record["events"])
        require(
            bool(raw_events) == (disposition == ReviewDisposition.CONFIRMED_EVENT),
            "events_disposition_mismatch",
        )
        if disposition != ReviewDisposition.UNRESOLVED:
            require(document["parse_status"] == "REVIEW_INDEXED", "document_not_reviewable")
        events: list[dict[str, Any]] = []
        for raw_event in raw_events:
            event = fields(
                raw_event, "event_class effective_date evidence_entry_name evidence_entry_sha256"
            )
            event_class = text(event["event_class"])
            require(event_class in EVENT_CLASSES, "unsupported_corporate_action_class")
            effective_date = _effective_date(event["effective_date"])
            name = text(event["evidence_entry_name"])
            require(name in document["entries"], "unknown_evidence_entry")
            entry_sha = sha(event["evidence_entry_sha256"])
            require(entry_sha == document["entries"][name], "evidence_entry_hash_mismatch")
            # A second attachment cannot turn the same document event into a new event.
            semantic_key = (receipt, ticker, event_class, effective_date.isoformat())
            require(semantic_key not in semantic_events, "duplicate_semantic_event")
            semantic_events.add(semantic_key)
            evidence = CorporateActionEventEvidence(
                ticker=ticker,
                event_class=event_class,
                effective_date=effective_date,
                source=FrozenJSON.freeze(
                    {
                        "url": DOCUMENT_ENDPOINT,
                        "raw_sha256": document["raw_sha256"],
                        "document_reference": receipt,
                        "evidence_entry_name": name,
                        "evidence_entry_sha256": entry_sha,
                    }
                ),
                review_reference=reference,
                document_reference=receipt,
            )
            approved.append(evidence)
            events.append(event)
        records.append({**record, "events": sorted(events, key=canonical)})
    return (
        documents,
        sorted(records, key=lambda row: (row["rcept_no"], row["ticker"])),
        tuple(
            sorted(
                approved,
                key=lambda event: (
                    event.ticker,
                    event.effective_date,
                    event.event_class,
                    event.document_reference,
                    event.fingerprint,
                ),
            )
        ),
    )


def approved_event_evidence(
    wrapped_index: dict[str, Any], decisions: FrozenJSON
) -> tuple[CorporateActionEventEvidence, ...]:
    """Validate explicit decisions and convert only confirmed document events."""
    return _validated_review(wrapped_index, decisions)[2]


def build_semantic_review(wrapped_index: dict[str, Any], decisions: FrozenJSON) -> FrozenJSON:
    """Freeze partial semantic review without asserting identity or final coverage."""
    documents, records, events = _validated_review(wrapped_index, decisions)
    reviewed = {record["rcept_no"] for record in records}
    resolved_pairs = {
        (record["rcept_no"], record["ticker"])
        for record in records
        if record["disposition"] != ReviewDisposition.UNRESOLVED
    }
    expected_pairs = {
        (receipt, ticker)
        for receipt, document in documents.items()
        for ticker in document["repository_tickers"]
    }
    counts = {
        disposition: sum(record["disposition"] == disposition for record in records)
        for disposition in ReviewDisposition
    }
    return FrozenJSON.freeze(
        {
            "version": REVIEW_VERSION,
            "document_review_index_fingerprint": wrapped_index["fingerprint"],
            "decision_record_count": len(records),
            "reviewed_document_count": len(reviewed),
            "confirmed_event_record_count": counts[ReviewDisposition.CONFIRMED_EVENT],
            "approved_event_count": len(events),
            "no_relevant_event_document_count": len(
                {
                    record["rcept_no"]
                    for record in records
                    if record["disposition"] == ReviewDisposition.NO_RELEVANT_EVENT_IN_DOCUMENT
                }
            ),
            "unresolved_record_count": counts[ReviewDisposition.UNRESOLVED],
            "unreviewed_document_count": len(documents.keys() - reviewed),
            "pending_document_security_pair_count": len(expected_pairs - resolved_pairs),
            "approved_events": [event.to_dict() for event in events],
            "records": records,
            "decisions_fingerprint": digest(
                {
                    "version": DECISIONS_VERSION,
                    "document_review_index_fingerprint": wrapped_index["fingerprint"],
                    "records": records,
                }
            ),
            "authoritative_no_event_semantics_verified": False,
            "coverage_records_approved": 0,
            "corporate_action_gate_verified": False,
            "historical_identity_verified": False,
            "prices_adjusted": False,
            "prediction_or_backtest_invoked": False,
        }
    )
