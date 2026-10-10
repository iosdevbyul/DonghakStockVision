"""The composed evidence report must never open the model execution boundary."""

from donghak_stock_vision.api.deployment_approval import DeploymentApprovalAssessment
from donghak_stock_vision.api.execution_gate import inspect_execution_gate
from donghak_stock_vision.api.qualification_issuer import IssuerVerification
from donghak_stock_vision.api.qualification_registry import QualificationAssessment
from donghak_stock_vision.api.registration_review_gate import RegistrationReviewReport


def test_consistent_evidence_is_still_not_execution_authorization() -> None:
    report = inspect_execution_gate(
        RegistrationReviewReport(True, True, True, True, False, ()),
        QualificationAssessment(True, True, False, ()),
        IssuerVerification(True, False, ()),
        DeploymentApprovalAssessment(True, False, False, ()),
    )
    assert report.evidence_consistent
    assert report.registration_authorized is False
    assert report.inference_authorized is False
    assert "authoritative_operational_permission_service_missing" in report.blockers


def test_missing_and_revoked_evidence_fail_closed() -> None:
    report = inspect_execution_gate(
        RegistrationReviewReport(False, False, False, False, False, ("review_rejected",)),
        QualificationAssessment(False, False, False, ("qualification_revoked",)),
        IssuerVerification(False, False, ("qualification_issuer_untrusted_or_revoked",)),
        DeploymentApprovalAssessment(False, False, False, ("deployment_not_approved",)),
    )
    assert not report.evidence_consistent
    assert not report.registration_authorized
    assert not report.inference_authorized
    assert "qualification_revoked" in report.blockers
    assert "deployment_not_approved" in report.blockers
