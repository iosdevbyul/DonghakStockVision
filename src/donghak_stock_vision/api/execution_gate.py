"""Fail-closed integration of reviewed evidence before any operational model execution.

Diagnostic only: does not grant capabilities, register models, or run inference.
"""

from dataclasses import dataclass

from donghak_stock_vision.api.deployment_approval import DeploymentApprovalAssessment
from donghak_stock_vision.api.qualification_issuer import IssuerVerification
from donghak_stock_vision.api.qualification_registry import QualificationAssessment
from donghak_stock_vision.api.registration_review_gate import RegistrationReviewReport


@dataclass(frozen=True)
class ExecutionGateAssessment:
    evidence_consistent: bool
    registration_authorized: bool
    inference_authorized: bool
    blockers: tuple[str, ...]


def inspect_execution_gate(
    review: RegistrationReviewReport,
    qualification: QualificationAssessment,
    issuer: IssuerVerification,
    deployment: DeploymentApprovalAssessment,
) -> ExecutionGateAssessment:
    """Aggregate independent results without treating advisory flags as permissions."""
    blockers: list[str] = []
    if not (
        review.contract_valid
        and review.reviewer_signature_valid
        and review.artifact_structurally_consistent
        and review.ready_for_independent_qualification
    ):
        blockers.append("registration_review_failed")
    if not qualification.qualification_record_valid:
        blockers.append("qualification_record_invalid")
    if not issuer.issuer_authenticated:
        blockers.append("qualification_issuer_not_authenticated")
    if not deployment.receipt_authenticated:
        blockers.append("deployment_approval_not_authenticated")
    for evidence in (review, qualification, issuer, deployment):
        blockers.extend(
            blocker
            for blocker in evidence.blockers
            if blocker
            not in {
                "authenticated_reviewer_signature_required",
                "operational_artifact_provenance_not_verified",
                "independent_operational_qualification_not_verified",
                "authoritative_operational_qualification_required",
                "authoritative_registry_identity_not_authenticated",
            }
        )
    evidence_consistent = not blockers
    # Revocation freshness and authoritative operational qualification are not
    # established by the read-only contracts, even when signatures are valid.
    blockers.extend(
        (
            "authoritative_operational_permission_service_missing",
            "current_revocation_state_not_attested",
        )
    )
    return ExecutionGateAssessment(
        evidence_consistent=evidence_consistent,
        registration_authorized=False,
        inference_authorized=False,
        blockers=tuple(dict.fromkeys(blockers)),
    )
