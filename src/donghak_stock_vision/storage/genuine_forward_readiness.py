"""Fail-closed operator decision over the existing offline prospective owners.

A locally verified chain never proves independent source rights, chronology,
custody, research eligibility, performance or execution authorization.
"""

from dataclasses import dataclass
from datetime import datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.prediction_outcome_e2e import RehearsalInputs, rehearse
from donghak_stock_vision.storage.prospective_anchor_governance import (
    AnchorSnapshot,
    review_anchor,
)
from donghak_stock_vision.storage.prospective_custody_readiness import audit_custody

VERSION = "genuine_forward_operational_readiness_v1"
MAX_REPORT_BYTES = 256 * 1024

# These requirements cannot be closed by hashes, fixture clocks or assertions.
EXTERNAL_BLOCKERS = (
    "approved_krx_provider_and_endpoint_rights_not_authenticated",
    "krx_credentials_and_quota_not_assessed",
    "real_market_persistence_and_source_retention_not_verified",
    "independent_policy_predeclaration_not_authenticated",
    "independent_prediction_existence_time_not_authenticated",
    "independent_future_observation_provenance_not_authenticated",
    "third_party_custody_and_clock_not_authenticated",
    "historical_issuer_key_and_revocation_authority_not_authenticated",
    "point_in_time_research_eligibility_not_established",
    "prospective_performance_approval_not_available",
    "trading_execution_authorization_not_available",
)


@dataclass(frozen=True)
class ReadinessInputs:
    rehearsal: RehearsalInputs
    anchor: AnchorSnapshot | None = None
    expected_issuer: str | None = None
    expected_key_id: str | None = None


def assess_readiness(source: ReadinessInputs, as_of: datetime) -> FrozenJSON:
    """Reconstruct original evidence; never trust a supplied completed report."""
    require(type(source) is ReadinessInputs, "readiness_original_owners_required")
    require(
        (source.anchor is None)
        == (source.expected_issuer is None and source.expected_key_id is None),
        "readiness_anchor_identity_required",
    )
    at = timestamp(as_of)
    original = rehearse(source.rehearsal, at)
    original_doc = original.to_dict()
    custody = audit_custody(source.rehearsal.evaluation, at)
    custody_doc = custody.to_dict()
    require(
        custody_doc["preparation_pin"] == original_doc["evaluation_report_pin"]
        and custody_doc["counts"]["independently_authenticated_cases"] == 0,
        "readiness_inconsistent_evidence_replay",
    )
    governance = None
    if source.anchor is not None:
        assert source.expected_issuer is not None
        assert source.expected_key_id is not None
        governance = review_anchor(
            source.anchor.owner,
            source.anchor.pin,
            source.expected_issuer,
            source.expected_key_id,
            at,
        )
    binding = original_doc["binding_status"]
    require(binding in {"pending", "unavailable", "verified"}, "readiness_unsupported_binding")
    future_state = "verified_local" if binding == "verified" else "unavailable"
    capabilities = {
        "original_prediction_journal": {
            "status": "verified_local",
            "authority": "original_immutable_journal_and_capture_replay",
            "reference_pin": original_doc["custody_report_pin"],
        },
        "frozen_strategy_policy_and_horizon": {
            "status": "verified_local",
            "authority": "original_prediction_custody_contract",
            "reference_pin": source.rehearsal.custody.prediction_policy_pin,
        },
        "future_observation_and_binding": {
            "status": future_state,
            "authority": "original_offline_future_observation_resolver",
            "reference_pin": original_doc["binding_pin"],
        },
        "cohort_and_evaluation_structure": {
            "status": "verified_local",
            "authority": "existing_v1_evaluation_consumer",
            "reference_pin": original_doc["evaluation_report_pin"],
        },
        "issuer_key_governance_consistency": {
            "status": "verified_local" if governance is not None else "unavailable",
            "authority": "configured_anchor_not_independent_authority",
            "reference_pin": None if governance is None else governance.identifier,
        },
        "real_krx_source_authorization": {
            "status": "blocked",
            "authority": "provider_scope_and_credentials_not_supplied_to_offline_assessor",
            "reference_pin": None,
        },
        "operational_database_and_market_calendar": {
            "status": "unavailable",
            "authority": "requires_separate_original_operational_store_and_calendar_verification",
            "reference_pin": None,
        },
        "independent_chronology_and_external_custody": {
            "status": "blocked",
            "authority": "no_independent_time_or_custodian_authentication_adapter",
            "reference_pin": None,
        },
        "genuine_forward_performance_and_trading": {
            "status": "blocked",
            "authority": "independent_forward_pit_performance_and_execution_approval_missing",
            "reference_pin": None,
        },
    }
    actions = {
        "phase7_a": {
            "engineering": "allowed_offline",
            "live_collection": "blocked",
            "required": [
                "review_original_krx_grant_endpoint_usage_and_request_quota",
                "provision_credentials_only_through_existing_secure_mechanism",
                "verify_market_calendar_archive_retention_and_operational_store",
                "authorize_a_separate_bounded_live_smoke_test_before_any_request",
            ],
        },
        "phase7_b": {
            "engineering": "allowed_offline",
            "real_data_research": "blocked",
            "required": [
                "verify_original_persisted_krx_bars_and_source_rights",
                "verify_point_in_time_quality_coverage_and_calendar",
                "validate_existing_read_only_api_and_web_integration",
            ],
        },
        "phase7_c": {
            "engineering": "allowed_offline",
            "genuine_forward_evaluation": "blocked",
            "required": [
                "independently_authenticate_original_predeclaration_and_existence",
                "obtain_independent_chronology_custody_and_revocation_history",
                "verify_real_future_market_observation_and_research_eligibility",
                "separately_approve_performance_methodology_before_metrics",
            ],
        },
    }
    missing = list(EXTERNAL_BLOCKERS)
    if governance is None:
        missing.append("trusted_anchor_snapshot_not_supplied")
    else:
        missing.extend(governance.to_dict()["reasons"])
    if binding != "verified":
        missing.append("future_observation_" + binding)
    result = FrozenJSON.freeze(
        {
            "version": VERSION,
            "as_of": at.isoformat(),
            "overall": "blocked",
            "offline_software_readiness": "verified_local",
            "real_market_collection_authorized": False,
            "genuine_forward_evaluation_authorized": False,
            "capabilities": capabilities,
            "references": {
                "prediction_id": original_doc["prediction_id"],
                "journal_head_pin": original_doc["journal_head_pin"],
                "e2e_report_pin": original.identifier,
                "custody_report_pin": custody.identifier,
                "governance_report_pin": None if governance is None else governance.identifier,
            },
            "future_binding_status": binding,
            "missing_evidence_and_blockers": sorted(set(missing)),
            "phase7": actions,
            "allowed_operations": [
                "offline_fixture_rehearsal",
                "read_only_local_evidence_verification",
                "offline_phase7_engineering",
            ],
            "prohibited_operations": [
                "unauthorized_live_krx_requests",
                "external_uploads_or_paid_services",
                "genuine_forward_performance_claims",
                "trading_or_broker_orders",
            ],
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
            "network_requests": 0,
        }
    )
    require(len(result.payload_json.encode()) <= MAX_REPORT_BYTES, "readiness_report_budget")
    require(
        rehearse(source.rehearsal, at) == original
        and audit_custody(source.rehearsal.evaluation, at) == custody,
        "readiness_sources_changed_during_replay",
    )
    return result


def verify_readiness(
    report: FrozenJSON, trusted_pin: str, source: ReadinessInputs, as_of: datetime
) -> None:
    """Reject rehashed or stale summaries by complete original-owner replay."""
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_REPORT_BYTES,
        "readiness_report_budget",
    )
    require(report.identifier == sha(trusted_pin), "readiness_report_pin_mismatch")
    require(report == assess_readiness(source, as_of), "readiness_report_semantic_mismatch")
