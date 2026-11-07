"""Bounded cohort composition of unchanged single-case receipt reconciliation."""

from datetime import datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, utc
from donghak_stock_vision.storage.prediction_binding import inspect_prediction
from donghak_stock_vision.storage.prospective_custody_readiness import audit_custody
from donghak_stock_vision.storage.prospective_evaluation_consumer import EvaluationSources
from donghak_stock_vision.storage.prospective_evaluation_policy import validate_policy
from donghak_stock_vision.storage.prospective_evaluation_result import MAX_BYTES
from donghak_stock_vision.storage.prospective_evidence_audit import (
    MAX_CASES,
    MAX_INPUT_BYTES,
    MAX_REQUEST_BYTES,
    EvidenceCase,
)
from donghak_stock_vision.storage.prospective_external_receipt import (
    MAX_ARTIFACT,
    MAX_DOCUMENT,
    ReceiptOwner,
)
from donghak_stock_vision.storage.prospective_receipt_reconciliation import _reconcile_verified_pair


def reconcile_cohort(
    source: EvaluationSources,
    policy_receipt: ReceiptOwner,
    prediction_receipts: dict[str, ReceiptOwner],
    expectations: FrozenJSON,
    expectations_pin: str,
    anchor: FrozenJSON | None,
    anchor_pin: str | None,
    as_of: datetime,
) -> FrozenJSON:
    """Replay all declared cases; missing bodies never become fabricated receipts."""
    require(type(source) is EvaluationSources, "cohort_original_sources_required")
    validate_policy(source.policy, source.policy_pin)
    ids = source.policy.to_dict()["expected_prediction_ids"]
    require(
        type(source.cases) is tuple
        and 1 <= len(source.cases) <= MAX_CASES
        and len(ids) <= MAX_CASES,
        "cohort_case_budget",
    )
    request_bytes = 0
    for case in source.cases:
        require(type(case) is EvidenceCase, "cohort_original_case_required")
        requests = (case.prediction.request,) + (
            () if case.future is None else (case.future.request,)
        )
        for request in requests:
            require(type(request) is FrozenJSON, "cohort_original_request_required")
            size = len(request.payload_json.encode())
            require(size <= MAX_REQUEST_BYTES, "cohort_request_byte_budget")
            request_bytes += size
        require(request_bytes <= MAX_INPUT_BYTES, "cohort_original_input_budget")
    case_ids = [c.prediction.prediction_id for c in source.cases]
    require(len(set(case_ids)) == len(case_ids), "cohort_duplicate_prediction")
    require(sorted(case_ids) == ids, "cohort_declared_membership_mismatch")
    require(
        type(expectations) is FrozenJSON
        and len(expectations.payload_json.encode()) <= MAX_DOCUMENT,
        "cohort_expectation_budget",
    )
    require(expectations.identifier == sha(expectations_pin), "cohort_expectation_pin_mismatch")
    e = fields(expectations.to_dict(), "version prediction_receipt_ids")
    require(
        e["version"] == "prospective_receipt_cohort_expectations_v1", "cohort_expectation_version"
    )
    expected = e["prediction_receipt_ids"]
    require(
        type(expected) is dict and set(expected) == set(ids), "cohort_expected_receipt_membership"
    )
    for identity in expected.values():
        sha(identity)
    require(
        type(policy_receipt) is ReceiptOwner and policy_receipt.role == "policy",
        "cohort_policy_owner_required",
    )
    sha(policy_receipt.receipt_id)
    require(
        len(set(expected.values()) | {policy_receipt.receipt_id}) == len(ids) + 1,
        "cohort_duplicate_receipt_identity",
    )
    require(
        type(prediction_receipts) is dict and set(prediction_receipts) <= set(ids),
        "cohort_undeclared_receipt",
    )
    supplied = dict(prediction_receipts)
    for identity, owner in supplied.items():
        require(
            type(owner) is ReceiptOwner and owner.role == "prediction",
            "cohort_prediction_owner_required",
        )
        require(owner.receipt_id == expected[identity], "cohort_expected_receipt_identity_mismatch")
    # Preflight all receipt inputs before any replay; shared policy is checked once.
    for owner in (policy_receipt, *supplied.values()):
        require(
            type(owner.artifact) is FrozenJSON
            and len(owner.artifact.payload_json.encode()) <= MAX_ARTIFACT,
            "cohort_artifact_budget",
        )
        sha(owner.artifact_pin)
        if owner.receipt_pin is not None:
            sha(owner.receipt_pin)
        if owner.receipt is not None:
            require(
                type(owner.receipt) is FrozenJSON
                and len(owner.receipt.payload_json.encode()) <= MAX_DOCUMENT,
                "cohort_receipt_budget",
            )
    if anchor is not None:
        require(
            type(anchor) is FrozenJSON and len(anchor.payload_json.encode()) <= MAX_DOCUMENT,
            "cohort_anchor_budget",
        )
    if anchor_pin is not None:
        sha(anchor_pin)
    cases = {c.prediction.prediction_id: c for c in source.cases}
    # Fresh authoritative boundary: never accept a caller-provided verified context.
    initial_custody = audit_custody(source, as_of)
    # Detached immutable owners, local to this invocation and never returned as a cache.
    originals = tuple(
        (
            identity,
            FrozenJSON.freeze(
                inspect_prediction(cases[identity].prediction).to_dict()["prediction"]
            ),
        )
        for identity in ids
    )
    rows = []
    context = None
    context_pin = references_pin = None
    counts = {
        "total_declared_predictions": len(ids),
        "supplied_receipt_references": 0,
        "missing_receipt_references": 0,
        "prediction_signatures_verified": 0,
        "policy_prediction_pairs_verified": 0,
        "receipt_verification_failed": 0,
        "invalid_reconciliations": 0,
        "not_cryptographically_verified": 0,
        "local_future_pending": 0,
        "local_future_unavailable": 0,
        "local_future_invalid": 0,
    }
    retained_bytes = 0
    for identity, prediction in originals:
        case_owner = supplied.get(identity)
        present = case_owner is not None and (
            case_owner.receipt is not None or case_owner.receipt_pin is not None
        )
        if case_owner is None:
            # Pinned expected ID is supplied by the operator, not invented here.
            # None is absence, never an issued receipt or signed assertion.
            case_owner = ReceiptOwner(
                "prediction", prediction, prediction.identifier, expected[identity], None, None
            )
        single = _reconcile_verified_pair(
            source,
            identity,
            (policy_receipt, case_owner),
            anchor,
            anchor_pin,
            as_of,
            custody=initial_custody,
            prediction=prediction,
            entry_pin=cases[identity].prediction.entry_pin,
        )
        d = single.to_dict()
        if context is None:
            context = d["custody_report"]
            context_pin, references_pin = d["custody_report_pin"], d["original_references_pin"]
            retained_bytes += len(FrozenJSON.freeze(context).payload_json.encode())
        require(
            d["custody_report_pin"] == context_pin
            and d["original_references_pin"] == references_pin,
            "cohort_source_changed_during_replay",
        )
        counts["supplied_receipt_references" if present else "missing_receipt_references"] += 1
        findings = d["findings"]
        counts["policy_prediction_pairs_verified"] += int(
            findings["selected_receipt_signatures_verified"]
        )
        receipts = d["receipt_verification"]["receipts"]
        prediction_result = next(r for r in receipts if r["role"] == "prediction")
        counts["prediction_signatures_verified"] += int(prediction_result["signature_verified"])
        counts["receipt_verification_failed"] += int(
            d["state"] == "invalid"
            or (present and not findings["selected_receipt_signatures_verified"])
        )
        counts["invalid_reconciliations"] += int(d["state"] == "invalid")
        local = next(r for r in d["custody_report"]["cases"] if r["prediction_id"] == identity)
        if local["local_future_state"] != "verified":
            counts["local_future_" + local["local_future_state"]] += 1
        row = {
            "prediction_id": identity,
            "expected_receipt_id": expected[identity],
            "receipt_reference_supplied": present,
            "single_case_report_pin": single.identifier,
            "state": d["state"],
            "reason": d["reason"],
            "findings": findings,
            "local_future_state": local["local_future_state"],
            "local_future_reason": local["local_future_reason"],
            "receipt_verification": d["receipt_verification"],
            "original_prediction_artifact_pin": d["original_prediction_artifact_pin"],
        }
        retained_bytes += len(FrozenJSON.freeze(row).payload_json.encode())
        require(retained_bytes <= MAX_BYTES, "cohort_output_budget")
        rows.append(row)
    verified = counts["policy_prediction_pairs_verified"]
    counts["not_cryptographically_verified"] = len(ids) - verified
    report = FrozenJSON.freeze(
        {
            "version": "prospective_receipt_cohort_reconciliation_v1",
            "as_of": utc(as_of).isoformat(),
            "state": "invalid" if counts["invalid_reconciliations"] else "unresolved",
            "scope": "synthetic_offline_cryptographic_reconciliation_only",
            "expectations_pin": expectations_pin,
            "shared_policy_receipt_id": policy_receipt.receipt_id,
            "shared_policy_receipt_pin": policy_receipt.receipt_pin,
            "anchor_pin": anchor_pin,
            "original_references_pin": references_pin,
            "custody_report": context,
            "counts": counts,
            "cases": rows,
            "cryptographic_coverage_diagnostic": {
                "numerator": verified,
                "denominator": len(ids),
                "all_declared_pairs_have_valid_signatures": verified == len(ids),
            },
            "work_bounds": {
                "single_case_invocations": len(ids),
                "maximum_ed25519_checks": 2 * len(ids),
                "maximum_full_cohort_case_traversals_per_replay_layer": 2 * len(ids),
            },
            "issuer_independence_verified": False,
            "independently_authenticated_chronology": False,
            "historical_trust_anchor_validity_verified": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "prospective_performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_BYTES, "cohort_output_budget")
    # A second complete authoritative replay detects persistent mid-operation
    # changes, including alterations during crypto/serialization. No output escapes first.
    require(audit_custody(source, as_of) == initial_custody, "cohort_source_changed_during_replay")
    return report


def verify_cohort(
    report: FrozenJSON,
    trusted_report_pin: str,
    trusted_original_references_pin: str,
    source: EvaluationSources,
    policy_receipt: ReceiptOwner,
    prediction_receipts: dict[str, ReceiptOwner],
    expectations: FrozenJSON,
    expectations_pin: str,
    anchor: FrozenJSON | None,
    anchor_pin: str | None,
    as_of: datetime,
) -> None:
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_BYTES,
        "cohort_output_budget",
    )
    require(report.identifier == sha(trusted_report_pin), "cohort_report_pin_mismatch")
    require(
        report.to_dict().get("version") == "prospective_receipt_cohort_reconciliation_v1",
        "cohort_report_version",
    )
    replay = reconcile_cohort(
        source,
        policy_receipt,
        prediction_receipts,
        expectations,
        expectations_pin,
        anchor,
        anchor_pin,
        as_of,
    )
    require(
        replay.to_dict()["original_references_pin"] == sha(trusted_original_references_pin),
        "cohort_original_references_pin_mismatch",
    )
    if replay != report:
        # Explicit compatibility with reports generated by the original v1
        # algorithm. Only its closed-form work bound may differ; all evidence,
        # counts, signatures, ordering and single-case pins must match exactly.
        legacy = replay.to_dict()
        n = legacy["counts"]["total_declared_predictions"]
        legacy["work_bounds"]["maximum_full_cohort_case_traversals_per_replay_layer"] = n**2
        require(FrozenJSON.freeze(legacy) == report, "cohort_semantic_mismatch")
