"""Composed, non-authorizing operational registration review gate.

Never promotes research models, deserializes checkpoints, or grants execution access.
"""

from dataclasses import dataclass

from donghak_stock_vision.api.operational_artifact_preflight import (
    inspect_operational_artifact,
)
from donghak_stock_vision.api.operational_registry import assess_registration
from donghak_stock_vision.api.reviewer_signatures import (
    TrustedReviewerKey,
    verify_review_signature,
)


@dataclass(frozen=True)
class RegistrationReviewReport:
    contract_valid: bool
    reviewer_signature_valid: bool
    artifact_structurally_consistent: bool
    ready_for_independent_qualification: bool
    registration_authorized: bool
    blockers: tuple[str, ...]


def review_operational_registration(
    proposal_bytes: bytes,
    review_bytes: bytes,
    envelope_bytes: bytes,
    reviewer_keys: tuple[TrustedReviewerKey, ...],
    checkpoint_bytes: bytes,
    manifest_bytes: bytes,
    lock_bytes: bytes,
    qualification_bytes: bytes,
    *,
    trusted_checkpoint_sha256: str,
    trusted_manifest_sha256: str,
    trusted_lock_sha256: str,
    trusted_qualification_sha256: str,
) -> RegistrationReviewReport:
    """Compose existing checks without turning consistency into authorization."""
    contract = assess_registration(proposal_bytes, review_bytes)
    signature = verify_review_signature(
        proposal_bytes, review_bytes, envelope_bytes, reviewer_keys
    )
    artifact = inspect_operational_artifact(
        proposal_bytes,
        checkpoint_bytes,
        manifest_bytes,
        lock_bytes,
        qualification_bytes,
        trusted_checkpoint_sha256=trusted_checkpoint_sha256,
        trusted_manifest_sha256=trusted_manifest_sha256,
        trusted_lock_sha256=trusted_lock_sha256,
        trusted_qualification_sha256=trusted_qualification_sha256,
    )
    structural_exclusions = {
        "authenticated_reviewer_signature_required",
        "operational_artifact_provenance_not_verified",
        "independent_operational_qualification_not_verified",
    }
    blockers = tuple(
        dict.fromkeys(
            (
                *contract.blockers,
                *signature.blockers,
                *artifact.blockers,
                "authoritative_operational_qualification_required",
            )
        )
    )
    ready = (
        contract.structurallyEligible
        and signature.signature_valid
        and artifact.structurally_consistent
        and not (set(blockers) - structural_exclusions - {
            "authoritative_operational_qualification_required"
        })
    )
    return RegistrationReviewReport(
        contract_valid=contract.structurallyEligible,
        reviewer_signature_valid=signature.signature_valid,
        artifact_structurally_consistent=artifact.structurally_consistent,
        ready_for_independent_qualification=ready,
        registration_authorized=False,
        blockers=blockers,
    )
