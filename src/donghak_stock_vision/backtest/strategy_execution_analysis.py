"""Existing valuation/comparison evidence, gated by independent strategy decision replay."""

from dataclasses import dataclass, replace
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler
from donghak_stock_vision.backtest.paper_valuation import (
    FLOW_SCOPE,
    POLICY,
    ValuationEvidence,
    value_paper_simulation,
)
from donghak_stock_vision.backtest.simulation_comparison import (
    PerformanceInput,
    ValuationSource,
    compare_performance,
)
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.strategy_evaluation import CandidateAssessment
from donghak_stock_vision.backtest.strategy_execution import (
    StrategyExecutionBatch,
    StrategyExecutionRequest,
    verify_strategy_execution,
)
from donghak_stock_vision.backtest.strategy_experiment import candidates
from donghak_stock_vision.backtest.strategy_observations import timestamps
from donghak_stock_vision.backtest.validation import fields, integer, require, sha, text
from donghak_stock_vision.data.learning import digest


@dataclass(frozen=True)
class PinnedValuation:
    evidence: ValuationEvidence
    trusted_hash: str


@dataclass(frozen=True)
class StrategyEconomicAnalysis:
    report: FrozenJSON
    inputs: tuple[PerformanceInput, ...]


def analyze_strategy_execution(
    batch: StrategyExecutionBatch,
    trusted_execution_hash: str,
    request: StrategyExecutionRequest,
    valuation_policy: FrozenJSON,
    trusted_policy_hash: str,
    marks: tuple[PinnedValuation, ...],
) -> StrategyEconomicAnalysis:
    verify_strategy_execution(batch, trusted_execution_hash, request)
    bounded_json(valuation_policy, 4096)
    require(
        valuation_policy.identifier
        == sha(trusted_policy_hash)
        == request.plan.valuation_policy.to_dict()["content_hash"],
        "valuation_policy_pin_mismatch",
    )
    p = fields(
        valuation_policy.to_dict(),
        "version cohort_id valuation_policy_version max_age_seconds cash_flow_scope",
    )
    require(
        p["version"] == "simulation_performance_comparison_policy_v1"
        and p["valuation_policy_version"] == POLICY,
        "unknown_valuation_policy",
    )
    require(
        integer(p["max_age_seconds"]) <= 86400 and p["cash_flow_scope"] in (None, FLOW_SCOPE),
        "unsupported_valuation_policy",
    )
    require(
        text(p["cohort_id"])
        == request.plan.accessible_partition(request.authorization.role).cohort.to_dict()[
            "artifact_id"
        ],
        "valuation_cohort_mismatch",
    )
    require(
        type(marks) is tuple and len(marks) <= request.plan.limits.max_runs,
        "valuation_input_budget",
    )
    require(
        all(type(m) is PinnedValuation and type(m.evidence) is ValuationEvidence for m in marks),
        "original_valuation_owners_required",
    )
    keyed = {(m.evidence.experiment_fingerprint, m.evidence.run_id): m for m in marks}
    require(len(keyed) == len(marks), "duplicate_valuation_evidence")
    used = set()
    inputs = []
    sources: list[dict[str, Any]] = []
    for owner in batch.inputs:
        valuations = []
        scheduler = BatchScheduler(
            owner.experiment,
            checkpoint=owner.checkpoint,
            expected_checkpoint_hash=owner.trusted_checkpoint_hash,
        )
        for record in scheduler.results:
            r = record.to_dict()
            key = owner.experiment.fingerprint, r["run_id"]
            mark = keyed.get(key)
            if mark is None:
                continue  # Original comparison retains this unavailable result in coverage.
            used.add(key)
            require(r["status"] == "succeeded", "valuation_requires_completed_run")
            require(
                mark.evidence.document.identifier == sha(mark.trusted_hash),
                "valuation_evidence_pin",
            )
            require(
                all(request.authorization.window.contains(t) for t in timestamps(mark.evidence)),
                "valuation_outside_authorized_partition",
            )
            require(
                mark.evidence.max_age_seconds == p["max_age_seconds"]
                and mark.evidence.cash_flow_scope == p["cash_flow_scope"],
                "valuation_policy_mismatch",
            )
            output = FrozenJSON.freeze(r["output"])
            job = next(j for j in owner.experiment.jobs if j.key == r["job_key"])
            valuation = value_paper_simulation(
                owner.experiment,
                job,
                output,
                output.to_dict()["result_fingerprint"],
                owner.adapter,
                mark.evidence,
                mark.trusted_hash,
            )
            valuations.append(
                ValuationSource(
                    job.key,
                    valuation,
                    valuation.to_dict()["content_fingerprint"],
                    output,
                    output.to_dict()["result_fingerprint"],
                    mark.evidence,
                    mark.trusted_hash,
                )
            )
            sources.append(
                {
                    "experiment_hash": owner.experiment.fingerprint,
                    "run_id": r["run_id"],
                    "evidence_hash": mark.trusted_hash,
                    "valuation": valuation.to_dict(),
                }
            )
        inputs.append(replace(owner, valuations=tuple(valuations)))
    require(used == set(keyed), "unmatched_valuation_evidence")
    comparison = (
        compare_performance(
            tuple(inputs),
            valuation_policy,
            trusted_policy_hash,
            max_jobs=min(request.plan.limits.max_runs, 300),
        )
        if len(inputs) >= 2
        else None
    )
    entries = batch.report.to_dict()["candidates"]
    value = {
        "version": "strategy_parameter_analysis_v1",
        "simulated": True,
        "execution_report_hash": trusted_execution_hash,
        "valuation_policy_hash": trusted_policy_hash,
        "candidate_count": len(entries),
        "executed_candidate_count": len(inputs),
        "failed_configuration_count": sum(r["status"] == "failed" for r in entries),
        "candidate_outcomes": [
            {
                "candidate_id": r["candidate"]["candidate_id"],
                "status": r["status"],
                "reason": r["reason"],
            }
            for r in entries
        ],
        "coverage_warning": "executed_candidate_subset_only"
        if len(inputs) != len(entries)
        else None,
        "valuations": sources,
        "comparison": comparison.to_dict() if comparison is not None else None,
        "comparison_unavailable_reason": "fewer_than_two_executed_candidates"
        if comparison is None
        else None,
        "ranking": None,
        "operational_eligible": False,
        "usage_restriction": "synthetic_test_only",
        "strategy_evaluation_v1_status": "unavailable_derived_dataset_namespace",
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, min(8 * 1024 * 1024, request.plan.limits.max_total_result_bytes))
    return StrategyEconomicAnalysis(report, tuple(inputs))


def verify_strategy_analysis(
    analysis: StrategyEconomicAnalysis,
    trusted_report_hash: str,
    batch: StrategyExecutionBatch,
    trusted_execution_hash: str,
    request: StrategyExecutionRequest,
    valuation_policy: FrozenJSON,
    trusted_policy_hash: str,
    marks: tuple[PinnedValuation, ...],
) -> None:
    require(type(analysis) is StrategyEconomicAnalysis, "analysis_owner_required")
    bounded_json(analysis.report, 8 * 1024 * 1024)
    value = analysis.report.to_dict()
    require(
        value.get("content_fingerprint")
        == sha(trusted_report_hash)
        == digest({k: v for k, v in value.items() if k != "content_fingerprint"}),
        "analysis_pin_mismatch",
    )
    fresh = analyze_strategy_execution(
        batch, trusted_execution_hash, request, valuation_policy, trusted_policy_hash, marks
    )
    require(analysis == fresh, "strategy_analysis_replay_mismatch")


def legacy_evaluation_assessments(
    batch: StrategyExecutionBatch,
    trusted_execution_hash: str,
    request: StrategyExecutionRequest,
) -> tuple[CandidateAssessment, ...]:
    """Honest v1 bridge: derived namespaces cannot satisfy raw-plan v1 economic binding."""
    verify_strategy_execution(batch, trusted_execution_hash, request)
    rows = batch.report.to_dict()["candidates"]
    return tuple(
        CandidateAssessment(
            candidate,
            "failed" if row["status"] == "failed" else "unavailable",
            "invalid_configuration" if row["status"] == "failed" else "evaluation_not_supported",
            request.plan.provenance,
            None,
        )
        for candidate, row in zip(candidates(request.plan), rows, strict=True)
    )
