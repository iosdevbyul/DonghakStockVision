"""Pinned, read-only reviewer public-key registry with explicit revocation state."""

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.api.reviewer_signatures import TrustedReviewerKey


class ReviewerKeyRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    keyId: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    publicKeyBase64: str = Field(min_length=1)
    status: Literal["active", "revoked"]

    @model_validator(mode="after")
    def verify_key(self) -> "ReviewerKeyRecord":
        try:
            raw = base64.b64decode(self.publicKeyBase64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid public key encoding") from error
        if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != self.publicKeyBase64:
            raise ValueError("invalid Ed25519 public key")
        return self


class ReviewerKeyRegistryDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: Literal["trusted_reviewer_keys_v1"]
    keys: tuple[ReviewerKeyRecord, ...]

    @model_validator(mode="after")
    def unique_ids(self) -> "ReviewerKeyRegistryDocument":
        ids = [key.keyId for key in self.keys]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate reviewer key ID")
        return self


class ReviewerRegistryRejected(ValueError):
    """Untrusted, invalid or unexpectedly modified reviewer key registry."""


def load_pinned_reviewer_keys(
    path: Path, *, trusted_sha256: str
) -> tuple[TrustedReviewerKey, ...]:
    """Load only an externally pinned registry file, never from a review envelope."""
    if (
        len(trusted_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in trusted_sha256)
    ):
        raise ReviewerRegistryRejected("independent registry SHA-256 pin required")
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != trusted_sha256:
            raise ReviewerRegistryRejected("reviewer registry fingerprint mismatch")
        document = ReviewerKeyRegistryDocument.model_validate_json(raw)
    except (OSError, ValueError, UnicodeError) as error:
        raise ReviewerRegistryRejected("invalid or untrusted reviewer key registry") from error
    return tuple(
        TrustedReviewerKey(
            key_id=record.keyId,
            reviewer=record.reviewer,
            public_key=base64.b64decode(record.publicKeyBase64, validate=True),
            active=record.status == "active",
        )
        for record in document.keys
    )
