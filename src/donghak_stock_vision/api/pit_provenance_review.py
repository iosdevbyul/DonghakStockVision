"""Read-only binding of PIT features to declared snapshot provenance evidence.

Matching hashes are diagnostics, never independent source authentication.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.api.pit_inference_input import PITInferenceInput


class PITSourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["pit_source_evidence_v1"]
    snapshotId: str = Field(min_length=1)
    inputHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ticker: str = Field(min_length=1)
    lastTradingDate: str
    featureVersion: str = Field(min_length=1)
    snapshotCutoff: datetime
    revisionHistoryStatus: Literal["verified", "unavailable", "unknown"]
    corporateActionStatus: Literal["verified", "unverified", "unknown"]
    calendarStatus: Literal["verified", "unverified", "unknown"]
    qualityFlags: tuple[str, ...]

    @model_validator(mode="after")
    def require_timezone(self) -> "PITSourceEvidence":
        if self.snapshotCutoff.utcoffset() is None:
            raise ValueError("source evidence cutoff must have timezone")
        return self


@dataclass(frozen=True)
class PITProvenanceReview:
    evidence_bound: bool
    provenance_authenticated: bool
    inference_authorized: bool
    blockers: tuple[str, ...]


def inspect_pit_provenance(
    candidate_bytes: bytes,
    evidence_bytes: bytes,
    *,
    trusted_evidence_sha256: str,
) -> PITProvenanceReview:
    """Require out-of-band fingerprint but never treat it as issuer authentication."""
    blockers: list[str] = []
    try:
        candidate = PITInferenceInput.model_validate_json(candidate_bytes)
        evidence = PITSourceEvidence.model_validate_json(evidence_bytes)
    except (ValueError, UnicodeError):
        return PITProvenanceReview(
            False, False, False, ("invalid_pit_candidate_or_evidence",)
        )

    if (
        len(trusted_evidence_sha256) != 64
        or any(char not in "0123456789abcdef" for char in trusted_evidence_sha256)
        or hashlib.sha256(evidence_bytes).hexdigest() != trusted_evidence_sha256
    ):
        blockers.append("independent_evidence_pin_mismatch")
    if (
        evidence.snapshotId != candidate.snapshotId
        or evidence.inputHash != candidate.inputHash
        or evidence.ticker != candidate.ticker
        or evidence.lastTradingDate != candidate.lastTradingDate
        or evidence.featureVersion != candidate.featureVersion
        or evidence.snapshotCutoff != candidate.sourceCutoff
        or evidence.qualityFlags != candidate.qualityFlags
    ):
        blockers.append("pit_evidence_candidate_mismatch")
    if evidence.revisionHistoryStatus != "verified":
        blockers.append("pit_revision_history_not_verified")
    if evidence.corporateActionStatus != "verified":
        blockers.append("pit_corporate_actions_not_verified")
    if evidence.calendarStatus != "verified":
        blockers.append("pit_calendar_not_verified")
    if candidate.qualityFlags:
        blockers.append("pit_snapshot_quality_flags_unresolved")

    matched = not blockers
    blockers.append("independent_source_issuer_signature_missing")
    blockers.append("operational_execution_not_authorized")
    return PITProvenanceReview(
        evidence_bound=matched,
        provenance_authenticated=False,
        inference_authorized=False,
        blockers=tuple(blockers),
    )
