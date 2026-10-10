"""Verify detached Ed25519 independent-review signatures against locally pinned keys.

This verifies receipt authenticity, NOT model registration or artifact approval.
"""

import base64
import binascii
import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from donghak_stock_vision.api.operational_registry import (
    IndependentReviewClaim,
    OperationalRegistrationProposal,
)


class SignedReviewEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: str = Field(pattern=r"^signed_operational_model_review_v1$")
    keyId: str = Field(min_length=1)
    signatureBase64: str = Field(min_length=1)


@dataclass(frozen=True)
class TrustedReviewerKey:
    key_id: str
    reviewer: str
    public_key: bytes
    active: bool

    def __post_init__(self) -> None:
        if not self.key_id or not self.reviewer or len(self.public_key) != 32:
            raise ValueError("invalid trusted reviewer key")


@dataclass(frozen=True)
class ReviewSignatureAssessment:
    signature_valid: bool
    blockers: tuple[str, ...]
    registration_authorized: bool = False


def verify_review_signature(
    proposal_bytes: bytes,
    review_bytes: bytes,
    envelope_bytes: bytes,
    trusted_keys: tuple[TrustedReviewerKey, ...],
) -> ReviewSignatureAssessment:
    """Never accept a public key supplied in the untrusted review envelope."""
    blockers: list[str] = []
    try:
        proposal = OperationalRegistrationProposal.model_validate_json(proposal_bytes)
        review = IndependentReviewClaim.model_validate_json(review_bytes)
        envelope = SignedReviewEnvelope.model_validate_json(envelope_bytes)
    except (ValueError, UnicodeError):
        return ReviewSignatureAssessment(False, ("invalid_review_contract",))

    if review.proposalSha256 != hashlib.sha256(proposal_bytes).hexdigest():
        blockers.append("review_proposal_hash_mismatch")
    if review.reviewer == proposal.proposer:
        blockers.append("self_review_not_allowed")
    if review.decision != "approved":
        blockers.append("review_not_approved")
    if review.reviewedAt < proposal.proposedAt:
        blockers.append("review_precedes_proposal")

    matches = [key for key in trusted_keys if key.key_id == envelope.keyId]
    if len(matches) != 1 or not matches[0].active:
        blockers.append("reviewer_key_untrusted_or_revoked")
    else:
        key = matches[0]
        if key.reviewer != review.reviewer:
            blockers.append("reviewer_key_identity_mismatch")
        else:
            try:
                signature = base64.b64decode(envelope.signatureBase64, validate=True)
                if len(signature) != 64:
                    raise ValueError("invalid Ed25519 signature length")
                Ed25519PublicKey.from_public_bytes(key.public_key).verify(
                    signature,
                    b"DonghakStockVision:operational-model-review:v1\x00" + review_bytes,
                )
            except (InvalidSignature, ValueError, binascii.Error):
                blockers.append("invalid_review_signature")

    return ReviewSignatureAssessment(
        signature_valid=not blockers,
        blockers=tuple(blockers),
        registration_authorized=False,
    )
