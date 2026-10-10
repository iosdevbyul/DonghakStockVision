"""Composition rejects missing approvals and does not authorize any model."""

from donghak_stock_vision.api.registration_review_gate import review_operational_registration


def test_invalid_registration_is_fail_closed() -> None:
    report = review_operational_registration(
        b"{}",
        b"{}",
        b"{}",
        (),
        b"model",
        b"{}",
        b"{}",
        b"{}",
        trusted_checkpoint_sha256="0" * 64,
        trusted_manifest_sha256="0" * 64,
        trusted_lock_sha256="0" * 64,
        trusted_qualification_sha256="0" * 64,
    )
    assert not report.contract_valid
    assert not report.reviewer_signature_valid
    assert not report.artifact_structurally_consistent
    assert not report.ready_for_independent_qualification
    assert report.registration_authorized is False
    assert "authoritative_operational_qualification_required" in report.blockers


def test_no_review_signature_cannot_be_bypassed() -> None:
    report = review_operational_registration(
        b"not-json",
        b"not-json",
        b"not-json",
        (),
        b"",
        b"",
        b"",
        b"",
        trusted_checkpoint_sha256="x",
        trusted_manifest_sha256="x",
        trusted_lock_sha256="x",
        trusted_qualification_sha256="x",
    )
    assert "independent_review_missing" not in report.blockers
    assert "invalid_independent_review_claim" in report.blockers
    assert report.registration_authorized is False
