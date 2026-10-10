"""Registration proposals never promote research artifacts or authorize inference."""

import hashlib
import json

from donghak_stock_vision.api.operational_registry import assess_registration


def proposal() -> bytes:
    return json.dumps(
        {
            "schemaVersion": "operational_model_registration_v1",
            "modelId": "HGB-7",
            "modelVersion": "candidate-v1",
            "direction": "up",
            "artifactSha256": "a" * 64,
            "artifactManifestFingerprint": "b" * 64,
            "experimentLockId": "c" * 64,
            "qualificationId": "d" * 64,
            "trainingSnapshotId": "reviewed-snapshot",
            "featureVersion": "ohlcv_value_v1",
            "labelVersion": "reversal_barrier_v1",
            "usageRestriction": "operational_review_required",
            "operationalStatus": "proposed",
            "proposer": "submitter",
            "proposedAt": "2026-10-10T10:00:00+09:00",
        }
    ).encode()


def claim(source: bytes, *, reviewer: str = "independent", decision: str = "approved") -> bytes:
    return json.dumps(
        {
            "schemaVersion": "operational_model_review_v1",
            "proposalSha256": hashlib.sha256(source).hexdigest(),
            "reviewer": reviewer,
            "decision": decision,
            "reviewedAt": "2026-10-10T11:00:00+09:00",
        }
    ).encode()


def test_missing_review_is_not_authorized() -> None:
    result = assess_registration(proposal())
    assert "independent_review_missing" in result.blockers
    assert not result.registrationAuthorized
    assert not result.structurallyEligible


def test_independent_claim_still_does_not_authorize() -> None:
    source = proposal()
    result = assess_registration(source, claim(source))
    assert result.structurallyEligible
    assert not result.registrationAuthorized
    assert "authenticated_reviewer_signature_required" in result.blockers


def test_self_review_rejected() -> None:
    source = proposal()
    result = assess_registration(source, claim(source, reviewer="submitter"))
    assert "self_review_not_allowed" in result.blockers
    assert not result.structurallyEligible


def test_research_only_model_cannot_be_promoted_by_field_replacement() -> None:
    payload = json.loads(proposal())
    payload["usageRestriction"] = "research_only"
    result = assess_registration(json.dumps(payload).encode())
    assert "invalid_operational_registration_proposal" in result.blockers
    assert not result.registrationAuthorized


def test_changed_proposal_rejects_old_review_hash() -> None:
    source = proposal()
    changed = json.loads(source)
    changed["modelVersion"] = "other-v2"
    result = assess_registration(json.dumps(changed).encode(), claim(source))
    assert "review_proposal_hash_mismatch" in result.blockers
    assert not result.structurallyEligible


def test_rejected_review_stays_blocked() -> None:
    source = proposal()
    result = assess_registration(source, claim(source, decision="rejected"))
    assert "review_not_approved" in result.blockers
    assert not result.registrationAuthorized
