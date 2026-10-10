"""Verify independently signed deployment approval receipts without granting execution."""

import base64
import binascii
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.api.operational_registry import OperationalRegistrationProposal


class DeploymentApprovalReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["operational_deployment_approval_v1"]
    proposalSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualificationRegistrySha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    modelId: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    checkpointSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment: Literal["production", "staging"]
    purpose: Literal["pit_research_inference"]
    approver: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    approvedAt: datetime
    expiresAt: datetime

    @model_validator(mode="after")
    def check_dates(self) -> "DeploymentApprovalReceipt":
        if (
            self.approvedAt.utcoffset() is None
            or self.expiresAt.utcoffset() is None
            or self.expiresAt <= self.approvedAt
        ):
            raise ValueError("invalid approval lifetime")
        return self


class SignedDeploymentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["signed_deployment_approval_v1"]
    keyId: str = Field(min_length=1)
    signatureBase64: str = Field(min_length=1)


@dataclass(frozen=True)
class TrustedDeploymentApprover:
    key_id: str
    approver: str
    public_key: bytes
    active: bool

    def __post_init__(self) -> None:
        if not self.key_id or not self.approver or len(self.public_key) != 32:
            raise ValueError("invalid deployment approver")


@dataclass(frozen=True)
class DeploymentApprovalAssessment:
    receipt_authenticated: bool
    registration_authorized: bool
    inference_authorized: bool
    blockers: tuple[str, ...]


def inspect_deployment_approval(
    proposal_bytes: bytes,
    registry_bytes: bytes,
    receipt_bytes: bytes,
    signature_envelope_bytes: bytes,
    trusted_approvers: tuple[TrustedDeploymentApprover, ...],
    *,
    environment: str,
    assessed_at: datetime,
) -> DeploymentApprovalAssessment:
    """Only checks a scoped receipt; never mints capability or registers a model."""
    try:
        proposal = OperationalRegistrationProposal.model_validate_json(proposal_bytes)
        receipt = DeploymentApprovalReceipt.model_validate_json(receipt_bytes)
        envelope = SignedDeploymentEnvelope.model_validate_json(signature_envelope_bytes)
    except (ValueError, UnicodeError):
        return DeploymentApprovalAssessment(
            False, False, False, ("invalid_deployment_approval_contract",)
        )

    blockers: list[str] = []
    if (
        receipt.proposalSha256 != hashlib.sha256(proposal_bytes).hexdigest()
        or receipt.qualificationRegistrySha256 != hashlib.sha256(registry_bytes).hexdigest()
        or receipt.modelId != proposal.modelId
        or receipt.modelVersion != proposal.modelVersion
        or receipt.checkpointSha256 != proposal.artifactSha256
    ):
        blockers.append("deployment_identity_mismatch")
    if receipt.environment != environment:
        blockers.append("deployment_environment_mismatch")
    if assessed_at.utcoffset() is None or not receipt.approvedAt <= assessed_at < receipt.expiresAt:
        blockers.append("deployment_approval_expired_or_not_yet_valid")
    if receipt.decision != "approved":
        blockers.append("deployment_not_approved")
    if receipt.approver == proposal.proposer:
        blockers.append("self_approval_not_allowed")

    matches = [key for key in trusted_approvers if key.key_id == envelope.keyId]
    if len(matches) != 1 or not matches[0].active:
        blockers.append("deployment_approver_untrusted_or_revoked")
    elif matches[0].approver != receipt.approver:
        blockers.append("deployment_approver_identity_mismatch")
    else:
        try:
            signature = base64.b64decode(envelope.signatureBase64, validate=True)
            if len(signature) != 64:
                raise ValueError("invalid Ed25519 signature")
            message = b"DonghakStockVision:deployment-approval:v1\x00" + receipt_bytes
            Ed25519PublicKey.from_public_bytes(matches[0].public_key).verify(signature, message)
        except (InvalidSignature, ValueError, binascii.Error):
            blockers.append("invalid_deployment_approval_signature")
    return DeploymentApprovalAssessment(
        receipt_authenticated=not blockers,
        registration_authorized=False,
        inference_authorized=False,
        blockers=tuple(blockers),
    )
