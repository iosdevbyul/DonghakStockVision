"""Synthetic negative evidence must never become authentic historical availability."""

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_availability import (
    AvailabilityTimestampClaim,
    HistoricalAvailabilityClaim,
    HistoricalRevisionClaim,
    PinnedAvailabilityClaim,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.capture_evidence_sink import _receipt_bytes
from donghak_stock_vision.storage.historical_pit import (
    assess_historical_pit,
    availability_manifest,
    historical_pit_execution_gate,
    verify_historical_pit,
)
from donghak_stock_vision.storage.historical_readiness import inspect_historical_market
from tests.backtest.test_historical_readiness import file_hash, fixture, mutate
from tests.trading.test_risk import KR

T = datetime(2025, 7, 1, 8, tzinfo=UTC)


def owner_for(
    path: Path, scope: Any, calendars: Any, *, custody: bool = False
) -> PinnedAvailabilityClaim:
    original = inspect_historical_market(path, scope, scope.document.identifier, calendars)
    record = original.observations.to_dict()["records"][0]
    raw_id = record["raw_ids"][0]
    raw = next(r for r in original.report.to_dict()["raw_sources"] if r["raw_id"] == raw_id)
    claim = HistoricalAvailabilityClaim(
        KR,
        date.fromisoformat(record["trading_date"]),
        record["bar_hash"],
        raw_id,
        raw["sha256"],
        f"raw_pages/{raw_id}/sha256/{raw['sha256']}",
        "synthetic-issuer-not-authentic",
        "test-vault",
        (AvailabilityTimestampClaim("publication", T, "synthetic-negative-fixture"),),
        (),
        "fixture-local-capture",
        "self-declared-root-not-authenticated",
    )
    if not custody:
        return PinnedAvailabilityClaim(
            claim, claim.document.identifier, claim.publisher_id, claim.custodian_id
        )
    with sqlite3.connect(path) as c:
        receipt = _receipt_bytes(c, raw_id)
    ack = json.dumps(
        {
            "schemaVersion": "capture_custody_ack_v1",
            "custodianId": "test-vault",
            "receiptId": "fake-ack-1",
            "rawId": raw_id,
            "acceptedAt": "2026-01-05T00:00:00+00:00",
            "receiptSha256": hashlib.sha256(receipt).hexdigest(),
        }
    ).encode()
    return PinnedAvailabilityClaim(
        claim,
        claim.document.identifier,
        claim.publisher_id,
        claim.custodian_id,
        ack,
        receipt,
        hashlib.sha256(ack).hexdigest(),
        hashlib.sha256(receipt).hexdigest(),
    )


def assess(
    path: Path, scope: Any, calendars: Any, owners: tuple[PinnedAvailabilityClaim, ...] = ()
) -> Any:
    return assess_historical_pit(
        path,
        scope,
        scope.document.identifier,
        owners,
        availability_manifest(owners).identifier,
        calendars,
    )


def test_missing_evidence_exact_coverage_timestamps_and_read_only(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    before = file_hash(path)
    result = assess(path, scope, calendars)
    value = result.report.to_dict()
    assert value["coverage"] == {
        "total_scoped_observations": 3,
        "unprojectable_scoped_observations": 0,
        "verified_source_lineage": 3,
        "authentic_publication_evidence": 0,
        "authenticated_revision_evidence": 0,
        "decision_time_availability_established": 0,
        "pit_unqualified": 3,
    }
    assert all(all(t is None for t in row["timestamps"].values()) for row in value["observations"])
    assert all(row["locally_retained_retrievals"] for row in value["observations"])
    assert all(row["local_capture_receipts"] for row in value["observations"])
    assert result == assess(path, scope, calendars)
    verify_historical_pit(
        result,
        value["content_fingerprint"],
        path,
        scope,
        scope.document.identifier,
        (),
        availability_manifest(()).identifier,
        calendars,
    )
    assert file_hash(path) == before


@pytest.mark.parametrize("custody", [False, True])
def test_pinned_timestamp_and_receipt_are_not_authenticity(tmp_path: Path, custody: bool) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars, custody=custody)
    result = assess(path, scope, calendars, (owner,))
    row = result.report.to_dict()["observations"][0]
    assert row["timestamps"]["source_publication_at"] is None
    assert row["timestamps"]["strategy_safe_available_at"] is None
    assert not row["pit_qualified"]
    evidence = row["unverified_evidence"][0]
    if custody:
        assert evidence["custody_review"]["content_consistent"]
        assert not evidence["custody_review"]["custodian_authenticated"]
    assert result.report.to_dict()["coverage"]["authentic_publication_evidence"] == 0


@pytest.mark.parametrize(
    "defect",
    ["document_pin", "publisher", "custodian", "ack_pin", "receipt_pin", "missing_receipt"],
)
def test_independent_identity_and_receipt_pins(tmp_path: Path, defect: str) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars, custody=True)
    defects: dict[str, dict[str, Any]] = {
        "document_pin": {"trusted_document_hash": "0" * 64},
        "publisher": {"expected_publisher": "substituted"},
        "custodian": {"expected_custodian": "substituted"},
        "ack_pin": {"trusted_acknowledgement_hash": "0" * 64},
        "receipt_pin": {"trusted_receipt_hash": "0" * 64},
        "missing_receipt": {"exported_receipt": None},
    }
    changes = defects[defect]
    with pytest.raises(ContractError):
        assess(path, scope, calendars, (replace(owner, **changes),))


@pytest.mark.parametrize("change", ["instrument", "bar_hash", "date", "raw_hash"])
def test_wrong_original_observation_binding(tmp_path: Path, change: str) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars)
    bindings: dict[str, dict[str, Any]] = {
        "instrument": {"instrument": replace(KR, symbol="000660")},
        "bar_hash": {"bar_hash": "0" * 64},
        "date": {"trading_date": date(2025, 7, 2)},
        "raw_hash": {
            "raw_hash": "0" * 64,
            "document_identity": f"raw_pages/{owner.claim.raw_id}/sha256/" + "0" * 64,
        },
    }
    kwargs = bindings[change]
    changed = replace(owner.claim, **kwargs)
    forged = replace(owner, claim=changed, trusted_document_hash=changed.document.identifier)
    with pytest.raises(ContractError):
        assess(path, scope, calendars, (forged,))


def test_document_identity_and_contradictory_chronology(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    claim = owner_for(path, scope, calendars).claim
    with pytest.raises(ContractError, match="source_document_identity_mismatch"):
        replace(claim, document_identity="invented-official-publication")
    with pytest.raises(ContractError, match="conflicting_publication_correction"):
        replace(
            claim,
            timestamps=(
                AvailabilityTimestampClaim("publication", T, "negative"),
                AvailabilityTimestampClaim("correction", T - timedelta(seconds=1), "negative"),
            ),
        )
    first = HistoricalRevisionClaim(1, "a" * 64, None, T)
    second = HistoricalRevisionClaim(2, claim.raw_hash, "a" * 64, T - timedelta(seconds=1))
    with pytest.raises(ContractError, match="conflicting_revision_chronology"):
        replace(claim, revisions=(first, second))
    with pytest.raises(ContractError, match="revision_link_mismatch"):
        replace(claim, revisions=(first, replace(second, previous_hash="b" * 64)))


def test_valid_claimed_revision_chain_still_unqualified(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars)
    claim = replace(
        owner.claim, revisions=(HistoricalRevisionClaim(1, owner.claim.raw_hash, None, T),)
    )
    owner = replace(owner, claim=claim, trusted_document_hash=claim.document.identifier)
    value = assess(path, scope, calendars, (owner,)).report.to_dict()
    assert value["coverage"]["authenticated_revision_evidence"] == 0


@pytest.mark.parametrize(
    "field", ["coverage", "observations", "pit_approved", "original_approval_state"]
)
def test_rehashed_assessment_rejected(tmp_path: Path, field: str) -> None:
    path, scope, calendars = fixture(tmp_path)
    result = assess(path, scope, calendars)
    value = result.report.to_dict()
    value[field] = True
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="pit_assessment_semantic_replay_mismatch"):
        historical_pit_execution_gate(
            replace(result, report=FrozenJSON.freeze(value)),
            value["content_fingerprint"],
            path,
            scope,
            scope.document.identifier,
            (),
            availability_manifest(()).identifier,
            calendars,
        )


def test_evidence_manifest_is_not_taken_from_report(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars)
    original_manifest = availability_manifest((owner,)).identifier
    claim = replace(
        owner.claim,
        timestamps=(AvailabilityTimestampClaim("publication", T + timedelta(hours=1), "fake"),),
    )
    forged = replace(owner, claim=claim, trusted_document_hash=claim.document.identifier)
    with pytest.raises(ContractError, match="independent_evidence_manifest_pin"):
        assess_historical_pit(
            path, scope, scope.document.identifier, (forged,), original_manifest, calendars
        )


@pytest.mark.parametrize("role", ["oos", "final_test", "holdout"])
def test_forbidden_scope_before_source_loading(tmp_path: Path, role: str) -> None:
    _, scope, _ = fixture(tmp_path)
    object.__setattr__(scope, "partition", role)
    with pytest.raises(ContractError, match="historical_partition_forbidden"):
        assess(Path("/does/not/exist"), scope, ())


def test_bad_source_and_invalid_rows_stay_in_denominator(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        body = json.loads(c.execute("SELECT payload FROM bars LIMIT 1").fetchone()[0])
    body["available_at"] = "invented"
    scope = mutate(
        path,
        scope,
        "UPDATE bars SET payload=? WHERE trading_date='2025-07-01'",
        (json.dumps(body),),
    )
    result = assess(path, scope, calendars).report.to_dict()
    assert result["coverage"]["total_scoped_observations"] == 3
    assert result["coverage"]["unprojectable_scoped_observations"] == 1
    assert result["coverage"]["verified_source_lineage"] == 2
    assert result["coverage"]["pit_unqualified"] == 3
    assert result["historical_strategy_execution"] == "blocked"


def test_gate_no_network_or_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network/broker forbidden")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr(
        "donghak_stock_vision.trading.paper.DeterministicPaperBroker.submit", forbidden
    )
    path, scope, calendars = fixture(tmp_path)
    result = assess(path, scope, calendars)
    value = historical_pit_execution_gate(
        result,
        result.report.to_dict()["content_fingerprint"],
        path,
        scope,
        scope.document.identifier,
        (),
        availability_manifest(()).identifier,
        calendars,
    ).to_dict()
    assert value["status"] == "blocked" and value["orders"] == value["fills"] == 0


def test_conflicting_independently_pinned_claims_do_not_choose_earliest(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars)
    changed = replace(
        owner.claim,
        timestamps=(
            AvailabilityTimestampClaim(
                "publication", T + timedelta(hours=1), "different-negative-claim"
            ),
        ),
    )
    other = replace(owner, claim=changed, trusted_document_hash=changed.document.identifier)
    with pytest.raises(ContractError, match="conflicting_availability_timestamp_claims"):
        assess(path, scope, calendars, (owner, other))


def test_original_receipt_must_exist_in_original_archive(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars, custody=True)
    scope = mutate(path, scope, "DROP TABLE raw_capture_receipts")
    value = assess(path, scope, calendars).report.to_dict()
    assert all(not row["local_capture_receipts"] for row in value["observations"])
    with pytest.raises(ContractError, match="original_capture_receipt_missing_or_mismatched"):
        assess(path, scope, calendars, (owner,))


def test_source_bytes_and_calendar_tampering(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars)
    changed = mutate(path, scope, "UPDATE raw_pages SET body=x'66616b65'")
    with pytest.raises(ContractError, match="database_pin_mismatch"):
        assess(path, scope, calendars)
    with pytest.raises(ContractError, match="availability_original_source_mismatch"):
        assess(path, changed, calendars, (owner,))
    with pytest.raises(ContractError, match="calendar_pin_mismatch"):
        assess(path, changed, (replace(calendars[0], trusted_normalized_hash="0" * 64),))


def test_unsupported_authenticated_timestamp_claim_stays_a_claim(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    owner = owner_for(path, scope, calendars)
    claim = replace(
        owner.claim,
        timestamps=(
            AvailabilityTimestampClaim("authenticated_timestamp", T, "self-signed-fiction"),
        ),
    )
    owner = replace(owner, claim=claim, trusted_document_hash=claim.document.identifier)
    value = assess(path, scope, calendars, (owner,)).report.to_dict()
    assert value["observations"][0]["timestamps"]["independently_authenticated_at"] is None
    assert value["coverage"]["decision_time_availability_established"] == 0


def test_timezone_aware_timestamp_contract_is_deterministic() -> None:
    from zoneinfo import ZoneInfo

    original = AvailabilityTimestampClaim("publication", T, "negative-fixture-only")
    equivalent = AvailabilityTimestampClaim(
        "publication", T.astimezone(ZoneInfo("Asia/Seoul")), "negative-fixture-only"
    )
    assert original == equivalent
    with pytest.raises(ContractError):
        AvailabilityTimestampClaim("publication", T.replace(tzinfo=None), "negative-fixture-only")
