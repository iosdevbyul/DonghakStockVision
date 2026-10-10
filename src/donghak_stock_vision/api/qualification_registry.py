"""Pinned qualification registry diagnostics, not an operational authorization service."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.api.operational_registry import OperationalRegistrationProposal
from donghak_stock_vision.api.registration_review_gate import RegistrationReviewReport


class QualificationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposalSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    modelId: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    artifactSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualificationId: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["qualified", "revoked"]
    validFrom: datetime
    validUntil: datetime

    @model_validator(mode="after")
    def check_dates(self) -> "QualificationRecord":
        if (
            self.validFrom.utcoffset() is None
            or self.validUntil.utcoffset() is None
            or self.validUntil <= self.validFrom
        ):
            raise ValueError("invalid qualification period")
        return self


class QualificationRegistryDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: Literal["operational_qualification_registry_v1"]
    records: tuple[QualificationRecord, ...]

    @model_validator(mode="after")
    def unique_proposals(self) -> "QualificationRegistryDocument":
        ids = [item.proposalSha256 for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate qualification proposal")
        return self


@dataclass(frozen=True)
class QualificationAssessment:
    qualification_record_valid: bool
    ready_for_external_authorization: bool
    registration_authorized: bool
    blockers: tuple[str, ...]


def inspect_qualification(
    proposal_bytes: bytes,
    review_report: RegistrationReviewReport,
    registry_path: Path,
    *,
    independently_trusted_registry_sha256: str,
    assessed_at: datetime,
) -> QualificationAssessment:
    """Never infer trust from an in-band hash or turn qualifications into permissions."""
    blockers: list[str] = []
    if assessed_at.utcoffset() is None:
        blockers.append("assessment_timestamp_requires_timezone")
    if not review_report.ready_for_independent_qualification or not (
        review_report.contract_valid
        and review_report.reviewer_signature_valid
        and review_report.artifact_structurally_consistent
    ):
        blockers.append("registration_review_not_ready")
    if len(independently_trusted_registry_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in independently_trusted_registry_sha256
    ):
        blockers.append("independent_registry_pin_missing")
        return QualificationAssessment(False, False, False, tuple(blockers))

    try:
        raw = registry_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != independently_trusted_registry_sha256:
            raise ValueError("registry hash mismatch")
        registry = QualificationRegistryDocument.model_validate_json(raw)
        proposal = OperationalRegistrationProposal.model_validate_json(proposal_bytes)
        proposal_hash = hashlib.sha256(proposal_bytes).hexdigest()
        matching = [r for r in registry.records if r.proposalSha256 == proposal_hash]
        if len(matching) != 1:
            blockers.append("qualification_record_missing")
        else:
            record = matching[0]
            if (
                record.modelId != proposal.modelId
                or record.modelVersion != proposal.modelVersion
                or record.artifactSha256 != proposal.artifactSha256
                or record.qualificationId != proposal.qualificationId
            ):
                blockers.append("qualification_identity_mismatch")
            if record.status != "qualified":
                blockers.append("qualification_revoked")
            if assessed_at.utcoffset() is not None and not (
                record.validFrom <= assessed_at < record.validUntil
            ):
                blockers.append("qualification_outside_validity")
    except (OSError, ValueError, UnicodeError):
        blockers.append("qualification_registry_invalid_or_untrusted")

    # File hashes and reviewer claims are not proof of an authoritative registry.
    blockers.append("authoritative_registry_identity_not_authenticated")
    valid = not (set(blockers) - {"authoritative_registry_identity_not_authenticated"})
    return QualificationAssessment(
        qualification_record_valid=valid,
        ready_for_external_authorization=valid,
        registration_authorized=False,
        blockers=tuple(dict.fromkeys(blockers)),
    )
