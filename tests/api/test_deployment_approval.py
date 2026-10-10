"""Verify deployment approval scope; no grant of execution access."""

import base64
import hashlib
import json
from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from donghak_stock_vision.api.deployment_approval import (
    DeploymentApprovalAssessment,
    TrustedDeploymentApprover,
    inspect_deployment_approval,
)


def test_deployment_approval_is_not_execution_authorization() -> None:
    proposal_fields = {
        "schemaVersion": "operational_model_registration_v1",
        "modelId": "HGB-7",
        "modelVersion": "v1",
        "direction": "up",
        "artifactSha256": "a" * 64,
        "artifactManifestFingerprint": "b" * 64,
        "experimentLockId": "c" * 64,
        "qualificationId": "d" * 64,
        "trainingSnapshotId": "snapshot",
        "featureVersion": "features",
        "labelVersion": "labels",
        "usageRestriction": "operational_review_required",
        "operationalStatus": "proposed",
        "proposer": "author",
        "proposedAt": "2026-10-10T09:00:00+09:00",
    }
    proposal = json.dumps(proposal_fields).encode()
    registry = b'{"schemaVersion":"operational_qualification_registry_v1","records":[]}'
    receipt_fields = {
        "schemaVersion": "operational_deployment_approval_v1",
        "proposalSha256": hashlib.sha256(proposal).hexdigest(),
        "qualificationRegistrySha256": hashlib.sha256(registry).hexdigest(),
        "modelId": "HGB-7",
        "modelVersion": "v1",
        "checkpointSha256": "a" * 64,
        "environment": "staging",
        "purpose": "pit_research_inference",
        "approver": "other-person",
        "decision": "approved",
        "approvedAt": "2026-10-10T10:00:00+09:00",
        "expiresAt": "2026-10-11T10:00:00+09:00",
    }
    receipt = json.dumps(receipt_fields).encode()
    private = Ed25519PrivateKey.generate()
    signature = private.sign(b"DonghakStockVision:deployment-approval:v1\x00" + receipt)
    envelope_fields = {
        "schemaVersion": "signed_deployment_approval_v1",
        "keyId": "operator-key",
        "signatureBase64": base64.b64encode(signature).decode(),
    }
    envelope = json.dumps(envelope_fields).encode()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trusted = TrustedDeploymentApprover("operator-key", "other-person", public, True)

    def assess(
        *,
        environment: str = "staging",
        when: str = "2026-10-10T12:00:00+09:00",
        source: bytes = receipt,
        key: TrustedDeploymentApprover = trusted,
    ) -> DeploymentApprovalAssessment:
        return inspect_deployment_approval(
            proposal,
            registry,
            source,
            envelope,
            (key,),
            environment=environment,
            assessed_at=datetime.fromisoformat(when),
        )

    valid = assess()
    assert valid.receipt_authenticated
    assert valid.registration_authorized is False
    assert valid.inference_authorized is False
    assert "deployment_environment_mismatch" in assess(environment="production").blockers
    assert (
        "deployment_approval_expired_or_not_yet_valid"
        in assess(when="2026-10-12T12:00:00+09:00").blockers
    )
    revoked = TrustedDeploymentApprover("operator-key", "other-person", public, False)
    assert "deployment_approver_untrusted_or_revoked" in assess(key=revoked).blockers
    assert "invalid_deployment_approval_signature" in assess(source=receipt + b" ").blockers
