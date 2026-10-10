"""Read-only custody acknowledgement claims; no authentication or delivery authority."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaptureCustodyAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["capture_custody_ack_v1"]
    custodianId: str = Field(min_length=1)
    receiptId: str = Field(min_length=1)
    rawId: int = Field(gt=0)
    acceptedAt: datetime
    receiptSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_time(self) -> "CaptureCustodyAcknowledgement":
        if self.acceptedAt.utcoffset() is None:
            raise ValueError("acceptance time must contain a timezone")
        return self


@dataclass(frozen=True)
class CustodyAcknowledgementReview:
    content_consistent: bool
    custodian_authenticated: bool
    independent_custody_verified: bool
    inference_authorized: bool
    blockers: tuple[str, ...]


def inspect_custody_acknowledgement(
    acknowledgement_bytes: bytes,
    exported_receipt_bytes: bytes,
    *,
    expected_raw_id: int,
    expected_custodian_id: str,
    independently_pinned_ack_sha256: str,
) -> CustodyAcknowledgementReview:
    """Compare explicit externally supplied claims; never promote local hashes to authority."""
    try:
        acknowledgement = CaptureCustodyAcknowledgement.model_validate_json(
            acknowledgement_bytes
        )
    except (ValueError, UnicodeError):
        return CustodyAcknowledgementReview(
            False, False, False, False, ("invalid_custody_acknowledgement",)
        )
    blockers: list[str] = []
    if (
        len(independently_pinned_ack_sha256) != 64
        or any(character not in "0123456789abcdef" for character in independently_pinned_ack_sha256)
        or hashlib.sha256(acknowledgement_bytes).hexdigest() != independently_pinned_ack_sha256
    ):
        blockers.append("custody_ack_independent_pin_mismatch")
    if acknowledgement.rawId != expected_raw_id or acknowledgement.custodianId != expected_custodian_id:
        blockers.append("custody_ack_identity_mismatch")
    if hashlib.sha256(exported_receipt_bytes).hexdigest() != acknowledgement.receiptSha256:
        blockers.append("custody_ack_receipt_digest_mismatch")
    consistent = not blockers
    blockers.extend(
        (
            "custodian_identity_not_cryptographically_authenticated",
            "independent_custody_not_verified",
            "inference_not_authorized",
        )
    )
    return CustodyAcknowledgementReview(consistent, False, False, False, tuple(blockers))
