"""Read-only offline prediction custody -> future outcome -> evaluation rehearsal.

Only existing source owners authenticate local evidence. This does not prove
independent chronology, custody, genuine-forward eligibility or performance.
"""

from dataclasses import dataclass
from datetime import datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.prediction_binding import (
    bind_outcome,
    inspect_prediction,
    verify_binding,
)
from donghak_stock_vision.storage.prospective_evaluation_consumer import (
    EvaluationSources,
    prepare_evaluation,
    verify_preparation,
)
from donghak_stock_vision.storage.prospective_prediction_workflow import (
    WorkflowInputs,
    verify_workflow,
)

VERSION = "prediction_outcome_e2e_rehearsal_v1"


@dataclass(frozen=True)
class RehearsalInputs:
    custody: WorkflowInputs
    evaluation: EvaluationSources


def rehearse(source: RehearsalInputs, as_of: datetime) -> FrozenJSON:
    """Replay original owners without writes and preserve v1 evaluator output."""
    require(type(source) is RehearsalInputs, "e2e_inputs_required")
    require(len(source.evaluation.cases) == 1, "e2e_single_prediction_required")
    case = source.evaluation.cases[0]
    at = timestamp(as_of)
    # Original custody is re-derived from original raw capture bytes and the
    # immutable journal. Never accept a caller's completed custody report.
    verified_prediction = inspect_prediction(case.prediction).to_dict()
    recorded_at = timestamp(verified_prediction["prediction"]["recorded_at"])
    original = verify_workflow(source.custody, case.prediction, recorded_at)
    binding = bind_outcome(case.prediction, at, case.future)
    status = binding.to_dict()["status"]
    require(status != "invalid", "e2e_invalid_outcome_evidence")
    verify_binding(binding, binding.identifier, case.prediction, at, case.future)
    prepared = prepare_evaluation(source.evaluation, at)
    details = prepared.to_dict()
    require(
        details["result"]["cases"][0]["prediction_id"] == case.prediction.prediction_id,
        "e2e_evaluation_prediction_mismatch",
    )
    require(
        not details["diagnostics"]["performance_available"]
        and all(value is None for value in details["result"]["metrics"].values())
        and details["result"]["genuine_forward_evidence"] is False
        and details["result"]["performance_approved"] is False
        and details["result"]["execution_authorized"] is False,
        "e2e_unverified_performance_forbidden",
    )
    verify_preparation(
        prepared, prepared.identifier, details["references_pin"], source.evaluation, at
    )
    return FrozenJSON.freeze(
        {
            "version": VERSION,
            "origin": "offline_synthetic_rehearsal",
            "as_of": at.isoformat(),
            "prediction_id": case.prediction.prediction_id,
            "journal_head_pin": case.prediction.journal_head,
            "entry_pin": case.prediction.entry_pin,
            "custody_workflow_pin": source.custody.workflow_pin,
            "custody_report_pin": original.identifier,
            "binding_status": status,
            "binding_reason": binding.to_dict()["reason"],
            "binding_pin": binding.identifier,
            "evaluation_report": details,
            "evaluation_report_pin": prepared.identifier,
            "local_integrity_verified": True,
            "independent_chronology_verified": False,
            "independent_external_custody_verified": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
            "network_requests": 0,
            "unresolved_prerequisites": [
                "independent_prediction_predeclaration",
                "authenticated_original_creation_time",
                "independent_external_prediction_and_observation_custody",
                "authenticated_external_trust_anchor_and_revocation_history",
                "point_in_time_and_performance_approval",
            ],
        }
    )


def verify_rehearsal(
    report: FrozenJSON, trusted_pin: str, source: RehearsalInputs, as_of: datetime
) -> None:
    """Reconstruct a retained report against original evidence, not report claims."""
    require(report.identifier == sha(trusted_pin), "e2e_report_pin_mismatch")
    require(report == rehearse(source, as_of), "e2e_report_semantic_mismatch")
