"""Qualification issuer signatures authenticate bytes, not operational permission."""

import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from donghak_stock_vision.api.qualification_issuer import (
    TrustedQualificationIssuer,
    verify_qualification_issuer,
)


def _signed() -> tuple[bytes, bytes, TrustedQualificationIssuer]:
    registry = b'{"schemaVersion":"operational_qualification_registry_v1","records":[]}'
    secret = Ed25519PrivateKey.generate()
    public = secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    signature = secret.sign(b"DonghakStockVision:qualification-registry:v1\x00" + registry)
    envelope = json.dumps(
        {
            "schemaVersion": "signed_qualification_registry_v1",
            "issuerKeyId": "issuer-1",
            "registrySha256": hashlib.sha256(registry).hexdigest(),
            "signatureBase64": base64.b64encode(signature).decode("ascii"),
        }
    ).encode()
    return registry, envelope, TrustedQualificationIssuer("issuer-1", public, True)


def test_valid_signed_registry_is_not_authorization() -> None:
    registry, envelope, issuer = _signed()
    result = verify_qualification_issuer(registry, envelope, (issuer,))
    assert result.issuer_authenticated
    assert not result.registration_authorized


def test_modified_bytes_are_rejected() -> None:
    registry, envelope, issuer = _signed()
    result = verify_qualification_issuer(registry + b" ", envelope, (issuer,))
    assert "qualification_registry_digest_mismatch" in result.blockers
    assert not result.issuer_authenticated


def test_untrusted_and_revoked_keys_are_rejected() -> None:
    registry, envelope, issuer = _signed()
    assert "qualification_issuer_untrusted_or_revoked" in verify_qualification_issuer(
        registry, envelope, ()
    ).blockers
    revoked = TrustedQualificationIssuer(issuer.key_id, issuer.public_key, False)
    assert not verify_qualification_issuer(registry, envelope, (revoked,)).issuer_authenticated


def test_bad_signature_is_rejected() -> None:
    registry, envelope, issuer = _signed()
    payload = json.loads(envelope)
    payload["signatureBase64"] = base64.b64encode(b"x" * 64).decode()
    bad = verify_qualification_issuer(registry, json.dumps(payload).encode(), (issuer,))
    assert "invalid_qualification_issuer_signature" in bad.blockers
