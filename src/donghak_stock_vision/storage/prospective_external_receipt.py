"""Pinned Ed25519 signed assertions, not RFC3161 or independent custody approval."""

import base64
from dataclasses import dataclass
from datetime import datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, text, utc

DOMAIN = b"DSV prospective signed assertion v1 / Ed25519\x00"
MAX_ARTIFACT = 128 * 1024
MAX_DOCUMENT = 8192
MAX_REPORT = 32 * 1024
VERSIONS = {
    "policy": "prospective_evaluation_policy_v1",
    "prediction": "forward_prediction_evidence_v1",
}


@dataclass(frozen=True)
class ReceiptOwner:
    role: str
    artifact: FrozenJSON
    artifact_pin: str
    receipt_id: str
    receipt: FrozenJSON | None
    receipt_pin: str | None


def _bounded(owner: FrozenJSON, limit: int) -> None:
    require(
        type(owner) is FrozenJSON and len(owner.payload_json.encode()) <= limit,
        "receipt_document_budget",
    )


def _instant(value: object) -> datetime:
    require(
        type(value) is str and utc(value).isoformat() == value, "receipt_canonical_utc_required"
    )
    return utc(value)


def _decode(value: object, length: int) -> bytes:
    require(type(value) is str and len(value) <= 128, "receipt_base64_bound")
    raw = base64.b64decode(str(value), validate=True)
    require(
        len(raw) == length and base64.b64encode(raw).decode() == value, "receipt_base64_encoding"
    )
    return raw


def _verify_signature(key: bytes, signature: bytes, message: bytes) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(key).verify(signature, message)


def verify_receipts(
    owners: tuple[ReceiptOwner, ...],
    anchor: FrozenJSON | None,
    anchor_pin: str | None,
    as_of: datetime,
    *,
    outcome_observed_at: datetime | None = None,
) -> FrozenJSON:
    require(type(owners) is tuple and len(owners) == 2, "receipt_exact_policy_prediction_required")
    require(all(type(o) is ReceiptOwner for o in owners), "unsupported_receipt_owner")
    require({o.role for o in owners} == set(VERSIONS), "receipt_duplicate_or_wrong_roles")
    require(len({o.receipt_id for o in owners}) == 2, "duplicate_receipt_identity")
    at = utc(as_of)
    outcome = None if outcome_observed_at is None else utc(outcome_observed_at)
    require(outcome is None or outcome <= at, "receipt_future_outcome_context")
    for owner in owners:
        sha(owner.receipt_id)
        sha(owner.artifact_pin)
        if owner.receipt_pin is not None:
            sha(owner.receipt_pin)
        _bounded(owner.artifact, MAX_ARTIFACT)
        if owner.receipt is not None:
            _bounded(owner.receipt, MAX_DOCUMENT)
    if anchor_pin is not None:
        sha(anchor_pin)
    require(len({o.artifact.identifier for o in owners}) == 2, "duplicate_artifact_identity")
    if anchor is not None:
        _bounded(anchor, MAX_DOCUMENT)
    rows = []
    signed_times = {}
    for owner in sorted(owners, key=lambda o: o.role):
        row: dict[str, object] = {
            "role": owner.role,
            "artifact_id": owner.artifact.identifier,
            "expected_receipt_id": owner.receipt_id,
            "receipt_pin": owner.receipt_pin,
            "structurally_valid": False,
            "signature_verified": False,
            "signed_issuer_time": None,
            "issuer_independence_verified": False,
        }
        state, reason = "invalid", "malformed_receipt_or_anchor"
        try:
            require(owner.artifact.identifier == sha(owner.artifact_pin), "artifact_pin_mismatch")
            require(
                owner.artifact.to_dict().get("version") == VERSIONS[owner.role],
                "unsupported_original_artifact_version",
            )
            if owner.receipt is None:
                require(owner.receipt_pin is None, "missing_receipt_with_pin")
                state, reason = "unsupported_receipt", "receipt_missing"
            else:
                require(owner.receipt.identifier == sha(owner.receipt_pin), "receipt_pin_mismatch")
                envelope = fields(
                    owner.receipt.to_dict(), "version algorithm payload signature_base64"
                )
                if (
                    envelope["version"] != "prospective_ed25519_receipt_v1"
                    or envelope["algorithm"] != "Ed25519"
                ):
                    state, reason = "unsupported_receipt", "unsupported_receipt_format_or_algorithm"
                else:
                    payload = fields(
                        envelope["payload"],
                        "receipt_id issuer key_id artifact_role artifact_id artifact_digest "
                        "artifact_encoding issued_at",
                    )
                    text(payload["issuer"])
                    require(len(payload["issuer"]) <= 128, "receipt_issuer_bound")
                    sha(payload["key_id"])
                    issued = _instant(payload["issued_at"])
                    signature = _decode(envelope["signature_base64"], 64)
                    require(payload["receipt_id"] == owner.receipt_id, "receipt_identity_mismatch")
                    row["structurally_valid"] = True
                    if (
                        payload["artifact_role"] != owner.role
                        or payload["artifact_id"] != owner.artifact.identifier
                        or payload["artifact_digest"] != owner.artifact.identifier
                        or payload["artifact_encoding"] != "dsv_canonical_json_sha256_v1"
                    ):
                        state, reason = "artifact_mismatch", "receipt_artifact_binding_mismatch"
                    elif anchor is None:
                        state, reason = (
                            "missing_trust_anchor",
                            "explicit_pinned_trust_anchor_required",
                        )
                    else:
                        require(anchor.identifier == sha(anchor_pin), "untrusted_anchor_pin")
                        trust = fields(
                            anchor.to_dict(),
                            "version issuer key_id public_key_base64 valid_from valid_until "
                            "status revoked_receipt_ids",
                        )
                        require(
                            trust["version"] == "prospective_receipt_trust_anchor_v1",
                            "unsupported_trust_anchor_version",
                        )
                        require(trust["status"] == "active", "trust_anchor_revoked_or_inactive")
                        revoked = trust["revoked_receipt_ids"]
                        require(
                            type(revoked) is list and len(revoked) <= 32,
                            "receipt_revocation_budget",
                        )
                        for identity in revoked:
                            sha(identity)
                        require(len(set(revoked)) == len(revoked), "duplicate_revoked_receipt_id")
                        require(
                            owner.receipt_id not in revoked, "receipt_revoked_in_pinned_snapshot"
                        )
                        key = _decode(trust["public_key_base64"], 32)
                        require(
                            trust["issuer"] == payload["issuer"]
                            and trust["key_id"] == payload["key_id"],
                            "receipt_issuer_or_key_mismatch",
                        )
                        require(
                            _instant(trust["valid_from"])
                            <= issued
                            <= _instant(trust["valid_until"])
                            and issued <= at,
                            "receipt_issuance_outside_trusted_window",
                        )
                        try:
                            from cryptography.exceptions import (
                                InvalidSignature,
                                UnsupportedAlgorithm,
                            )

                            _verify_signature(
                                key,
                                signature,
                                DOMAIN + FrozenJSON.freeze(payload).payload_json.encode(),
                            )
                        except ImportError:
                            state, reason = "unsupported_receipt", "existing_crypto_extra_required"
                        except UnsupportedAlgorithm:
                            state, reason = "unsupported_receipt", "ed25519_backend_unavailable"
                        except InvalidSignature:
                            state, reason = "invalid_signature", "ed25519_signature_invalid"
                        else:
                            state, reason = (
                                "verified_cryptographic_structure",
                                "signature_matches_explicit_pinned_key",
                            )
                            row["signature_verified"] = True
                            row["signed_issuer_time"] = issued.isoformat()
                            signed_times[owner.role] = issued
        except (ValueError, KeyError, TypeError) as error:
            # Only fixed local contract codes may leave this boundary.
            code = str(error)
            reason = (
                code
                if code.replace("_", "").isalnum() and len(code) <= 100
                else "malformed_receipt_or_anchor"
            )
        rows.append({**row, "state": state, "reason": reason})
    order = None
    if len(signed_times) == 2:
        order = signed_times["policy"] < signed_times["prediction"]
        if outcome is not None:
            order = order and signed_times["prediction"] < outcome
    report = FrozenJSON.freeze(
        {
            "version": "prospective_external_receipt_verification_v1",
            "as_of": at.isoformat(),
            "anchor_pin": anchor_pin,
            "receipts": rows,
            "state": "invalid" if order is False else "chronology_unresolved",
            "reason": "inconsistent_signed_time_order"
            if order is False
            else "independent_issuer_and_clock_evidence_unavailable",
            "signed_time_order_consistent": order,
            "outcome_time_context": None if outcome is None else outcome.isoformat(),
            "outcome_time_authenticated": False,
            "issuer_independence_verified": False,
            "independently_authenticated_chronology": False,
            "original_source_replay_verified": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "performance_approved": False,
            "execution_authorized": False,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_REPORT, "receipt_report_budget")
    return report


def verify_report(
    report: FrozenJSON,
    trusted_pin: str,
    owners: tuple[ReceiptOwner, ...],
    anchor: FrozenJSON | None,
    anchor_pin: str | None,
    as_of: datetime,
    *,
    outcome_observed_at: datetime | None = None,
) -> None:
    _bounded(report, MAX_REPORT)
    require(report.identifier == sha(trusted_pin), "receipt_report_pin_mismatch")
    require(
        verify_receipts(owners, anchor, anchor_pin, as_of, outcome_observed_at=outcome_observed_at)
        == report,
        "receipt_report_semantic_mismatch",
    )
