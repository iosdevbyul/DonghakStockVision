"""A signed deployment receipt does not grant execution permission."""

import base64
import hashlib
import json
from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from donghak_stock_vision.api.deployment_approval import (
    TrustedDeploymentApprover,
    inspect_deployment_approval,
)


def test_signature_verified_but_execution_blocked() -> None:
    proposal = json.dumps({
        "schemaVersion": "operational_model_registration_v1",
        "modelId": "HGB-7", "modelVersion": "v1", "direction": "up",
        "artifactSha256": "a" * 64, "artifactManifestFingerprint": "b" * 64,
        "experimentLockId": "c" * 64, "qualificationId": "d" * 64,
        "trainingSnapshotId": "snapshot", "featureVersion": "features",
        "labelVersion": "labels", "usageRestriction": "operational_review_required",
        "operationalStatus": "proposed", "proposer": "author",
        "proposedAt": "2026-10-10T09:00:00+09:00",
    }).encode()
    registry = b'{"schemaVersion":"operational_qualification_registry_v1","records":[]}'
    receipt = json.dumps({
        "schemaVersion": "operational_deployment_approval_v1",
        "proposalSha256": hashlib.sha256(proposal).hexdigest(),
        "qualificationRegistrySha256": hashlib.sha256(registry).hexdigest(),
        "modelId": "HGB-7", "modelVersion": "v1", "checkpointSha256": "a" * 64,
        "environment": "staging", "purpose": "pit_research_inference",
        "approver": "other-person", "decision": "approved",
        "approvedAt": "2026-10-10T10:00:00+09:00",
        "expiresAt": "2026-10-11T10:00:00+09:00",
    }).encode()
    private = Ed25519PrivateKey.generate()
    signature = private.sign(b"DonghakStockVision:deployment-approval:v1\x00" + receipt)
    envelope = json.dumps({
        "schemaVersion": "signed_deployment_approval_v1",
        "keyId": "trusted-operator",
        "signatureBase64": base64.b64encode(signature).decode(),
    }).encode()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trusted = (TrustedDeploymentApprover("trusted-operator", "other-person", public, True),)

    def check(
        raw: bytes = receipt,
        env: str = "staging",
        when: str = "2026-10-10T12:00:00+09:00",
        keys: tuple[TrustedDeploymentApprover, ...] = trusted,
    ) -> object:
        return inspect_deployment_approval(
            proposal, registry, raw, envelope, keys,
            environment=env, assessed_at=datetime.fromisoformat(when),
        )

    good = inspect_deployment_approval(
        proposal, registry, receipt, envelope, trusted,
        environment="staging",
        assessed_at=datetime.fromisoformat("2026-10-10T12:00:00+09:00"),
    )
    assert good.receipt_authenticated
    assert not good.registration_authorized
    assert not good.inference_authorized
    assert "deployment_environment_mismatch" in inspect_deployment_approval(
        proposal, registry, receipt, envelope, trusted,
        environment="production",
        assessed_at=datetime.fromisoformat("2026-10-10T12:00:00+09:00"),
    ).blockers
    assert "deployment_approval_expired_or_not_yet_valid" in inspect_deployment_approval(
        proposal, registry, receipt, envelope, trusted,
        environment="staging",
        assessed_at=datetime.fromisoformat("2026-10-12T12:00:00+09:00"),
    ).blockers
    revoked = (TrustedDeploymentApprover("trusted-operator", "other-person", public, False),)
    assert "deployment_approver_untrusted_or_revoked" in inspect_deployment_approval(
        proposal, registry, receipt, envelope, revoked,
        environment="staging",
        assessed_at=datetime.fromisoformat("2026-10-10T12:00:00+09:00"),
    ).blockers
    assert "invalid_deployment_approval_signature" in inspect_deployment_approval(
        proposal, registry, receipt + b" ", envelope, trusted,
        environment="staging",
        assessed_at=datetime.fromisoformat("2026-10-10T12:00:00+09:00"),
    ).blockers
