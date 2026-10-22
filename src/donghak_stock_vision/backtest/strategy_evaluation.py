"""Explicit evidence projection for tuning; no evaluator implementation or optimizer."""

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from typing import Any, Protocol

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_comparison import (
    PerformanceInput,
    verify_performance_comparison,
)
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.strategy_experiment import (
    ResearchPlan,
    StrategyCandidate,
    candidates,
)
from donghak_stock_vision.backtest.validation import require, sha, text
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.paper_types import wire


@dataclass(frozen=True)
class ComparisonEvidence:
    report: FrozenJSON
    trusted_report_hash: str
    inputs: tuple[PerformanceInput, ...]
    policy: FrozenJSON
    trusted_policy_hash: str
    experiment_hash: str
    max_jobs: int


@dataclass(frozen=True)
class CandidateAssessment:
    candidate: StrategyCandidate
    state: str
    reason: str | None
    provenance: FrozenJSON
    source: ComparisonEvidence | None

    @property
    def document(self) -> FrozenJSON:
        source = self.source
        return FrozenJSON.freeze(
            {
                "version": "strategy_candidate_assessment_v1",
                "candidate": self.candidate.document.to_dict(),
                "state": self.state,
                "reason": self.reason,
                "provenance": self.provenance.to_dict(),
                "source": None
                if source is None
                else {
                    "comparison_hash": source.trusted_report_hash,
                    "policy_hash": source.trusted_policy_hash,
                    "experiment_hash": source.experiment_hash,
                    "owners": [i.experiment.fingerprint for i in source.inputs],
                    "checkpoint_pins": [i.trusted_checkpoint_hash for i in source.inputs],
                    "max_jobs": source.max_jobs,
                },
            }
        )


class CandidateEvaluator(Protocol):
    """Future explicit integration port. This PR supplies only a TEST fixture adapter.

    The caller retains independent assessment pins; returned summaries are never trusted
    without source verification. The port is not a strategy loader or execution language.
    """

    def assess(self, candidate: StrategyCandidate) -> CandidateAssessment: ...


def _timestamps(owner: Any) -> list[datetime]:
    if isinstance(owner, datetime):
        return [owner]
    if is_dataclass(owner) and not isinstance(owner, type):
        return [t for f in fields(owner) for t in _timestamps(getattr(owner, f.name))]
    if isinstance(owner, tuple):
        return [t for item in owner for t in _timestamps(item)]
    return []


def _verified_metrics(plan: ResearchPlan, item: CandidateAssessment) -> dict[str, Any]:
    source = item.source
    require(type(source) is ComparisonEvidence, "comparison_owners_required")
    assert source is not None
    require(source.policy.identifier == source.trusted_policy_hash, "policy_pin_mismatch")
    require(
        plan.valuation_policy.to_dict()["content_hash"] == source.trusted_policy_hash,
        "candidate_valuation_policy_mismatch",
    )
    partition = plan.accessible_partition("tuning")
    require(
        source.policy.to_dict().get("cohort_id") == partition.cohort.to_dict()["artifact_id"],
        "candidate_cohort_mismatch",
    )
    require(
        type(source.inputs) is tuple and 2 <= len(source.inputs) <= 5, "comparison_owners_required"
    )
    # Check ALL owners before replay. A tuning assessment cannot import a hidden OOS variant.
    for owner in source.inputs:
        exp = owner.experiment
        require(exp.dataset == partition.dataset, "candidate_dataset_mismatch")
        require(
            exp.market == plan.market and exp.currency == plan.currency, "candidate_market_mismatch"
        )
        require(
            exp.execution_policy.identifier == plan.execution_policy.to_dict()["content_hash"],
            "candidate_execution_policy_mismatch",
        )
        require(
            exp.code == plan.code and exp.limits == plan.limits and exp.seed == plan.seed,
            "candidate_execution_context_mismatch",
        )
        for scenario in owner.adapter.scenarios:
            require(
                digest(wire(scenario.policy.risk_policies))
                == plan.risk_policy.to_dict()["content_hash"],
                "candidate_risk_policy_mismatch",
            )
            times = _timestamps(scenario)
            require(
                bool(times) and all(partition.window.contains(t) for t in times),
                "tuning_temporal_membership_unavailable",
            )
        for valuation in owner.valuations:
            require(
                all(partition.window.contains(t) for t in _timestamps(valuation.evidence)),
                "tuning_valuation_outside_partition",
            )
    matches = [i for i in source.inputs if i.experiment.fingerprint == source.experiment_hash]
    require(len(matches) == 1, "candidate_experiment_owner_missing")
    exp = matches[0].experiment
    require(exp.strategy == plan.strategy, "candidate_strategy_mismatch")
    require(exp.parameters == item.candidate.parameters, "candidate_parameter_mismatch")
    verify_performance_comparison(
        source.report,
        source.trusted_report_hash,
        source.inputs,
        source.policy,
        source.trusted_policy_hash,
        max_jobs=source.max_jobs,
    )
    row = next(
        r
        for r in source.report.to_dict()["experiments"]
        if r["experiment_fingerprint"] == source.experiment_hash
    )
    return {
        "comparison_hash": source.trusted_report_hash,
        "experiment_hash": source.experiment_hash,
        "evaluation_membership_hash": digest(sorted(r["pair_id"] for r in row["runs"])),
        "walk_forward_report": None,
        "walk_forward_reason": "oos_feedback_forbidden_during_tuning",
        "return_statistics": row["return_statistics"],
        "outcomes": row["outcomes"],
        "runs": row["runs"],
    }


def summarize_experiment(
    plan: ResearchPlan,
    trusted_plan_hash: str,
    assessments: tuple[CandidateAssessment, ...],
    trusted_assessment_hashes: tuple[str, ...],
) -> FrozenJSON:
    """Revalidate sources on every call; pins must come from independent trusted custody.

    Missing assessments remain in the denominator. No OOS owner is accepted by this
    tuning-only interface. Declared split provenance is not historical PIT approval.
    """
    expected = candidates(plan)
    require(plan.document.identifier == sha(trusted_plan_hash), "plan_pin_mismatch")
    require(
        type(assessments) is tuple and type(trusted_assessment_hashes) is tuple,
        "immutable_assessments_required",
    )
    require(len(assessments) == len(trusted_assessment_hashes) <= len(expected), "assessment_count")
    require(all(type(a) is CandidateAssessment for a in assessments), "assessment_owners_required")
    require(
        len({a.candidate.candidate_id for a in assessments}) == len(assessments),
        "duplicate_candidate_assessment",
    )
    total_jobs = 0
    for item in assessments:
        if item.source is not None:
            require(type(item.source) is ComparisonEvidence, "comparison_owners_required")
            require(
                type(item.source.inputs) is tuple and 2 <= len(item.source.inputs) <= 5,
                "comparison_owners_required",
            )
            require(
                all(type(i) is PerformanceInput for i in item.source.inputs),
                "performance_input_owners_required",
            )
            total_jobs += sum(len(i.experiment.jobs) for i in item.source.inputs)
    require(total_jobs <= min(plan.limits.max_runs, 300), "evaluation_job_budget")
    by_id = {
        a.candidate.candidate_id: (a, pin)
        for a, pin in zip(assessments, trusted_assessment_hashes, strict=True)
    }
    allowed = {c.candidate_id for c in expected}
    require(set(by_id) <= allowed, "candidate_not_in_plan")
    rows = []
    reason: str | None
    membership: str | None = None
    for candidate in expected:
        supplied = by_id.get(candidate.candidate_id)
        metrics = None
        if supplied is None:
            state, reason, provenance, assessment_hash = (
                "unavailable",
                "assessment_missing",
                None,
                None,
            )
        else:
            item, pin = supplied
            require(item.candidate == candidate, "candidate_identity_mismatch")
            bounded_json(item.document, 65536)
            require(item.document.identifier == sha(pin), "assessment_pin_mismatch")
            bounded_json(item.provenance, 4096)
            require(item.provenance == plan.provenance, "assessment_provenance_mismatch")
            require(item.state in ("completed", "failed", "unavailable"), "assessment_state")
            state, reason = item.state, item.reason
            provenance, assessment_hash = item.provenance.to_dict(), pin
            if state == "completed":
                require(reason is None, "completed_reason_not_allowed")
                metrics = _verified_metrics(plan, item)
                current_membership = str(metrics["evaluation_membership_hash"])
                require(
                    membership is None or membership == current_membership,
                    "candidate_evaluation_membership_mismatch",
                )
                membership = current_membership
                if not metrics["return_statistics"]["evaluated_count"]:
                    state, reason = "unavailable", "no_evaluated_returns"
            else:
                require(item.source is None, "noncompleted_economic_source")
                require(len(text(reason)) <= 256, "assessment_reason_bound")
                require(
                    reason
                    in (
                        (
                            "infrastructure_failure",
                            "invalid_configuration",
                            "execution_budget_exhausted",
                        )
                        if state == "failed"
                        else (
                            "inputs_missing",
                            "evaluation_not_supported",
                            "partition_provenance_unavailable",
                        )
                    ),
                    "unsupported_assessment_reason",
                )
        rows.append(
            {
                "candidate": candidate.document.to_dict(),
                "state": state,
                "reason": reason,
                "provenance": provenance,
                "assessment_hash": assessment_hash,
                "metrics": metrics,
                "failure_category": "infrastructure" if state == "failed" else None,
            }
        )
    value = {
        "version": "strategy_research_summary_v1",
        "plan_hash": trusted_plan_hash,
        "experiment_id": plan.experiment_id,
        "stage": "tuning",
        "candidate_count": len(expected),
        "evaluated_count": sum(r["state"] == "completed" for r in rows),
        "unavailable_count": sum(r["state"] == "unavailable" for r in rows),
        "failed_count": sum(r["state"] == "failed" for r in rows),
        "cohort": plan.accessible_partition("tuning").cohort.to_dict(),
        "evaluation_policy": plan.valuation_policy.to_dict(),
        "candidates": rows,
        "ranking": None,
        "operational_eligible": False,
        "historical_pit_approved": False,
        "usage_restriction": "research_only",
        "limitations": [
            "explicit_synthetic_scripts_not_parameter_execution",
            "no_oos_or_holdout_feedback",
            "no_strategy_selection",
            "split_metadata_not_training_custody_or_pit_approval",
            "failed_assessments_are_pinned_claims_not_replayed_infrastructure",
        ],
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, 8 * 1024 * 1024)
    return report


def verify_experiment_summary(
    report: FrozenJSON,
    trusted_report_hash: str,
    plan: ResearchPlan,
    trusted_plan_hash: str,
    assessments: tuple[CandidateAssessment, ...],
    trusted_assessment_hashes: tuple[str, ...],
) -> None:
    bounded_json(report, 8 * 1024 * 1024)
    value = report.to_dict()
    require(
        value.get("content_fingerprint")
        == sha(trusted_report_hash)
        == digest({k: v for k, v in value.items() if k != "content_fingerprint"}),
        "summary_hash_mismatch",
    )
    require(
        report
        == summarize_experiment(plan, trusted_plan_hash, assessments, trusted_assessment_hashes),
        "summary_replay_mismatch",
    )
