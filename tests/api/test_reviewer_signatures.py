"""Independent signatures must be verified using keys from an external trust boundary."""

import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from donghak_stock_vision.api.reviewer_signatures import (
    TrustedReviewerKey,
    verify_review_signature,
)


def _inputs() -> tuple[bytes, bytes]:
    proposal = json.dumps(
        {
            "schemaVersion": "operational_model_registration_v1",
            "modelId": "HGB-7",
            "modelVersion": "candidate-v1",
            "direction": "up",
            "artifactSha256": "a" * 64,
            "artifactManifestFingerprint": "b" * 64,
            "experimentLockId": "c" * 64,
            "qualificationId": "d" * 64,
            "trainingSnapshotId": "candidate-snapshot",
            "featureVersion": "ohlcv_value_v1",
            "labelVersion": "reversal_barrier_v1",
            "usageRestriction": "operational_review_required",
            "operationalStatus": "proposed",
            "proposer": "proposer-a",
            "proposedAt": "2026-10-10T10:00:00+09:00",
        }
    ).encode()
    receipt = json.dumps(
        {
            "schemaVersion": "operational_model_review_v1",
            "proposalSha256": hashlib.sha256(proposal).hexdigest(),
            "reviewer": "reviewer-b",
            "decision": "approved",
            "reviewedAt": "2026-10-10T11:00:00+09:00",
        }
    ).encode()
    return proposal, receipt


def test_valid_signature_is_not_operational_registration() -> None:
    proposal, receipt = _inputs()
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    signature = private.sign(b"DonghakStockVision:operational-model-review:v1\x00" + receipt)
    envelope = json.dumps({
        "schemaVersion": "signed_operational_model_review_v1",
        "keyId": "reviewer-key",
        "signatureBase64": base64.b64encode(signature).decode(),
    }).encode()
    trusted = (TrustedReviewerKey("reviewer-key", "reviewer-b", public, True),)
    good = verify_review_signature(proposal, receipt, envelope, trusted)
    assert good.signature_valid
    assert good.registration_authorized is False

    tampered = verify_review_signature(proposal, receipt + b" ", envelope, trusted)
    assert not tampered.signature_valid
    assert "invalid_review_signature" in tampered.blockers

    revoked = verify_review_signature(
        proposal, receipt, envelope,
        (TrustedReviewerKey("reviewer-key", "reviewer-b", public, False),),
    )
    assert not revoked.signature_valid
    assert "reviewer_key_untrusted_or_revoked" in revoked.blockers

    wrong_reviewer = verify_review_signature(
        proposal, receipt, envelope,
        (TrustedReviewerKey("reviewer-key", "someone-else", public, True),),
    )
    assert "reviewer_key_identity_mismatch" in wrong_reviewer.blockers


def test_unsigned_or_unauthorized_review_cannot_pass() -> None:
    proposal, receipt = _inputs()
    envelope = json.dumps({
        "schemaVersion": "signed_operational_model_review_v1",
        "keyId": "unknown",
        "signatureBase64": base64.b64encode(b"x" * 64).decode(),
    }).encode()
    assert "reviewer_key_untrusted_or_revoked" in verify_review_signature(
        proposal, receipt, envelope, ()
    ).blockers
