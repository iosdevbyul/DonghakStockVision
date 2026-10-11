"""Fail-closed operator assessment for independent custody deployment readiness."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CustodyDeploymentEvidence:
    """Operator assertions are inputs for review, not proof of independent custody."""

    approved_endpoint: bool = False
    independently_administered_provider: bool = False
    authenticated_acknowledgement_verifier: bool = False
    durable_remote_retrieval_tested: bool = False
    idempotent_remote_replay_tested: bool = False


@dataclass(frozen=True)
class CustodyDeploymentReadiness:
    missing_requirements: tuple[str, ...]
    operator_claims_complete: bool
    verified_independent_custody: bool = False
    inference_authorized: bool = False


def assess_custody_deployment_readiness(
    evidence: CustodyDeploymentEvidence,
) -> CustodyDeploymentReadiness:
    """List missing controls without mistaking operator assertions for proof."""
    checks = (
        ("approved_endpoint", evidence.approved_endpoint),
        ("independently_administered_provider", evidence.independently_administered_provider),
        ("authenticated_acknowledgement_verifier", evidence.authenticated_acknowledgement_verifier),
        ("durable_remote_retrieval_tested", evidence.durable_remote_retrieval_tested),
        ("idempotent_remote_replay_tested", evidence.idempotent_remote_replay_tested),
    )
    missing = tuple(name for name, enabled in checks if not enabled)
    return CustodyDeploymentReadiness(
        missing_requirements=missing,
        operator_claims_complete=not missing,
    )
