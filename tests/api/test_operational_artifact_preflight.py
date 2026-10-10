"""Artifact preflight may confirm byte consistency but never permits inference."""

import hashlib
import json

from donghak_stock_vision.api.operational_artifact_preflight import (
    ArtifactProvenanceAssessment,
    inspect_operational_artifact,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inputs(research_only: bool = False) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    checkpoint = b"opaque-trusted-test-checkpoint"
    state = "unregistered" if research_only else "proposed"
    restriction = "research_only" if research_only else "operational_review_required"
    manifest = {
        "fingerprint": "a" * 64,
        "direction": "up",
        "checkpoint_sha256": _sha(checkpoint),
        "experiment_lock_id": "b" * 64,
        "qualification_id": "c" * 64,
        "source_snapshot_id": "snapshot",
        "feature_version": "ohlcv_value_v1",
        "label_version": "reversal_barrier_v1",
    }
    lock = {
        "lock_id": "b" * 64,
        "training_snapshot_id": "snapshot",
        "feature_version": "ohlcv_value_v1",
        "label_version": "reversal_barrier_v1",
        "operational_status": state,
        "usage_restriction": restriction,
    }
    qualification = {
        "report_id": "c" * 64,
        "lock_id": "b" * 64,
        "operational_status": state,
        "usage_restriction": restriction,
    }
    proposal = {
        "schemaVersion": "operational_model_registration_v1",
        "modelId": "HGB-7",
        "modelVersion": "candidate",
        "direction": "up",
        "artifactSha256": _sha(checkpoint),
        "artifactManifestFingerprint": "a" * 64,
        "experimentLockId": "b" * 64,
        "qualificationId": "c" * 64,
        "trainingSnapshotId": "snapshot",
        "featureVersion": "ohlcv_value_v1",
        "labelVersion": "reversal_barrier_v1",
        "usageRestriction": "operational_review_required",
        "operationalStatus": "proposed",
        "proposer": "author",
        "proposedAt": "2026-10-10T10:00:00+09:00",
    }
    return (
        json.dumps(proposal).encode(),
        checkpoint,
        json.dumps(manifest).encode(),
        json.dumps(lock).encode(),
        json.dumps(qualification).encode(),
    )


def _check(inputs: tuple[bytes, bytes, bytes, bytes, bytes]) -> ArtifactProvenanceAssessment:
    proposal, checkpoint, manifest, lock, qualification = inputs
    return inspect_operational_artifact(
        proposal,
        checkpoint,
        manifest,
        lock,
        qualification,
        trusted_checkpoint_sha256=_sha(checkpoint),
        trusted_manifest_sha256=_sha(manifest),
        trusted_lock_sha256=_sha(lock),
        trusted_qualification_sha256=_sha(qualification),
    )


def test_consistent_artifact_does_not_authorize_registration() -> None:
    result = _check(_inputs())
    assert result.structurally_consistent
    assert result.registration_authorized is False
    assert "independent_operational_qualification_not_verified" in result.blockers


def test_research_only_checkpoint_cannot_be_promoted() -> None:
    result = _check(_inputs(research_only=True))
    assert not result.structurally_consistent
    assert "research_only_or_unapproved_artifact" in result.blockers


def test_tampered_checkpoint_is_rejected() -> None:
    values = list(_inputs())
    values[1] += b"tampered"
    assert "checkpoint_proposal_mismatch" in _check(tuple(values)).blockers


def test_changed_qualification_link_is_rejected() -> None:
    values = list(_inputs())
    qualification = json.loads(values[4])
    qualification["lock_id"] = "d" * 64
    values[4] = json.dumps(qualification).encode()
    assert "experiment_qualification_link_mismatch" in _check(tuple(values)).blockers
