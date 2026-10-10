"""Detached Ed25519 verification of operational qualification registry issuer.

Signature validity never grants model registration or inference access.
"""

import base64
import binascii
import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from donghak_stock_vision.api.qualification_registry import QualificationRegistryDocument


class SignedQualificationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: str = Field(pattern=r"^signed_qualification_registry_v1$")
    issuerKeyId: str = Field(min_length=1)
    registrySha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signatureBase64: str = Field(min_length=1)


@dataclass(frozen=True)
class TrustedQualificationIssuer:
    key_id: str
    public_key: bytes
    active: bool

    def __post_init__(self) -> None:
        if not self.key_id or len(self.public_key) != 32:
            raise ValueError("invalid trusted issuer key")


@dataclass(frozen=True)
class IssuerVerification:
    issuer_authenticated: bool
    registration_authorized: bool
    blockers: tuple[str, ...]


def verify_qualification_issuer(
    registry_bytes: bytes,
    envelope_bytes: bytes,
    trusted_issuers: tuple[TrustedQualificationIssuer, ...],
) -> IssuerVerification:
    """Trust anchors are injected independently, never parsed from a signed envelope."""
    try:
        QualificationRegistryDocument.model_validate_json(registry_bytes)
        envelope = SignedQualificationEnvelope.model_validate_json(envelope_bytes)
    except (ValueError, UnicodeError):
        return IssuerVerification(False, False, ("invalid_qualification_registry_or_envelope",))

    blockers: list[str] = []
    sha = hashlib.sha256(registry_bytes).hexdigest()
    if envelope.registrySha256 != sha:
        blockers.append("qualification_registry_digest_mismatch")
    matches = [item for item in trusted_issuers if item.key_id == envelope.issuerKeyId]
    if len(matches) != 1 or not matches[0].active:
        blockers.append("qualification_issuer_untrusted_or_revoked")
    else:
        try:
            signature = base64.b64decode(envelope.signatureBase64, validate=True)
            if len(signature) != 64:
                raise ValueError("incorrect signature length")
            message = b"DonghakStockVision:qualification-registry:v1\x00" + registry_bytes
            Ed25519PublicKey.from_public_bytes(matches[0].public_key).verify(
                signature, message
            )
        except (InvalidSignature, ValueError, binascii.Error):
            blockers.append("invalid_qualification_issuer_signature")
    return IssuerVerification(
        issuer_authenticated=not blockers,
        registration_authorized=False,
        blockers=tuple(blockers),
    )
