"""Bounded training/tuning-only research iteration, with original economic replay."""

from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from statistics import median
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.paper_valuation import ValuationEvidence
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.strategy_execution import (
    StrategyExecutionBatch,
    StrategyExecutionRequest,
    execute_strategy_candidates,
)
from donghak_stock_vision.backtest.strategy_execution_analysis import (
    PinnedValuation,
    StrategyEconomicAnalysis,
    analyze_strategy_execution,
    verify_strategy_analysis,
)
from donghak_stock_vision.backtest.strategy_experiment import candidates
from donghak_stock_vision.backtest.strategy_observations import (
    PartitionAuthorization,
    SyntheticObservations,
)
from donghak_stock_vision.backtest.validation import integer, require, sha, text
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.paper_types import validate_owner, wire
from donghak_stock_vision.trading.risk import MarketQuote


def _ratio(value: Fraction | None) -> dict[str, str] | None:
    return (
        None
        if value is None
        else {"numerator": str(value.numerator), "denominator": str(value.denominator)}
    )


@dataclass(frozen=True)
class TuningMarks:
    """Explicit pinned marks, not execution evidence until bound to original run IDs."""

    case_id: str
    opening_at: datetime
    closing_at: datetime
    opening_marks: tuple[MarketQuote, ...]
    closing_marks: tuple[MarketQuote, ...]
    max_age_seconds: int
    cash_flow_scope: str | None

    def __post_init__(self) -> None:
        # Reuse original mark/time/policy validation; zero IDs are only template placeholders.
        self.bind("0" * 64, "0" * 64, "0" * 64)

    def bind(self, experiment: str, run: str, execution: str) -> ValuationEvidence:
        return ValuationEvidence(
            text(self.case_id),
            experiment,
            run,
            execution,
            self.opening_at,
            self.closing_at,
            self.opening_marks,
            self.closing_marks,
            self.max_age_seconds,
            self.cash_flow_scope,
        )


@dataclass(frozen=True)
class ImprovementPlan:
    cycle_id: str
    source: StrategyExecutionRequest
    valuation_policy: FrozenJSON
    trusted_policy_hash: str
    marks: tuple[TuningMarks, ...]
    search_axis: str
    initial_indices: tuple[int, ...]
    metric: str
    minimum_coverage: str
    max_generations: int
    max_attempts: int
    max_result_bytes: int

    def __post_init__(self) -> None:
        text(self.cycle_id)
        require(len(self.cycle_id) <= 128, "cycle_identity_bound")
        # Exact allowlist BEFORE reading owner fields; no generic reports or OOS readers.
        require(type(self.source) is StrategyExecutionRequest, "tuning_source_required")
        require(type(self.source.observations) is SyntheticObservations, "tuning_source_required")
        require(type(self.source.authorization) is PartitionAuthorization, "tuning_source_required")
        require(self.source.authorization.role in ("training", "tuning"), "feedback_role_forbidden")
        self.source.verify()
        require(self.valuation_policy.identifier == sha(self.trusted_policy_hash), "policy_pin")
        require(
            self.trusted_policy_hash == self.source.plan.valuation_policy.to_dict()["content_hash"],
            "policy_identity",
        )
        require(type(self.marks) is tuple and len(self.marks) <= 4, "marks_bound")
        require(all(type(m) is TuningMarks for m in self.marks), "tuning_marks_required")
        require(len({m.case_id for m in self.marks}) == len(self.marks), "duplicate_marks")
        require(
            {m.case_id for m in self.marks} <= {c.case_id for c in self.source.observations.cases},
            "unmatched_marks",
        )
        for mark in self.marks:
            validate_owner(mark)
        axes = self.source.plan.grid
        require(self.search_axis in {a.name for a in axes}, "unknown_search_axis")
        require(
            all(a.name == self.search_axis or len(a.values) == 1 for a in axes),
            "single_search_axis_required",
        )
        size = len(next(a for a in axes if a.name == self.search_axis).values)
        require(
            type(self.initial_indices) is tuple and 0 < len(self.initial_indices) <= 5,
            "initial_candidate_bound",
        )
        require(len(set(self.initial_indices)) == len(self.initial_indices), "duplicate_initial")
        require(all(0 <= integer(i) < size for i in self.initial_indices), "initial_index")
        require(self.metric in ("mean_return", "median_return"), "unsupported_objective")
        require(
            type(self.minimum_coverage) is str
            and 0 < len(self.minimum_coverage) <= 32
            and all(c in "0123456789./" for c in self.minimum_coverage),
            "coverage_string_required",
        )
        require(0 < Fraction(self.minimum_coverage) <= 1, "coverage_threshold")
        require(0 < integer(self.max_generations) <= 16, "generation_budget")
        require(len(self.initial_indices) <= integer(self.max_attempts) <= 64, "attempt_budget")
        require(0 < integer(self.max_result_bytes) <= 16 * 1024 * 1024, "result_budget")
        require(
            len(self.initial_indices) * len(self.source.observations.cases)
            <= self.source.plan.limits.max_runs,
            "run_budget",
        )
        bounded_json(self.document, self.source.plan.limits.max_spec_bytes)

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "strategy_improvement_plan_v1",
                "cycle_id": self.cycle_id,
                "source_plan_hash": self.source.trusted_plan_hash,
                "source_hash": self.source.trusted_observations_hash,
                "authorization_hash": self.source.trusted_authorization_hash,
                "role": self.source.authorization.role,
                "policy_hash": self.trusted_policy_hash,
                "marks": [wire(m) for m in self.marks],
                "search_axis": self.search_axis,
                "initial_indices": list(self.initial_indices),
                "objective_version": "verified_tuning_return_v1",
                "metric": self.metric,
                "minimum_coverage": self.minimum_coverage,
                "max_generations": self.max_generations,
                "max_attempts": self.max_attempts,
                "max_result_bytes": self.max_result_bytes,
                "proposal_policy": "eligible_best_nearest_unseen_axis_v1",
                "feedback_allowlist": ["training", "tuning"],
                "operational_eligible": False,
            }
        )


def generation_request(
    plan: ImprovementPlan,
    generation: int,
    indices: tuple[int, ...],
    parent: str | None,
) -> StrategyExecutionRequest:
    """Explicit v1 namespace bridge; original candidate IDs remain plan-specific."""
    require(type(plan) is ImprovementPlan, "cycle_plan_required")
    plan.__post_init__()
    require(0 <= integer(generation) < plan.max_generations, "generation_index")
    require(
        (generation == 0 and parent is None) or (generation > 0 and parent is not None),
        "generation_parent",
    )
    if parent is not None:
        sha(parent)
    size = len(next(a for a in plan.source.plan.grid if a.name == plan.search_axis).values)
    require(
        type(indices) is tuple and 0 < len(indices) <= min(5, plan.max_attempts),
        "generation_candidate_bound",
    )
    require(
        len(set(indices)) == len(indices) and all(0 <= integer(i) < size for i in indices),
        "generation_indices",
    )
    grid = tuple(
        replace(a, values=tuple(a.values[i] for i in indices)) if a.name == plan.search_axis else a
        for a in plan.source.plan.grid
    )
    provenance = FrozenJSON.freeze(
        {
            "artifact_id": "training-tuning-cycle-generation",
            "version": "v1",
            "content_hash": digest(
                {
                    "cycle": plan.document.identifier,
                    "generation": generation,
                    "parent": parent,
                    "indices": list(indices),
                }
            ),
        }
    )
    derived = replace(plan.source.plan, grid=grid, provenance=provenance)
    return replace(plan.source, plan=derived, trusted_plan_hash=derived.document.identifier)


@dataclass(frozen=True)
class TuningFeedback:
    cycle_hash: str
    generation: int
    parent_hash: str | None
    indices: tuple[int, ...]
    batch: StrategyExecutionBatch
    analysis: StrategyEconomicAnalysis
    marks: tuple[PinnedValuation, ...]


def _marks(plan: ImprovementPlan, batch: StrategyExecutionBatch) -> tuple[PinnedValuation, ...]:
    templates = {m.case_id: m for m in plan.marks}
    result = []
    for owner in batch.inputs:
        for record in owner.checkpoint.to_dict()["records"]:
            template = templates.get(record["job_key"])
            if record["status"] != "succeeded" or template is None:
                continue
            evidence = template.bind(
                owner.experiment.fingerprint,
                record["run_id"],
                record["output"]["execution_fingerprint"],
            )
            result.append(PinnedValuation(evidence, evidence.document.identifier))
    return tuple(result)


def evaluate_generation(
    plan: ImprovementPlan,
    generation: int,
    indices: tuple[int, ...],
    parent: str | None,
) -> TuningFeedback:
    request = generation_request(plan, generation, indices, parent)
    batch = execute_strategy_candidates(request)
    marks = _marks(plan, batch)
    analysis = analyze_strategy_execution(
        batch,
        batch.report.to_dict()["content_fingerprint"],
        request,
        plan.valuation_policy,
        plan.trusted_policy_hash,
        marks,
    )
    return TuningFeedback(
        plan.document.identifier, generation, parent, indices, batch, analysis, marks
    )


def verify_feedback(
    plan: ImprovementPlan,
    feedback: TuningFeedback,
    generation: int,
    indices: tuple[int, ...],
    parent: str | None,
) -> None:
    require(type(feedback) is TuningFeedback, "tuning_feedback_owner_required")
    require(
        (feedback.cycle_hash, feedback.generation, feedback.indices, feedback.parent_hash)
        == (plan.document.identifier, generation, indices, parent),
        "feedback_lineage_mismatch",
    )
    request = generation_request(plan, generation, indices, parent)
    require(feedback.marks == _marks(plan, feedback.batch), "feedback_marks_mismatch")
    verify_strategy_analysis(
        feedback.analysis,
        feedback.analysis.report.to_dict()["content_fingerprint"],
        feedback.batch,
        feedback.batch.report.to_dict()["content_fingerprint"],
        request,
        plan.valuation_policy,
        plan.trusted_policy_hash,
        feedback.marks,
    )


def _assess(plan: ImprovementPlan, feedback: TuningFeedback) -> list[dict[str, Any]]:
    request = generation_request(plan, feedback.generation, feedback.indices, feedback.parent_hash)
    rows = []
    expected = len(plan.source.observations.cases)
    for index, candidate, entry in zip(
        feedback.indices,
        candidates(request.plan),
        feedback.batch.report.to_dict()["candidates"],
        strict=True,
    ):
        observations = []
        sources = []
        evaluated_cases: set[str] = set()
        owner = None
        # Match the complete original immutable experiment, including its frozen strategy.
        if entry["status"] != "failed":
            owner = next(
                (
                    i
                    for i in feedback.analysis.inputs
                    if i.experiment.to_dict() == entry["experiment"]
                ),
                None,
            )
        if owner is not None:
            for value in owner.valuations:
                doc = value.report.to_dict()
                metric = doc["performance"]["total_return"]
                sources.append(
                    {
                        "valuation_hash": value.trusted_report_hash,
                        "performance": doc["performance"],
                        "execution_costs": doc["execution_costs"],
                    }
                )
                if metric["available"]:
                    require(value.job_key not in evaluated_cases, "duplicate_evaluated_case")
                    evaluated_cases.add(value.job_key)
                    ratio = metric["exact_ratio"]
                    observations.append(
                        Fraction(int(ratio["numerator"]), int(ratio["denominator"]))
                    )
        coverage = Fraction(len(observations), expected)
        eligible = entry["status"] == "completed" and coverage >= Fraction(plan.minimum_coverage)
        metric_value = None
        if observations:
            metric_value = (
                sum(observations, Fraction()) / len(observations)
                if plan.metric == "mean_return"
                else median(observations)
            )
        rows.append(
            {
                "axis_index": index,
                "candidate_id": candidate.candidate_id,
                "configuration_hash": candidate.parameters.identifier,
                "parameters": candidate.parameters.to_dict(),
                "status": entry["status"],
                "execution_reason": entry["reason"],
                "failure_classification": entry["failure_classification"],
                "expected": expected,
                "evaluated": len(observations),
                "evaluated_case_ids": sorted(evaluated_cases),
                "coverage": _ratio(coverage),
                "eligible": eligible,
                "objective": _ratio(metric_value),
                "exclusion": None if eligible else "incomplete_or_unavailable_tuning_evidence",
                "sources": sources,
                "runs": entry.get("runs", []),
            }
        )
    return rows


@dataclass(frozen=True)
class ImprovementResult:
    report: FrozenJSON
    feedback: tuple[TuningFeedback, ...]


def run_improvement(
    plan: ImprovementPlan,
    trusted_plan_hash: str,
    *,
    cancel_before_generation: int | None = None,
) -> ImprovementResult:
    require(type(plan) is ImprovementPlan, "cycle_plan_required")
    validate_owner(plan)
    require(plan.document.identifier == sha(trusted_plan_hash), "cycle_plan_pin")
    if cancel_before_generation is not None:
        require(0 <= integer(cancel_before_generation) <= plan.max_generations, "cancel_boundary")
    proposed = plan.initial_indices
    visited: set[int] = set()
    feedbacks = []
    generations = []
    parent = None
    history: list[dict[str, Any]] = []
    state, reason = "completed", "generation_budget_exhausted"
    retained = 0
    size = len(next(a for a in plan.source.plan.grid if a.name == plan.search_axis).values)
    events = ["planned", "running"]
    for generation in range(plan.max_generations):
        if generation == cancel_before_generation:
            state, reason = "stopped", "cancelled_at_generation_boundary"
            break
        if len(visited) + len(proposed) > plan.max_attempts:
            state, reason = "stopped", "attempt_budget_exhausted"
            break
        # Prior generations provide an observed lower-bound budget for the next one.
        # Existing per-run limits still govern the execution itself.
        remaining = plan.max_result_bytes - retained
        if generation > 0 and remaining <= max(0, retained // generation):
            state, reason = "stopped", "evidence_budget_preflight"
            break
        feedback = evaluate_generation(plan, generation, proposed, parent)
        verify_feedback(plan, feedback, generation, proposed, parent)
        rows = _assess(plan, feedback)
        # Bound retained evidence as well as the compact report, before retaining a generation.
        retained += len(feedback.batch.report.payload_json.encode())
        retained += len(feedback.analysis.report.payload_json.encode())
        retained += sum(len(i.checkpoint.payload_json.encode()) for i in feedback.batch.inputs)
        require(retained <= plan.max_result_bytes, "cycle_evidence_budget")
        visited.update(proposed)
        history.extend(rows)
        # Incomplete but policy-eligible results remain visible, but must not drive
        # research priority against different observed subsets. Require full paired
        # case membership before comparing return objectives for new proposals.
        expected_cases = {c.case_id for c in plan.source.observations.cases}
        eligible = [
            r for r in history
            if r["eligible"] and set(r["evaluated_case_ids"]) == expected_cases
        ]
        unseen = [i for i in range(size) if i not in visited]
        anchor = None
        next_indices: tuple[int, ...] = ()
        if eligible and unseen:
            best = max(
                eligible,
                key=lambda r: (
                    Fraction(int(r["objective"]["numerator"]), int(r["objective"]["denominator"])),
                    -r["axis_index"],
                ),
            )
            anchor = best["configuration_hash"]
            next_indices = (min(unseen, key=lambda i: (abs(i - best["axis_index"]), i)),)
        record = {
            "generation": generation,
            "parent_hash": parent,
            "indices": list(proposed),
            "execution_hash": feedback.batch.report.to_dict()["content_fingerprint"],
            "analysis_hash": feedback.analysis.report.to_dict()["content_fingerprint"],
            "assessments": rows,
            "research_priority_anchor": anchor,
            "next_indices": list(next_indices),
        }
        record["generation_hash"] = digest(record)
        parent = record["generation_hash"]
        feedbacks.append(feedback)
        generations.append(record)
        if not unseen:
            reason = "finite_search_exhausted"
            break
        if not eligible:
            state, reason = "stopped", "no_eligible_tuning_feedback"
            break
        proposed = next_indices
    if state != "stopped" and history and all(r["status"] == "failed" for r in history):
        state, reason = "failed", "all_candidate_configurations_failed"
    value = {
        "version": "strategy_improvement_report_v1",
        "cycle_hash": trusted_plan_hash,
        "cycle_id": plan.cycle_id,
        "state": state,
        "events": [*events, state],
        "stop_reason": reason,
        "generations": generations,
        "attempted": len(visited),
        "retained_evidence_bytes": retained,
        "cancel_before_generation": cancel_before_generation,
        "feedback_roles": [plan.source.authorization.role],
        "cohort": plan.source.plan.accessible_partition(
            plan.source.authorization.role
        ).cohort.to_dict(),
        "objective": plan.metric,
        "simulated": True,
        "operational_eligible": False,
        "oos_access": False,
        "holdout_access": False,
        "production_selection": None,
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, plan.max_result_bytes - retained)
    return ImprovementResult(report, tuple(feedbacks))


def verify_improvement(
    result: ImprovementResult,
    trusted_result_hash: str,
    plan: ImprovementPlan,
    trusted_plan_hash: str,
    *,
    cancel_before_generation: int | None = None,
) -> None:
    require(type(result) is ImprovementResult, "cycle_result_owner_required")
    require(
        result.report.to_dict().get("content_fingerprint") == sha(trusted_result_hash),
        "cycle_result_pin",
    )
    # Fresh owners and full original strategy/risk/execution/economic replay, no trusted cache.
    fresh = run_improvement(
        plan, trusted_plan_hash, cancel_before_generation=cancel_before_generation
    )
    require(result == fresh, "cycle_semantic_replay_mismatch")
