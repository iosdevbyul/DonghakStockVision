"""Synthetic public configuration review, never mocked external authentication."""

import base64
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.prospective_anchor_governance import (
    GAPS,
    AnchorSnapshot,
    GovernanceReference,
    review_anchor,
    verify_governance,
)

ISSUER = "synthetic_configured_issuer"
KEY_ID = "a" * 64
AT = datetime(2025, 7, 15, tzinfo=UTC)


def anchor(**changes: Any) -> FrozenJSON:
    # Public RFC8032 vector material; no production/private signing keys.
    key = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    return FrozenJSON.freeze(
        {
            "version": "prospective_receipt_trust_anchor_v1",
            "issuer": ISSUER,
            "key_id": KEY_ID,
            "public_key_base64": base64.b64encode(key).decode(),
            "valid_from": "2025-07-01T00:00:00+00:00",
            "valid_until": "2025-08-01T00:00:00+00:00",
            "status": "active",
            "revoked_receipt_ids": [],
            **changes,
        }
    )


def test_configured_anchor_is_unresolved_with_all_missing_evidence_and_no_io() -> None:
    owner = anchor()
    before = owner.payload_json
    with (
        patch("builtins.open", side_effect=AssertionError("no file IO")),
        patch("sqlite3.connect", side_effect=AssertionError("no database IO")),
        patch("httpx.Client.send", side_effect=AssertionError("no network")),
    ):
        report = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT)
        verify_governance(report, report.identifier, owner, owner.identifier, ISSUER, KEY_ID, AT)
    d = report.to_dict()
    assert d["state"] == "unresolved" and d["structurally_valid"]
    assert d["configured_key_material_consistent"] and d["public_key_bytes"] == 32
    assert d["locally_active_at_review_time"] and d["performance_metrics"] is None
    assert set(GAPS) <= set(d["reasons"])
    assert "historical_snapshot_references_missing" in d["reasons"]
    assert all(n == 0 for n in d["supplied_reference_counts"].values())
    for name in (
        "independent_issuer_authentication",
        "independent_key_custody_verified",
        "historical_trust_anchor_authentication",
        "historical_revocation_evidence_authenticated",
        "independent_clock_authentication",
        "genuine_forward_evidence",
        "pit_qualified",
        "prospective_performance_approved",
        "execution_authorized",
    ):
        assert d[name] is False
    assert owner.payload_json == before
    assert review_anchor(
        FrozenJSON(owner.payload_json), owner.identifier, ISSUER, KEY_ID, AT
    ) == FrozenJSON(report.payload_json)


@pytest.mark.parametrize(
    "at,expected",
    [
        (datetime(2025, 6, 30, 23, 59, 59, tzinfo=UTC), "not_yet_valid"),
        (datetime(2025, 7, 1, tzinfo=UTC), "within_declared_window"),
        (datetime(2025, 8, 1, tzinfo=UTC), "within_declared_window"),
        (datetime(2025, 8, 1, 0, 0, 1, tzinfo=UTC), "expired"),
    ],
)
def test_declared_validity_window_boundaries(at: datetime, expected: str) -> None:
    owner = anchor()
    d = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, at).to_dict()
    assert d["declared_window_state"] == expected
    assert not d["historical_trust_anchor_authentication"]


@pytest.mark.parametrize("status", ["inactive", "revoked"])
def test_nonactive_configuration_is_reviewed_not_approved(status: str) -> None:
    owner = anchor(status=status)
    d = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT).to_dict()
    assert d["structurally_valid"] and not d["locally_active_at_review_time"]
    assert "declared_key_" + status in d["reasons"]
    assert d["state"] == "unresolved"


def test_revocations_are_sorted_configuration_not_historical_proof() -> None:
    owner = anchor(revoked_receipt_ids=["2" * 64, "1" * 64])
    d = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT).to_dict()
    assert d["configured_revoked_receipt_ids"] == ["1" * 64, "2" * 64]
    assert not d["historical_revocation_evidence_authenticated"]


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "unknown"},
        {"issuer": "other"},
        {"key_id": "b" * 64},
        {"algorithm": "RSA"},
        {"public_key_base64": "!"},
        {"public_key_base64": base64.b64encode(b"short").decode()},
        {"valid_from": "2025-09-01T00:00:00+00:00"},
        {"valid_from": "2025-07-01T00:00:00"},
        {"valid_until": "2025-08-01T09:00:00+09:00"},
        {"status": "approved"},
        {"trusted": True},
        {"independent": True},
        {"clock_verified": True},
        {"revoked_receipt_ids": ["1" * 64, "1" * 64]},
        {"revoked_receipt_ids": [f"{i:064x}" for i in range(33)]},
    ],
)
def test_malformed_unknown_or_approval_fields_rejected(changes: dict[str, Any]) -> None:
    owner = anchor(**changes)
    with pytest.raises(ValueError):
        review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT)


def test_history_differences_are_not_authenticated_lifecycle_order() -> None:
    owner = anchor()
    revoked = anchor(status="revoked", revoked_receipt_ids=["f" * 64])
    rotated = anchor(key_id="b" * 64, public_key_base64=base64.b64encode(b"x" * 32).decode())
    history = (
        AnchorSnapshot(revoked, revoked.identifier),
        AnchorSnapshot(rotated, rotated.identifier),
    )
    report = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, history=history)
    d = report.to_dict()
    assert len(d["local_snapshot_references"]) == 3
    assert "key_status_chronology_unverified" in d["reasons"]
    assert "revocation_snapshot_differences_unresolved" in d["reasons"]
    assert "different_key_ids_without_authenticated_rotation_lineage" in d["reasons"]
    assert (
        review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, history=tuple(reversed(history)))
        == report
    )


def test_conflicting_material_for_same_key_and_duplicate_history_rejected() -> None:
    owner = anchor()
    conflicting = anchor(public_key_base64=base64.b64encode(b"x" * 32).decode())
    with pytest.raises(ValueError, match="conflicting_key_material"):
        review_anchor(
            owner,
            owner.identifier,
            ISSUER,
            KEY_ID,
            AT,
            history=(AnchorSnapshot(conflicting, conflicting.identifier),),
        )
    with pytest.raises(ValueError, match="duplicate_snapshot"):
        review_anchor(
            owner,
            owner.identifier,
            ISSUER,
            KEY_ID,
            AT,
            history=(AnchorSnapshot(owner, owner.identifier),),
        )


def test_self_attested_document_is_supplied_but_unsupported_not_authentication() -> None:
    owner = anchor()
    statement = FrozenJSON.freeze(
        {
            "version": "self_signed_claim",
            "trusted": True,
            "independent": True,
            "clock_verified": True,
        }
    )
    ref = GovernanceReference("issuer_identity", ISSUER, KEY_ID, statement, statement.identifier)
    report = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, references=(ref,))
    d = report.to_dict()
    assert d["supplied_reference_counts"]["issuer_identity"] == 1
    assert d["unsupported_evidence_references"][0]["state"] == "unsupported_evidence"
    assert not d["independent_issuer_authentication"] and set(GAPS) <= set(d["reasons"])


@pytest.mark.parametrize("kind", ["issuer", "key", "pin", "category"])
def test_reference_identity_and_pin_matching(kind: str) -> None:
    owner = anchor()
    document = FrozenJSON.freeze({"original": "unverified reference"})
    ref = GovernanceReference(
        "clock_assurance" if kind != "category" else "unknown",
        ISSUER if kind != "issuer" else "other",
        KEY_ID if kind != "key" else "b" * 64,
        document,
        document.identifier if kind != "pin" else "0" * 64,
    )
    with pytest.raises(ValueError):
        review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, references=(ref,))


def test_changed_owners_and_rehashed_report_are_rejected() -> None:
    owner = anchor()
    report = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT)
    changed = anchor(status="revoked")
    with pytest.raises(ValueError, match="anchor_pin_mismatch"):
        review_anchor(changed, owner.identifier, ISSUER, KEY_ID, AT)
    with pytest.raises(ValueError, match="report_pin_mismatch"):
        verify_governance(report, "0" * 64, owner, owner.identifier, ISSUER, KEY_ID, AT)
    forged = report.to_dict()
    forged["independent_issuer_authentication"] = True
    body = FrozenJSON.freeze(forged)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_governance(body, body.identifier, owner, owner.identifier, ISSUER, KEY_ID, AT)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_governance(
            report, report.identifier, changed, changed.identifier, ISSUER, KEY_ID, AT
        )


def test_bounds_and_versions_before_io() -> None:
    owner = anchor()
    large = FrozenJSON.freeze({"large": "x" * 8192})
    with patch("builtins.open", side_effect=AssertionError("no IO")):
        with pytest.raises(ValueError, match="document_budget"):
            review_anchor(large, large.identifier, ISSUER, KEY_ID, AT)
        with pytest.raises(ValueError, match="history_budget"):
            review_anchor(
                owner,
                owner.identifier,
                ISSUER,
                KEY_ID,
                AT,
                history=(AnchorSnapshot(owner, owner.identifier),) * 9,
            )
        large_report = FrozenJSON.freeze({"large": "x" * (32 * 1024)})
        with pytest.raises(ValueError, match="report_budget"):
            verify_governance(
                large_report, large_report.identifier, owner, owner.identifier, ISSUER, KEY_ID, AT
            )
        unknown = FrozenJSON.freeze({"version": "unknown"})
        with pytest.raises(ValueError, match="report_version"):
            verify_governance(
                unknown, unknown.identifier, owner, owner.identifier, ISSUER, KEY_ID, AT
            )


def test_reference_and_aggregate_bounds_are_checked_before_interpretation() -> None:
    owner = anchor()
    document = FrozenJSON.freeze({"body": "x" * 8170})
    ref = GovernanceReference("key_custody", ISSUER, KEY_ID, document, document.identifier)
    with pytest.raises(ValueError, match="reference_budget"):
        review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, references=(ref,) * 9)
    with pytest.raises(ValueError, match="total_input_budget"):
        review_anchor(
            owner,
            owner.identifier,
            ISSUER,
            KEY_ID,
            AT,
            history=(AnchorSnapshot(document, document.identifier),) * 8,
            references=(ref,) * 8,
        )


def test_reference_order_is_deterministic_and_duplicates_are_rejected() -> None:
    owner = anchor()
    a = FrozenJSON.freeze({"unverified": "identity document"})
    b = FrozenJSON.freeze({"unverified": "clock document"})
    refs = (
        GovernanceReference("issuer_identity", ISSUER, KEY_ID, a, a.identifier),
        GovernanceReference("clock_assurance", ISSUER, KEY_ID, b, b.identifier),
    )
    result = review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, references=refs)
    assert (
        review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, references=tuple(reversed(refs)))
        == result
    )
    with pytest.raises(ValueError, match="duplicate_reference"):
        review_anchor(owner, owner.identifier, ISSUER, KEY_ID, AT, references=(refs[0], refs[0]))
