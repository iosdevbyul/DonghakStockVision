"""Read-only original-owner audit; structure readiness is not prospective approval."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, text
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.prediction_binding import (
    FutureSource,
    PredictionSource,
    bind_outcome,
)

MAX_CASES = 32
MAX_REQUEST_BYTES = 256 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
STATES = ("eligible", "pending", "unavailable", "invalid")


@dataclass(frozen=True)
class EvidenceCase:
    prediction: PredictionSource
    future: FutureSource | None = None


def audit_evidence(cases: tuple[EvidenceCase, ...], as_of: datetime) -> FrozenJSON:
    """Replay every original owner, never accept a supplied binding/status summary.

    Input order is retained. Counts describe only these supplied cases, not a
    predeclared evaluation cohort or prediction accuracy.
    """
    require(type(cases) is tuple and 1 <= len(cases) <= MAX_CASES, "audit_case_budget")
    at = timestamp(as_of)
    identities: set[str] = set()
    size = 0
    for case in cases:
        require(type(case) is EvidenceCase, "audit_original_owner_required")
        source = case.prediction
        require(type(source) is PredictionSource, "audit_prediction_owner_required")
        identity = sha(source.prediction_id)
        require(identity not in identities, "audit_duplicate_prediction")
        identities.add(identity)
        require(case.future is None or type(case.future) is FutureSource, "audit_future_owner")
        requests = (source.request,) + (() if case.future is None else (case.future.request,))
        for owner in requests:
            require(type(owner) is FrozenJSON, "audit_original_request_required")
            n = len(owner.payload_json.encode())
            require(n <= MAX_REQUEST_BYTES, "audit_request_byte_budget")
            size += n
        require(size <= MAX_INPUT_BYTES, "audit_input_byte_budget")
    rows = []
    counts = dict.fromkeys(STATES, 0)
    reasons: Counter[str] = Counter()
    for case in cases:
        binding = bind_outcome(case.prediction, at, case.future)
        d = binding.to_dict()
        status, reason = d["status"], d["reason"]
        if status == "verified":
            ready = (
                d["version"] == "prediction_outcome_binding_v1"
                and d["pair_verified"] is True
                and d["offline_evaluation_eligible"] is True
                and d["evidence_class"] == "synthetic_offline_only"
                and d["genuine_forward_evidence"] is False
                and d["prospective_evaluation_eligible"] is False
            )
            status = "eligible" if ready else "invalid"
            reason = (
                "verified_offline_pair_structure_only" if ready else "unsupported_binding_assurance"
            )
        require(status in STATES, "unsupported_binding_status")
        counts[status] += 1
        reasons[reason] += 1
        rows.append(
            {
                "prediction_id": case.prediction.prediction_id,
                "entry_pin": case.prediction.entry_pin,
                "status": status,
                "reason": reason,
                "binding_pin": binding.identifier,
                "binding_status": d["status"],
                "structure_ready": status == "eligible",
                "evidence_class": d["evidence_class"] if status == "eligible" else "unqualified",
                "genuine_forward_evidence": False,
                "prospective_evaluation_eligible": False,
            }
        )
    report = FrozenJSON.freeze(
        {
            "version": "prospective_evidence_eligibility_audit_v1",
            "as_of": at.isoformat(),
            "scope": "evidence_structure_readiness_only",
            "total_supplied_cases": len(cases),
            "counts": counts,
            "reason_counts": dict(sorted(reasons.items())),
            "cases": rows,
            "genuine_forward_evidence": False,
            "prospective_performance_approved": False,
            "pit_qualified": False,
            "execution_authorized": False,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_OUTPUT_BYTES, "audit_output_byte_budget")
    return report


def verify_audit(
    report: FrozenJSON, trusted_pin: str, cases: tuple[EvidenceCase, ...], as_of: datetime
) -> None:
    require(type(report) is FrozenJSON, "audit_report_owner_required")
    require(len(report.payload_json.encode()) <= MAX_OUTPUT_BYTES, "audit_output_byte_budget")
    require(report.identifier == sha(trusted_pin), "audit_pin_mismatch")
    require(audit_evidence(cases, as_of) == report, "audit_semantic_mismatch")


def input_cases(owner: FrozenJSON, trusted_pin: str) -> tuple[EvidenceCase, ...]:
    """Parse bounded explicit owner references; no disk reads or evidence creation."""
    require(type(owner) is FrozenJSON, "audit_input_owner_required")
    require(len(owner.payload_json.encode()) <= MAX_INPUT_BYTES, "audit_input_byte_budget")
    require(owner.identifier == sha(trusted_pin), "audit_input_pin_mismatch")
    d = fields(owner.to_dict(), "version cases")
    require(d["version"] == "prospective_evidence_audit_input_v1", "audit_input_version")
    require(type(d["cases"]) is list and 1 <= len(d["cases"]) <= MAX_CASES, "audit_case_budget")

    def path(value: object) -> Path:
        p = Path(text(value))
        require(p.is_absolute(), "audit_absolute_owner_path_required")
        return p

    cases = []
    for raw in d["cases"]:
        item = fields(raw, "prediction future")
        p = fields(
            item["prediction"],
            "journal journal_head entry_pin prediction_id capture_root "
            "request request_pin policy_pin",
        )
        prediction = PredictionSource(
            path(p["journal"]),
            sha(p["journal_head"]),
            sha(p["entry_pin"]),
            sha(p["prediction_id"]),
            path(p["capture_root"]),
            FrozenJSON.freeze(p["request"]),
            sha(p["request_pin"]),
            sha(p["policy_pin"]),
        )
        future = None
        if item["future"] is not None:
            f = fields(item["future"], "capture_root request request_pin")
            future = FutureSource(
                path(f["capture_root"]), FrozenJSON.freeze(f["request"]), sha(f["request_pin"])
            )
        cases.append(EvidenceCase(prediction, future))
    return tuple(cases)
