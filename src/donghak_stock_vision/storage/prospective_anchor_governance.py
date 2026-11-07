"""Offline configuration consistency, never issuer/lifecycle/clock authentication."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, text, utc
from donghak_stock_vision.storage.prospective_external_receipt import MAX_DOCUMENT, _decode

MAX_REFERENCES = 8
MAX_INPUT = 128 * 1024
MAX_REPORT = 32 * 1024
CATEGORIES = (
    "issuer_identity",
    "key_custody",
    "clock_assurance",
    "key_rotation",
    "retirement",
    "historical_revocation",
)
GAPS = (
    "independent_issuer_identity_missing_or_unverified",
    "independent_key_custody_missing_or_unverified",
    "historical_signing_authority_unverified",
    "historical_revocation_completeness_and_freshness_unverified",
    "independent_clock_assurance_missing_or_unverified",
    "key_rotation_lineage_missing_or_unverified",
    "key_retirement_provenance_missing_or_unverified",
)


@dataclass(frozen=True)
class AnchorSnapshot:
    owner: FrozenJSON
    pin: str


@dataclass(frozen=True)
class GovernanceReference:
    category: str
    issuer: str
    key_id: str
    document: FrozenJSON
    pin: str


def _bounded(owner: FrozenJSON) -> int:
    require(type(owner) is FrozenJSON, "governance_original_owner_required")
    size = len(owner.payload_json.encode())
    require(size <= MAX_DOCUMENT, "governance_document_budget")
    return size


def _instant(value: Any) -> datetime:
    require(
        type(value) is str and utc(value).isoformat() == value, "governance_canonical_utc_required"
    )
    return utc(value)


def _anchor(owner: FrozenJSON, pin: str, issuer: str) -> dict[str, Any]:
    require(owner.identifier == sha(pin), "governance_anchor_pin_mismatch")
    d = fields(
        owner.to_dict(),
        "version issuer key_id public_key_base64 valid_from valid_until status revoked_receipt_ids",
    )
    require(d["version"] == "prospective_receipt_trust_anchor_v1", "governance_anchor_version")
    require(text(d["issuer"]) == issuer, "governance_issuer_mismatch")
    sha(d["key_id"])
    _decode(d["public_key_base64"], 32)  # Same strict encoding/length as the receipt verifier.
    require(
        _instant(d["valid_from"]) <= _instant(d["valid_until"]), "governance_contradictory_validity"
    )
    require(
        type(d["status"]) is str and d["status"] in {"active", "revoked", "inactive"},
        "governance_unsupported_status",
    )
    ids = d["revoked_receipt_ids"]
    require(type(ids) is list and len(ids) <= 32, "governance_revocation_budget")
    for identity in ids:
        sha(identity)
    require(len(set(ids)) == len(ids), "governance_duplicate_revocation_identity")
    return d


def review_anchor(
    owner: FrozenJSON,
    anchor_pin: str,
    expected_issuer: str,
    expected_key_id: str,
    as_of: datetime,
    *,
    history: tuple[AnchorSnapshot, ...] = (),
    references: tuple[GovernanceReference, ...] = (),
) -> FrozenJSON:
    require(type(history) is tuple and len(history) <= MAX_REFERENCES, "governance_history_budget")
    require(
        type(references) is tuple and len(references) <= MAX_REFERENCES,
        "governance_reference_budget",
    )
    issuer = text(expected_issuer)
    require(len(issuer) <= 128, "governance_issuer_budget")
    sha(expected_key_id)
    total = _bounded(owner)
    for snapshot in history:
        require(type(snapshot) is AnchorSnapshot, "governance_snapshot_owner_required")
        total += _bounded(snapshot.owner)
    for reference in references:
        require(type(reference) is GovernanceReference, "governance_reference_owner_required")
        total += _bounded(reference.document)
    require(total <= MAX_INPUT, "governance_total_input_budget")
    at = utc(as_of)
    current = _anchor(owner, anchor_pin, issuer)
    require(current["key_id"] == expected_key_id, "governance_key_id_mismatch")
    snapshots = [(anchor_pin, current)]
    pins = {anchor_pin}
    material = {expected_key_id: current["public_key_base64"]}
    for snapshot in history:
        d = _anchor(snapshot.owner, snapshot.pin, issuer)
        require(snapshot.pin not in pins, "governance_duplicate_snapshot")
        pins.add(snapshot.pin)
        require(
            d["key_id"] not in material or material[d["key_id"]] == d["public_key_base64"],
            "governance_conflicting_key_material",
        )
        material[d["key_id"]] = d["public_key_base64"]
        snapshots.append((snapshot.pin, d))
    reference_rows = []
    reference_pins: set[str] = set()
    for reference in references:
        require(reference.category in CATEGORIES, "governance_unsupported_reference_category")
        require(
            reference.issuer == issuer and reference.key_id in material,
            "governance_reference_identity_mismatch",
        )
        require(
            reference.document.identifier == sha(reference.pin), "governance_reference_pin_mismatch"
        )
        require(reference.pin not in reference_pins, "governance_duplicate_reference")
        reference_pins.add(reference.pin)
        reference_rows.append(
            {
                "category": reference.category,
                "key_id": reference.key_id,
                "document_pin": reference.pin,
                "state": "unsupported_evidence",
                "reason": "independent_document_authentication_not_supported",
            }
        )
    start, end = _instant(current["valid_from"]), _instant(current["valid_until"])
    window = "not_yet_valid" if at < start else "expired" if at > end else "within_declared_window"
    reasons = list(GAPS)
    if window != "within_declared_window":
        reasons.append("declared_key_" + window)
    if current["status"] != "active":
        reasons.append("declared_key_" + current["status"])
    if not history:
        reasons.append("historical_snapshot_references_missing")
    if references:
        reasons.append("supplied_independent_evidence_verifier_unavailable")
    if len(material) > 1:
        reasons.append("different_key_ids_without_authenticated_rotation_lineage")
    if any(d["status"] != current["status"] for _, d in snapshots):
        reasons.append("key_status_chronology_unverified")
    if any(
        set(d["revoked_receipt_ids"]) != set(current["revoked_receipt_ids"]) for _, d in snapshots
    ):
        reasons.append("revocation_snapshot_differences_unresolved")
    report = FrozenJSON.freeze(
        {
            "version": "prospective_trust_anchor_governance_v1",
            "as_of": at.isoformat(),
            "state": "unresolved",
            "scope": "offline_configuration_and_provenance_gap_review",
            "anchor_pin": anchor_pin,
            "issuer": issuer,
            "key_id": expected_key_id,
            "algorithm": "Ed25519",
            "public_key_bytes": 32,
            "configured_public_key_digest": sha256(
                _decode(current["public_key_base64"], 32)
            ).hexdigest(),
            "structurally_valid": True,
            "configured_key_material_consistent": True,
            "declared_status": current["status"],
            "declared_window_state": window,
            "valid_from": current["valid_from"],
            "valid_until": current["valid_until"],
            "locally_active_at_review_time": window == "within_declared_window"
            and current["status"] == "active",
            "configured_revoked_receipt_ids": sorted(current["revoked_receipt_ids"]),
            "local_snapshot_references": [
                {
                    "anchor_pin": pin,
                    "key_id": d["key_id"],
                    "declared_status": d["status"],
                    "configured_revoked_receipt_ids": sorted(d["revoked_receipt_ids"]),
                    "public_key_digest": sha256(_decode(d["public_key_base64"], 32)).hexdigest(),
                    "valid_from": d["valid_from"],
                    "valid_until": d["valid_until"],
                }
                for pin, d in sorted(snapshots, key=lambda item: item[0])
            ],
            "supplied_reference_counts": {
                category: sum(r.category == category for r in references) for category in CATEGORIES
            },
            "unsupported_evidence_references": sorted(
                reference_rows, key=lambda r: (r["category"], r["document_pin"])
            ),
            "reasons": sorted(reasons),
            "independent_issuer_authentication": False,
            "independent_key_custody_verified": False,
            "historical_trust_anchor_authentication": False,
            "historical_revocation_evidence_authenticated": False,
            "independent_clock_authentication": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "prospective_performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_REPORT, "governance_report_budget")
    return report


def verify_governance(
    report: FrozenJSON,
    trusted_report_pin: str,
    owner: FrozenJSON,
    anchor_pin: str,
    expected_issuer: str,
    expected_key_id: str,
    as_of: datetime,
    *,
    history: tuple[AnchorSnapshot, ...] = (),
    references: tuple[GovernanceReference, ...] = (),
) -> None:
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_REPORT,
        "governance_report_budget",
    )
    require(report.identifier == sha(trusted_report_pin), "governance_report_pin_mismatch")
    require(
        report.to_dict().get("version") == "prospective_trust_anchor_governance_v1",
        "governance_report_version",
    )
    require(
        review_anchor(
            owner,
            anchor_pin,
            expected_issuer,
            expected_key_id,
            as_of,
            history=history,
            references=references,
        )
        == report,
        "governance_semantic_mismatch",
    )
