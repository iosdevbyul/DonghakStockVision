"""Offline timestamp inventory; local consistency never grants historical availability."""

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_availability import (
    HistoricalAvailabilityClaim,
    PinnedAvailabilityClaim,
)
from donghak_stock_vision.backtest.historical_readiness import (
    CalendarMonthSource,
    HistoricalReadScope,
)
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.capture_custody_ack import (
    CaptureCustodyAcknowledgement,
    inspect_custody_acknowledgement,
)
from donghak_stock_vision.storage.capture_evidence_sink import _receipt_bytes
from donghak_stock_vision.storage.historical_readiness import _file_hash, inspect_historical_market
from donghak_stock_vision.trading.paper_types import validate_owner, wire


def availability_manifest(owners: tuple[PinnedAvailabilityClaim, ...]) -> FrozenJSON:
    """The caller must retain/trust this pin independently of any assessed report."""
    require(type(owners) is tuple and len(owners) <= 256, "availability_evidence_bound")
    entries = []
    total = 0
    for owner in owners:
        require(
            type(owner) is PinnedAvailabilityClaim
            and type(owner.claim) is HistoricalAvailabilityClaim,
            "availability_owner_required",
        )
        validate_owner(owner.claim)
        doc = owner.claim.document
        total += len(doc.payload_json.encode())
        require(doc.identifier == sha(owner.trusted_document_hash), "availability_document_pin")
        require(
            (owner.claim.publisher_id, owner.claim.custodian_id)
            == (owner.expected_publisher, owner.expected_custodian),
            "evidence_issuer_substitution",
        )
        supplied = (
            owner.acknowledgement,
            owner.exported_receipt,
            owner.trusted_acknowledgement_hash,
            owner.trusted_receipt_hash,
        )
        require(
            all(v is None for v in supplied) or all(v is not None for v in supplied),
            "incomplete_original_custody_receipt",
        )
        for body, expected in (
            (owner.acknowledgement, owner.trusted_acknowledgement_hash),
            (owner.exported_receipt, owner.trusted_receipt_hash),
        ):
            if body is not None:
                require(type(body) is bytes and len(body) <= 65536, "custody_byte_budget")
                require(
                    expected is not None and hashlib.sha256(body).hexdigest() == sha(expected),
                    "custody_independent_pin_mismatch",
                )
                total += len(body)
        entries.append(
            {
                "document_hash": owner.trusted_document_hash,
                "expected_publisher": owner.expected_publisher,
                "expected_custodian": owner.expected_custodian,
                "ack_hash": owner.trusted_acknowledgement_hash,
                "receipt_hash": owner.trusted_receipt_hash,
            }
        )
    require(total <= 512 * 1024, "availability_total_byte_budget")
    require(
        len({e["document_hash"] for e in entries}) == len(entries),
        "duplicate_availability_evidence",
    )
    return FrozenJSON.freeze(
        {"version": "historical_availability_input_manifest_v1", "evidence": entries}
    )


@dataclass(frozen=True)
class HistoricalPITAssessment:
    report: FrozenJSON
    evidence: tuple[PinnedAvailabilityClaim, ...]


def assess_historical_pit(
    database: Path,
    scope: HistoricalReadScope,
    trusted_scope_hash: str,
    owners: tuple[PinnedAvailabilityClaim, ...],
    trusted_evidence_manifest_hash: str,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> HistoricalPITAssessment:
    require(type(scope) is HistoricalReadScope, "historical_scope_owner_required")
    scope.__post_init__()  # Reject final-test/holdout access before source or receipt I/O.
    require(scope.document.identifier == sha(trusted_scope_hash), "scope_pin_mismatch")
    manifest = availability_manifest(owners)
    require(
        manifest.identifier == sha(trusted_evidence_manifest_hash),
        "independent_evidence_manifest_pin",
    )
    original = inspect_historical_market(database, scope, trusted_scope_hash, calendars)
    readiness = original.report.to_dict()
    records = original.observations.to_dict()["records"]
    raw = {r["raw_id"]: r for r in readiness["raw_sources"]}
    mapped: dict[str, list[PinnedAvailabilityClaim]] = {r["bar_hash"]: [] for r in records}
    timestamp_origins: dict[tuple[str | None, str, str], datetime] = {}
    for owner in owners:
        claim = owner.claim
        for timestamp in claim.timestamps:
            key = claim.publisher_id, claim.document_identity, timestamp.kind
            require(
                key not in timestamp_origins or timestamp_origins[key] == timestamp.at,
                "conflicting_availability_timestamp_claims",
            )
            timestamp_origins[key] = timestamp.at
        require(
            wire(claim.instrument) == wire(scope.instrument), "availability_instrument_mismatch"
        )
        record = next(
            (
                r
                for r in records
                if r["bar_hash"] == claim.bar_hash
                and r["trading_date"] == claim.trading_date.isoformat()
            ),
            None,
        )
        require(record is not None, "availability_observation_mapping_mismatch")
        assert record is not None
        require(
            claim.raw_id in record["raw_ids"] and raw[claim.raw_id]["sha256"] == claim.raw_hash,
            "availability_original_source_mismatch",
        )
        mapped[claim.bar_hash].append(owner)
        require(len(mapped[claim.bar_hash]) <= 4, "per_observation_claim_bound")
    path = database.resolve(strict=True)
    require(
        not Path(str(path) + "-wal").exists() and not Path(str(path) + "-journal").exists(),
        "pit_source_journal_changed",
    )
    rows = []
    used_receipts: dict[int, bytes | None] = {}
    # The original reader verified all raw identity/scope/date boundaries before this step.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        total_scoped = connection.execute(
            "SELECT COUNT(*) FROM bars WHERE ticker=? AND trading_date BETWEEN ? AND ?",
            (scope.instrument.symbol, scope.window.start.isoformat(), scope.window.end.isoformat()),
        ).fetchone()[0]
        has_receipts = readiness["inventory"]["raw_capture_receipt_table_present"]
        for record in records:
            local_receipts = []
            sources = []
            for raw_id in record["raw_ids"]:
                meta = raw[raw_id]
                sources.append(
                    {
                        "raw_id": raw_id,
                        "source_hash": meta["sha256"],
                        "local_retrieval_at": meta["ingested_at"],
                        "origin": f"raw_pages/{raw_id}.collected_at",
                    }
                )
                if raw_id not in used_receipts:
                    exists = (
                        has_receipts
                        and connection.execute(
                            "SELECT 1 FROM raw_capture_receipts WHERE raw_id=?", (raw_id,)
                        ).fetchone()
                        is not None
                    )
                    context = connection.execute(
                        "SELECT provider,ticker,source_date,sha256,collected_at "
                        "FROM raw_pages WHERE id=?",
                        (raw_id,),
                    ).fetchone()
                    require(
                        context
                        == (
                            meta["provider"],
                            meta["scope"],
                            record["trading_date"],
                            meta["sha256"],
                            meta["ingested_at"],
                        ),
                        "capture_source_context_changed",
                    )
                    # No retrospective receipt is created for legacy archive rows.
                    used_receipts[raw_id] = _receipt_bytes(connection, raw_id) if exists else None
                receipt = used_receipts[raw_id]
                if receipt is not None:
                    local_receipts.append(
                        {
                            "raw_id": raw_id,
                            "sha256": hashlib.sha256(receipt).hexdigest(),
                            "assurance": "local_capture_receipt_not_authenticated_time",
                        }
                    )
            claims = []
            for owner in mapped[record["bar_hash"]]:
                claim = owner.claim
                custody = None
                if owner.acknowledgement is not None:
                    require(
                        owner.exported_receipt == used_receipts[claim.raw_id],
                        "original_capture_receipt_missing_or_mismatched",
                    )
                    require(
                        owner.expected_custodian is not None
                        and owner.trusted_acknowledgement_hash is not None,
                        "custodian_identity_required",
                    )
                    assert owner.exported_receipt is not None
                    assert owner.expected_custodian is not None
                    assert owner.trusted_acknowledgement_hash is not None
                    review = inspect_custody_acknowledgement(
                        owner.acknowledgement,
                        owner.exported_receipt,
                        expected_raw_id=claim.raw_id,
                        expected_custodian_id=owner.expected_custodian,
                        independently_pinned_ack_sha256=owner.trusted_acknowledgement_hash,
                    )
                    require(
                        review.content_consistent, "original_custody_acknowledgement_inconsistent"
                    )
                    ack = CaptureCustodyAcknowledgement.model_validate_json(owner.acknowledgement)
                    custody = {
                        "receipt_id": ack.receiptId,
                        "claimed_accepted_at": ack.acceptedAt.isoformat(),
                        "content_consistent": review.content_consistent,
                        "custodian_authenticated": review.custodian_authenticated,
                        "independent_custody_verified": review.independent_custody_verified,
                        "blockers": list(review.blockers),
                    }
                claims.append(
                    {
                        "claim": claim.document.to_dict(),
                        "claim_pin": owner.trusted_document_hash,
                        "custody_review": custody,
                        "classification": "content_bound_claim_not_authenticated_historical_time",
                    }
                )
            reasons = [
                "authentic_publication_evidence_missing",
                "authenticated_revision_history_missing",
                "independent_timestamp_verifier_unavailable",
                "decision_time_availability_unestablished",
            ]
            if not local_receipts:
                reasons.append("original_capture_receipt_missing")
            if not claims:
                reasons.append("independent_availability_evidence_missing")
            if not readiness["calendar_pins"] or readiness["invalid_reasons"]:
                reasons.append("original_source_or_calendar_not_verified")
            rows.append(
                {
                    "instrument": record["instrument"],
                    "session_date": record["trading_date"],
                    "bar_hash": record["bar_hash"],
                    "source_lineage_verified": bool(record["raw_ids"]),
                    "timestamps": {
                        "exchange_session_at": None,
                        "source_publication_at": None,
                        "source_correction_at": None,
                        "actual_first_observed_at": None,
                        "provider_ingestion_at": None,
                        "original_artifact_capture_at": None,
                        "independently_authenticated_at": None,
                        "evidence_available_at": None,
                        "strategy_safe_available_at": None,
                    },
                    "locally_retained_retrievals": sources,
                    "local_capture_receipts": local_receipts,
                    "unverified_evidence": claims,
                    "pit_qualified": False,
                    "missing_requirements": sorted(reasons),
                }
            )
    require(_file_hash(path) == scope.trusted_database_hash, "pit_source_changed_during_assessment")
    require(
        not Path(str(path) + "-wal").exists() and not Path(str(path) + "-journal").exists(),
        "pit_source_journal_changed",
    )
    value = {
        "version": "historical_pit_availability_assessment_v1",
        "scope_hash": trusted_scope_hash,
        "database_hash": scope.trusted_database_hash,
        "readiness_report_hash": readiness["content_fingerprint"],
        "evidence_manifest_hash": trusted_evidence_manifest_hash,
        "calendar_pins": readiness["calendar_pins"],
        "original_approval_state": {
            k: readiness[k]
            for k in (
                "eligible_for_historical_replay",
                "strict_pit_approved",
                "oos_approved",
                "operational_eligible",
            )
        },
        "original_readiness_blockers": readiness["blockers"]
        + readiness["invalid_reasons"]
        + readiness["missing_provenance"],
        "coverage": {
            "total_scoped_observations": total_scoped,
            "unprojectable_scoped_observations": total_scoped - len(records),
            "verified_source_lineage": sum(r["source_lineage_verified"] for r in rows),
            "authentic_publication_evidence": 0,
            "authenticated_revision_evidence": 0,
            "decision_time_availability_established": 0,
            "pit_unqualified": total_scoped,
        },
        "observations": rows,
        "missing_requirements": sorted(
            {reason for r in rows for reason in r["missing_requirements"]}
        ),
        "unprojectable_reason": "original_invalid_observation_cannot_bind_evidence"
        if total_scoped != len(records)
        else None,
        "historical_strategy_execution": "blocked",
        "pit_approved": False,
        "operational_eligible": False,
        "source_url_reference": readiness["source"]["endpoint_contract"],
        "source_url_origin": "provider_endpoint_contract_not_independently_retained_request_url",
        "issuer_authenticated": False,
        "assurance": "local_lineage_and_claim_consistency_only",
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    require(len(report.payload_json.encode()) <= 2 * 1024 * 1024, "pit_report_byte_budget")
    return HistoricalPITAssessment(report, owners)


def verify_historical_pit(
    result: HistoricalPITAssessment,
    trusted_report_hash: str,
    database: Path,
    scope: HistoricalReadScope,
    trusted_scope_hash: str,
    owners: tuple[PinnedAvailabilityClaim, ...],
    trusted_evidence_manifest_hash: str,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> None:
    require(type(result) is HistoricalPITAssessment, "pit_assessment_owner_required")
    require(
        type(result.report) is FrozenJSON
        and len(result.report.payload_json.encode()) <= 2 * 1024 * 1024,
        "pit_report_byte_budget",
    )
    require(
        result.report.to_dict().get("content_fingerprint") == sha(trusted_report_hash),
        "pit_report_pin",
    )
    fresh = assess_historical_pit(
        database, scope, trusted_scope_hash, owners, trusted_evidence_manifest_hash, calendars
    )
    require(result == fresh, "pit_assessment_semantic_replay_mismatch")


def historical_pit_execution_gate(
    result: HistoricalPITAssessment,
    trusted_report_hash: str,
    database: Path,
    scope: HistoricalReadScope,
    trusted_scope_hash: str,
    owners: tuple[PinnedAvailabilityClaim, ...],
    trusted_evidence_manifest_hash: str,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> FrozenJSON:
    verify_historical_pit(
        result,
        trusted_report_hash,
        database,
        scope,
        trusted_scope_hash,
        owners,
        trusted_evidence_manifest_hash,
        calendars,
    )
    value = result.report.to_dict()
    return FrozenJSON.freeze(
        {
            "version": "historical_pit_execution_gate_v1",
            "status": "blocked",
            "assessment_hash": trusted_report_hash,
            "reasons": sorted(
                set(value["missing_requirements"] + value["original_readiness_blockers"])
            ),
            "orders": 0,
            "fills": 0,
            "strategy_execution_started": False,
            "approval_granted": False,
        }
    )
