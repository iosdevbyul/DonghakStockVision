"""Frozen OOS-only execution, original economics and independent semantic replay."""

from dataclasses import dataclass
from fractions import Fraction
from time import monotonic
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.frozen_oos_contracts import FrozenOOSRequest
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler, Cancellation, RunBudget
from donghak_stock_vision.backtest.paper_simulation import (
    PaperSimulationAdapter,
    verify_paper_result,
)
from donghak_stock_vision.backtest.paper_valuation import (
    FLOW_SCOPE,
    POLICY,
    ValuationEvidence,
    value_paper_simulation,
)
from donghak_stock_vision.backtest.simulation_comparison import (
    PerformanceInput,
    ValuationSource,
    _statistics,
)
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, bounded_json
from donghak_stock_vision.backtest.strategy_execution import _compile
from donghak_stock_vision.backtest.strategy_execution_analysis import PinnedValuation
from donghak_stock_vision.backtest.validation import fields, flag, integer, require, sha, text
from donghak_stock_vision.data.learning import digest


@dataclass(frozen=True)
class FrozenOOSResult:
    report: FrozenJSON
    owner: PerformanceInput | None


def evaluate_frozen_oos(
    request: FrozenOOSRequest,
    marks: tuple[PinnedValuation, ...] = (),
    *,
    cancelled: bool = False,
) -> FrozenOOSResult:
    request.verify()
    flag(cancelled)
    spec, source = request.spec, request.observations
    require(
        spec.plan.limits.max_attempts_per_run == 1
        and len(source.cases) <= spec.plan.limits.max_runs,
        "frozen_oos_execution_budget",
    )
    p = fields(
        spec.valuation_policy.to_dict(),
        "version cohort_id valuation_policy_version max_age_seconds cash_flow_scope",
    )
    require(
        p["version"] == "simulation_performance_comparison_policy_v1"
        and p["valuation_policy_version"] == POLICY,
        "unknown_frozen_valuation_policy",
    )
    require(
        text(p["cohort_id"]) == spec.plan.partitions[2].cohort.to_dict()["artifact_id"]
        and integer(p["max_age_seconds"]) <= 86400
        and p["cash_flow_scope"] in (None, FLOW_SCOPE),
        "frozen_valuation_policy_mismatch",
    )
    require(
        type(marks) is tuple
        and len(marks) <= len(source.cases)
        and all(
            type(m) is PinnedValuation and type(m.evidence) is ValuationEvidence for m in marks
        ),
        "oos_valuation_owners_required",
    )
    require(not cancelled or not marks, "cancelled_oos_has_no_valuations")
    decisions: dict[str, list[dict[str, Any]]] = {}
    owner = None
    records: dict[str, dict[str, Any]] = {}
    experiment = None
    if not cancelled:
        compiled = [_compile(spec.candidate, case) for case in source.cases]
        adapter = PaperSimulationAdapter(tuple(s for s, _ in compiled))
        decisions = {c.case_id: rows for c, (_, rows) in zip(source.cases, compiled, strict=True)}
        instance = FrozenJSON.freeze(
            {
                "version": "frozen_oos_strategy_instance_v1",
                "spec_hash": request.trusted_spec_hash,
                "candidate": spec.candidate.document.to_dict(),
                "code": spec.plan.code.to_dict(),
                "freeze_custody_hash": request.trusted_custody_hash,
                "authorization_hash": request.trusted_authorization_hash,
            }
        )
        strategy = FrozenJSON.freeze(
            {
                "artifact_id": spec.candidate.candidate_id,
                "version": "frozen_oos_strategy_instance_v1",
                "content_hash": instance.identifier,
            }
        )
        experiment = ExperimentSpec(
            spec.evaluation_id,
            strategy,
            adapter.dataset_reference,
            spec.plan.code,
            adapter.reference,
            FrozenJSON.freeze({"strategy_parameter_scope": "frozen_oos_strategy_instance_v1"}),
            adapter.execution_policy,
            spec.plan.seed,
            spec.plan.market,
            spec.plan.currency,
            "synthetic",
            spec.plan.limits,
            tuple(
                adapter.job(c.case_id, s.scenario_id)
                for c, (s, _) in zip(source.cases, compiled, strict=True)
            ),
        )
        scheduler = BatchScheduler(experiment)
        scheduler.run(adapter)
        checkpoint = scheduler.checkpoint()
        owner = PerformanceInput(experiment, checkpoint, checkpoint.identifier, adapter, ())
        records = {r.to_dict()["job_key"]: r.to_dict() for r in scheduler.results}
    keyed = {m.evidence.run_id: m for m in marks}
    require(len(keyed) == len(marks), "duplicate_oos_valuation")
    used = set()
    valuations = []
    rows = []
    values = []
    for case in source.cases:
        record = records.get(case.case_id)
        status = "cancelled" if cancelled else record["status"] if record else "unavailable"
        valuation = None
        result = None
        amount = None
        mark = keyed.get(record["run_id"]) if record else None
        if mark is not None:
            require(
                record is not None and status == "succeeded" and owner is not None,
                "oos_valuation_requires_completed_execution",
            )
            assert record is not None and owner is not None
            used.add(mark.evidence.run_id)
            evidence = mark.evidence
            require(
                evidence.document.identifier == sha(mark.trusted_hash), "oos_valuation_pin_mismatch"
            )
            require(
                evidence.opening_at == case.steps[0].decision_frame.now
                and evidence.closing_at == case.steps[-1].execution_frame.now
                and evidence.max_age_seconds == p["max_age_seconds"]
                and evidence.cash_flow_scope == p["cash_flow_scope"],
                "frozen_valuation_rule_mismatch",
            )
            # Missing marks are unavailable; provided marks must be the ORIGINAL fixed endpoints.
            require(
                (
                    not evidence.opening_marks
                    or evidence.opening_marks == case.steps[0].decision_frame.quotes
                )
                and (
                    not evidence.closing_marks
                    or evidence.closing_marks == case.steps[-1].execution_frame.quotes
                ),
                "frozen_valuation_mark_substitution",
            )
            result = FrozenJSON.freeze(record["output"])
            job = next(j for j in owner.experiment.jobs if j.key == case.case_id)
            valuation = value_paper_simulation(
                owner.experiment,
                job,
                result,
                result.to_dict()["result_fingerprint"],
                owner.adapter,
                evidence,
                mark.trusted_hash,
            )
            valuations.append(
                ValuationSource(
                    job.key,
                    valuation,
                    valuation.to_dict()["content_fingerprint"],
                    result,
                    result.to_dict()["result_fingerprint"],
                    evidence,
                    mark.trusted_hash,
                )
            )
            metric = valuation.to_dict()["performance"]["total_return"]
            if metric["available"]:
                ratio = metric["exact_ratio"]
                amount = Fraction(int(ratio["numerator"]), int(ratio["denominator"]))
                values.append(amount)
        output = record["output"] if record and record["output"] else None
        metric = (
            valuation.to_dict()["performance"]["total_return"] if valuation is not None else None
        )
        rows.append(
            {
                "case_id": case.case_id,
                "status": status,
                "run_id": record["run_id"] if record else None,
                "decisions": decisions.get(case.case_id, []),
                "execution_result_hash": output["result_fingerprint"] if output else None,
                "execution_counts": output["execution"]["counts"] if output else None,
                "execution_costs": output["execution"]["costs"] if output else None,
                "failure": record["failure"] if record else None,
                "unavailable_reason": "explicit_pre_execution_cancellation"
                if cancelled
                else metric["reason"]
                if metric is not None and not metric["available"]
                else "valuation_not_supplied"
                if status == "succeeded" and metric is None
                else "execution_not_completed"
                if status != "succeeded"
                else None,
                "valuation": valuation.to_dict() if valuation is not None else None,
                "valuation_evidence_hash": mark.trusted_hash if mark is not None else None,
                "return": {
                    "numerator": str(amount.numerator),
                    "denominator": str(amount.denominator),
                }
                if amount is not None
                else None,
            }
        )
    require(used == set(keyed), "unmatched_oos_valuation")
    if owner is not None:
        owner = PerformanceInput(
            owner.experiment,
            owner.checkpoint,
            owner.trusted_checkpoint_hash,
            owner.adapter,
            tuple(valuations),
        )
    value = {
        "version": "frozen_strategy_oos_result_v1",
        "fold_id": spec.fold_id,
        "cycle_id": spec.cycle_id,
        "spec_hash": request.trusted_spec_hash,
        "freeze_custody_hash": request.trusted_custody_hash,
        "walk_forward_hash": request.trusted_walk_forward_hash,
        "oos_observations_hash": request.trusted_observations_hash,
        "authorization_hash": request.trusted_authorization_hash,
        "candidate": spec.candidate.document.to_dict(),
        "cancelled_before_execution": cancelled,
        "experiment": experiment.to_dict() if experiment is not None else None,
        "checkpoint_hash": owner.trusted_checkpoint_hash if owner is not None else None,
        "planned_cases": len(source.cases),
        "executed_cases": len(records),
        "completed_cases": sum(r["status"] == "succeeded" for r in rows),
        "failed_cases": sum(r["status"] in ("failed", "interrupted") for r in rows),
        "cancelled_cases": sum(r["status"] == "cancelled" for r in rows),
        "unavailable_returns": sum(r["return"] is None for r in rows),
        "coverage_warning": None
        if len(values) == len(source.cases)
        else "missing_cases_remain_in_denominator",
        "return_statistics": _statistics(values, len(source.cases)),
        "cases": rows,
        "usage_restriction": "synthetic_research_only_no_tuning_feedback",
        "historical_oos_eligible": False,
        "strict_pit_approved": False,
        "operational_eligible": False,
        "comparison_v1_status": "unavailable_single_frozen_variant",
        "walk_forward_v1_status": "separate_verified_frozen_strategy_adapter",
        "limitations": [
            "synthetic_custody_not_historical_freeze_authentication",
            "raw_price_reference_no_model_training",
            "no_same_cycle_feedback",
            "no_final_holdout_access",
            "no_ranking_or_promotion",
        ],
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, min(spec.plan.limits.max_total_result_bytes, 4 * 1024 * 1024))
    require(
        len(report.payload_json.encode())
        + (len(owner.checkpoint.payload_json.encode()) if owner else 0)
        <= spec.plan.limits.max_total_result_bytes,
        "oos_output_budget",
    )
    return FrozenOOSResult(report, owner)


def verify_frozen_oos(
    result: FrozenOOSResult,
    trusted_report_hash: str,
    request: FrozenOOSRequest,
    marks: tuple[PinnedValuation, ...] = (),
    *,
    cancelled: bool = False,
) -> None:
    require(
        type(result) is FrozenOOSResult
        and (result.owner is None or type(result.owner) is PerformanceInput),
        "original_frozen_oos_result_required",
    )
    bounded_json(result.report, 4 * 1024 * 1024)
    value = result.report.to_dict()
    require(
        value.get("content_fingerprint")
        == sha(trusted_report_hash)
        == digest({k: v for k, v in value.items() if k != "content_fingerprint"}),
        "frozen_oos_report_pin_mismatch",
    )
    fresh = evaluate_frozen_oos(request, marks, cancelled=cancelled)
    require(result == fresh, "frozen_oos_semantic_replay_mismatch")
    if result.owner is not None:
        owner = result.owner
        scheduler = BatchScheduler(
            owner.experiment,
            checkpoint=owner.checkpoint,
            expected_checkpoint_hash=owner.trusted_checkpoint_hash,
        )
        for record in scheduler.results:
            row = record.to_dict()
            if row["status"] == "succeeded":
                output = FrozenJSON.freeze(row["output"])
                verify_paper_result(output, output.to_dict()["result_fingerprint"])
                job = next(j for j in owner.experiment.jobs if j.key == row["job_key"])
                owner.adapter.replay_validate(
                    owner.experiment,
                    job,
                    output,
                    RunBudget(owner.experiment, Cancellation(), monotonic),
                )
