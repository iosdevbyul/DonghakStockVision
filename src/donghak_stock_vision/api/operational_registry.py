"""Non-authorizing contract for a separately reviewed operational model registration.

This validates proposed registration metadata, not artifact provenance or signatures.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegistrationRejected(ValueError):
    """A proposed operational model registration fails its static contract."""


class OperationalRegistrationProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: Literal["operational_model_registration_v1"]
    modelId: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    direction: Literal["up", "down"]
    artifactSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifactManifestFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    experimentLockId: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualificationId: str = Field(pattern=r"^[0-9a-f]{64}$")
    trainingSnapshotId: str = Field(min_length=1)
    featureVersion: str = Field(min_length=1)
    labelVersion: str = Field(min_length=1)
    usageRestriction: Literal["operational_review_required"]
    operationalStatus: Literal["proposed"]
    proposer: str = Field(min_length=1)
    proposedAt: datetime

    @model_validator(mode="after")
    def require_timezone(self) -> "OperationalRegistrationProposal":
        if self.proposedAt.utcoffset() is None:
            raise ValueError("proposal requires timezone-aware timestamp")
        return self


class IndependentReviewClaim(BaseModel):
    """A claim only; an unsigned JSON statement is NOT authenticated approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: Literal["operational_model_review_v1"]
    proposalSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    reviewedAt: datetime

    @model_validator(mode="after")
    def require_timezone(self) -> "IndependentReviewClaim":
        if self.reviewedAt.utcoffset() is None:
            raise ValueError("review requires timezone-aware timestamp")
        return self


class OperationalRegistrationAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    structurallyEligible: bool
    registrationAuthorized: Literal[False] = False
    blockers: tuple[str, ...]


def assess_registration(
    proposal_bytes: bytes,
    review_bytes: bytes | None = None,
) -> OperationalRegistrationAssessment:
    """No file writes, no model loading, and never authorizes operational use."""
    import hashlib

    blockers: list[str] = []
    proposal: OperationalRegistrationProposal | None = None
    try:
        proposal = OperationalRegistrationProposal.model_validate_json(proposal_bytes)
    except (ValueError, UnicodeError):
        blockers.append("invalid_operational_registration_proposal")
    if review_bytes is None:
        blockers.append("independent_review_missing")
    else:
        try:
            review = IndependentReviewClaim.model_validate_json(review_bytes)
            if (
                proposal is None
                or review.proposalSha256 != hashlib.sha256(proposal_bytes).hexdigest()
            ):
                blockers.append("review_proposal_hash_mismatch")
            if proposal is not None and review.reviewer == proposal.proposer:
                blockers.append("self_review_not_allowed")
            if review.decision != "approved":
                blockers.append("review_not_approved")
            if proposal is not None and review.reviewedAt < proposal.proposedAt:
                blockers.append("review_precedes_proposal")
        except (ValueError, UnicodeError):
            blockers.append("invalid_independent_review_claim")
    blockers.append("authenticated_reviewer_signature_required")
    blockers.append("operational_artifact_provenance_not_verified")
    return OperationalRegistrationAssessment(
        structurallyEligible=not (
            set(blockers)
            - {
                "authenticated_reviewer_signature_required",
                "operational_artifact_provenance_not_verified",
            }
        ),
        blockers=tuple(dict.fromkeys(blockers)),
    )
