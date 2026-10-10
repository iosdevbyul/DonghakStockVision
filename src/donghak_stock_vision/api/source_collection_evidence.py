"""Read-only inspection of archived collection evidence, without authority escalation."""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.providers.base import RawPage


class SourceCollectionEvidence(BaseModel):
    """Declarative evidence for one archived raw response, not an attestation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["source_collection_evidence_v1"]
    provider: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    sourceDate: date | None
    collectedAt: datetime
    runId: str = Field(min_length=1)
    collectorName: str = Field(min_length=1)
    collectorVersion: str = Field(min_length=1)
    rawSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observationKind: Literal["initial", "revision"]
    previousRawSha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshotId: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observation(self) -> "SourceCollectionEvidence":
        if self.collectedAt.utcoffset() is None:
            raise ValueError("collection time must be timezone-aware")
        if self.observationKind == "initial" and self.previousRawSha256 is not None:
            raise ValueError("initial observation cannot declare predecessor")
        if self.observationKind == "revision" and self.previousRawSha256 is None:
            raise ValueError("revision must declare predecessor")
        if self.previousRawSha256 == self.rawSha256:
            raise ValueError("revision must differ from predecessor")
        return self


@dataclass(frozen=True)
class SourceCollectionAssessment:
    content_consistent: bool
    provenance_authenticated: bool
    historical_availability_verified: bool
    inference_authorized: bool
    blockers: tuple[str, ...]


def inspect_source_collection(
    evidence_bytes: bytes,
    raw_page: RawPage,
    *,
    expected_provider: str,
    expected_scope: str,
    expected_run_id: str,
    expected_snapshot_id: str,
    independently_trusted_evidence_sha256: str,
) -> SourceCollectionAssessment:
    """Check raw bytes and declared identity; never grant source or PIT authority."""
    try:
        evidence = SourceCollectionEvidence.model_validate_json(evidence_bytes)
    except (ValueError, UnicodeError):
        return SourceCollectionAssessment(
            False, False, False, False, ("invalid_source_collection_evidence",)
        )

    blockers: list[str] = []
    if (
        len(independently_trusted_evidence_sha256) != 64
        or any(c not in "0123456789abcdef" for c in independently_trusted_evidence_sha256)
        or hashlib.sha256(evidence_bytes).hexdigest() != independently_trusted_evidence_sha256
    ):
        blockers.append("source_collection_evidence_pin_mismatch")
    if hashlib.sha256(raw_page.body).hexdigest() != evidence.rawSha256:
        blockers.append("archived_raw_content_mismatch")
    if (
        evidence.provider != expected_provider
        or evidence.scope != expected_scope
        or evidence.runId != expected_run_id
        or evidence.snapshotId != expected_snapshot_id
    ):
        blockers.append("source_collection_identity_mismatch")
    if evidence.sourceDate != raw_page.source_date or evidence.collectedAt != raw_page.collected_at:
        blockers.append("source_collection_page_metadata_mismatch")
    content_consistent = not blockers
    blockers.extend(
        (
            "collection_evidence_issuer_not_authenticated",
            "independent_historical_availability_not_attested",
            "operational_execution_not_authorized",
        )
    )
    return SourceCollectionAssessment(content_consistent, False, False, False, tuple(blockers))
