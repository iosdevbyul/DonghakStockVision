"""Offline provenance preflight; never loads checkpoints or authorizes registration."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from donghak_stock_vision.api.operational_registry import OperationalRegistrationProposal


@dataclass(frozen=True)
class ArtifactProvenanceAssessment:
    structurally_consistent: bool
    blockers: tuple[str, ...]
    registration_authorized: bool = False


def inspect_operational_artifact(
    proposal_bytes: bytes,
    checkpoint_bytes: bytes,
    manifest_bytes: bytes,
    lock_bytes: bytes,
    qualification_bytes: bytes,
    *,
    trusted_checkpoint_sha256: str,
    trusted_manifest_sha256: str,
    trusted_lock_sha256: str,
    trusted_qualification_sha256: str,
) -> ArtifactProvenanceAssessment:
    """All trusted hashes MUST be obtained from an independent approval authority."""
    blockers: list[str] = []
    try:
        proposal = OperationalRegistrationProposal.model_validate_json(proposal_bytes)
    except (ValueError, UnicodeError):
        return ArtifactProvenanceAssessment(False, ("invalid_registration_proposal",))

    artifacts = (
        ("checkpoint", checkpoint_bytes, trusted_checkpoint_sha256),
        ("manifest", manifest_bytes, trusted_manifest_sha256),
        ("lock", lock_bytes, trusted_lock_sha256),
        ("qualification", qualification_bytes, trusted_qualification_sha256),
    )
    for name, payload, trusted in artifacts:
        if (
            len(trusted) != 64
            or any(char not in "0123456789abcdef" for char in trusted)
            or hashlib.sha256(payload).hexdigest() != trusted
        ):
            blockers.append(f"{name}_independent_pin_mismatch")

    if hashlib.sha256(checkpoint_bytes).hexdigest() != proposal.artifactSha256:
        blockers.append("checkpoint_proposal_mismatch")

    try:
        manifest: Any = json.loads(manifest_bytes)
        lock: Any = json.loads(lock_bytes)
        qualification: Any = json.loads(qualification_bytes)
        if not all(isinstance(value, dict) for value in (manifest, lock, qualification)):
            raise ValueError("invalid provenance objects")
        if manifest.get("fingerprint") != proposal.artifactManifestFingerprint:
            blockers.append("manifest_fingerprint_mismatch")
        expected = {
            "direction": proposal.direction,
            "checkpoint_sha256": proposal.artifactSha256,
            "experiment_lock_id": proposal.experimentLockId,
            "qualification_id": proposal.qualificationId,
            "source_snapshot_id": proposal.trainingSnapshotId,
            "feature_version": proposal.featureVersion,
            "label_version": proposal.labelVersion,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            blockers.append("manifest_provenance_mismatch")
        if (
            lock.get("lock_id") != proposal.experimentLockId
            or lock.get("training_snapshot_id") != proposal.trainingSnapshotId
            or lock.get("feature_version") != proposal.featureVersion
            or lock.get("label_version") != proposal.labelVersion
            or qualification.get("report_id") != proposal.qualificationId
            or qualification.get("lock_id") != proposal.experimentLockId
        ):
            blockers.append("experiment_qualification_link_mismatch")
        if any(
            obj.get("usage_restriction") != "operational_review_required"
            or obj.get("operational_status") != "proposed"
            for obj in (lock, qualification)
        ):
            blockers.append("research_only_or_unapproved_artifact")
    except (ValueError, TypeError, AttributeError):
        blockers.append("invalid_provenance_json")

    # This is structural consistency only. No pickle/unpickling, no registration writes.
    blockers.append("independent_operational_qualification_not_verified")
    return ArtifactProvenanceAssessment(
        structurally_consistent=not (
            set(blockers) - {"independent_operational_qualification_not_verified"}
        ),
        blockers=tuple(dict.fromkeys(blockers)),
    )
