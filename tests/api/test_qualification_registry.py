"""A structurally valid qualification still cannot grant operational permission."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from donghak_stock_vision.api.qualification_registry import inspect_qualification
from donghak_stock_vision.api.registration_review_gate import RegistrationReviewReport


def _inputs(tmp_path: Path, *, status: str = "qualified") -> tuple[bytes, Path, str]:
    proposal = {
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
        "proposer": "submitter",
        "proposedAt": "2026-10-10T10:00:00+09:00",
    }
    raw = json.dumps(proposal).encode()
    registry = tmp_path / "qualifications.json"
    registry.write_text(
        json.dumps({
            "schemaVersion": "operational_qualification_registry_v1",
            "records": [{
                "proposalSha256": hashlib.sha256(raw).hexdigest(),
                "modelId": "HGB-7",
                "modelVersion": "v1",
                "artifactSha256": "a" * 64,
                "qualificationId": "d" * 64,
                "status": status,
                "validFrom": "2026-10-10T00:00:00+09:00",
                "validUntil": "2026-10-12T00:00:00+09:00",
            }],
        }),
        encoding="utf-8",
    )
    return raw, registry, hashlib.sha256(registry.read_bytes()).hexdigest()


def _review() -> RegistrationReviewReport:
    return RegistrationReviewReport(True, True, True, True, False, ())


def _at() -> datetime:
    return datetime.fromisoformat("2026-10-10T12:00:00+09:00")


def test_matching_record_never_authorizes_registration(tmp_path: Path) -> None:
    proposal, registry, pin = _inputs(tmp_path)
    result = inspect_qualification(
        proposal, _review(), registry,
        independently_trusted_registry_sha256=pin, assessed_at=_at(),
    )
    assert result.qualification_record_valid
    assert result.registration_authorized is False
    assert "authoritative_registry_identity_not_authenticated" in result.blockers


def test_revoked_qualification_is_rejected(tmp_path: Path) -> None:
    proposal, registry, pin = _inputs(tmp_path, status="revoked")
    result = inspect_qualification(
        proposal, _review(), registry,
        independently_trusted_registry_sha256=pin, assessed_at=_at(),
    )
    assert not result.qualification_record_valid
    assert "qualification_revoked" in result.blockers


def test_tampering_and_missing_review_fail_closed(tmp_path: Path) -> None:
    proposal, registry, pin = _inputs(tmp_path)
    registry.write_text(registry.read_text() + " ", encoding="utf-8")
    result = inspect_qualification(
        proposal, _review(), registry,
        independently_trusted_registry_sha256=pin, assessed_at=_at(),
    )
    assert "qualification_registry_invalid_or_untrusted" in result.blockers
    result = inspect_qualification(
        proposal, RegistrationReviewReport(False, False, False, False, False, ()),
        registry, independently_trusted_registry_sha256=pin, assessed_at=_at(),
    )
    assert "registration_review_not_ready" in result.blockers
