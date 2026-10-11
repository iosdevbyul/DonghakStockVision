"""Deployment readiness claims must never grant independent custody authority."""

from dataclasses import fields

from donghak_stock_vision.storage.capture_custody_readiness import (
    CustodyDeploymentEvidence,
    assess_custody_deployment_readiness,
)


def test_default_report_fails_closed_with_all_requirements_missing() -> None:
    report = assess_custody_deployment_readiness(CustodyDeploymentEvidence())
    assert report.missing_requirements == tuple(
        field.name for field in fields(CustodyDeploymentEvidence)
    )
    assert not report.operator_claims_complete
    assert not report.verified_independent_custody
    assert not report.inference_authorized


def test_partial_report_identifies_exact_missing_controls() -> None:
    report = assess_custody_deployment_readiness(
        CustodyDeploymentEvidence(
            approved_endpoint=True,
            independently_administered_provider=True,
        )
    )
    assert report.missing_requirements == (
        "authenticated_acknowledgement_verifier",
        "durable_remote_retrieval_tested",
        "idempotent_remote_replay_tested",
    )
    assert not report.operator_claims_complete


def test_even_all_operator_claims_cannot_grant_verified_custody() -> None:
    report = assess_custody_deployment_readiness(
        CustodyDeploymentEvidence(
            approved_endpoint=True,
            independently_administered_provider=True,
            authenticated_acknowledgement_verifier=True,
            durable_remote_retrieval_tested=True,
            idempotent_remote_replay_tested=True,
        )
    )
    assert report.missing_requirements == ()
    assert report.operator_claims_complete
    assert not report.verified_independent_custody
    assert not report.inference_authorized
