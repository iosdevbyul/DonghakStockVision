"""Offline source-revision ledger inspection; never attests ledger authenticity."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.api.pit_inference_input import PITInferenceInput


class SourceRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    observedAt: datetime
    sourceSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previousSha256: str | None

    @model_validator(mode="after")
    def validate_timestamp_and_link(self) -> "SourceRevision":
        if self.observedAt.utcoffset() is None:
            raise ValueError("revision timestamp must be timezone-aware")
        if self.previousSha256 is not None and (
            len(self.previousSha256) != 64
            or any(char not in "0123456789abcdef" for char in self.previousSha256)
        ):
            raise ValueError("invalid revision predecessor")
        return self


class SourceRevisionLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["pit_source_revision_ledger_v1"]
    snapshotId: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    revisions: tuple[SourceRevision, ...]


@dataclass(frozen=True)
class RevisionLedgerAssessment:
    chain_consistent: bool
    historical_availability_verified: bool
    inference_authorized: bool
    blockers: tuple[str, ...]


def inspect_revision_ledger(
    candidate_bytes: bytes,
    ledger_bytes: bytes,
    *,
    independently_trusted_ledger_sha256: str,
) -> RevisionLedgerAssessment:
    """Detect broken chronology and digest links without granting PIT eligibility."""
    try:
        candidate = PITInferenceInput.model_validate_json(candidate_bytes)
        ledger = SourceRevisionLedger.model_validate_json(ledger_bytes)
    except (ValueError, UnicodeError):
        return RevisionLedgerAssessment(False, False, False, ("invalid_revision_ledger",))

    blockers: list[str] = []
    if (
        len(independently_trusted_ledger_sha256) != 64
        or any(c not in "0123456789abcdef" for c in independently_trusted_ledger_sha256)
        or hashlib.sha256(ledger_bytes).hexdigest() != independently_trusted_ledger_sha256
    ):
        blockers.append("revision_ledger_pin_mismatch")
    if ledger.snapshotId != candidate.snapshotId or ledger.ticker != candidate.ticker:
        blockers.append("revision_ledger_identity_mismatch")
    if not ledger.revisions:
        blockers.append("revision_history_missing")
    else:
        for index, revision in enumerate(ledger.revisions):
            if revision.sequence != index + 1:
                blockers.append("revision_sequence_invalid")
            if index == 0:
                if revision.previousSha256 is not None:
                    blockers.append("revision_genesis_invalid")
            else:
                previous = ledger.revisions[index - 1]
                if revision.previousSha256 != previous.sourceSha256:
                    blockers.append("revision_link_invalid")
                if revision.observedAt <= previous.observedAt:
                    blockers.append("revision_chronology_invalid")
        if ledger.revisions[-1].observedAt > candidate.sourceCutoff:
            blockers.append("revision_observed_after_cutoff")
    chain_consistent = not blockers
    blockers.extend(
        (
            "revision_ledger_issuer_not_authenticated",
            "independent_historical_availability_not_attested",
            "operational_execution_not_authorized",
        )
    )
    return RevisionLedgerAssessment(
        chain_consistent,
        False,
        False,
        tuple(dict.fromkeys(blockers)),
    )
