"""Versioned frozen-strategy adapter; comparison-oriented walk-forward v1 stays unchanged."""

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.frozen_oos_contracts import FrozenOOSRequest
from donghak_stock_vision.backtest.frozen_oos_execution import FrozenOOSResult, verify_frozen_oos
from donghak_stock_vision.backtest.simulation_comparison import _statistics
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.simulation_validation import WalkForwardPlan
from donghak_stock_vision.backtest.strategy_execution_analysis import PinnedValuation
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.paper_types import wire


@dataclass(frozen=True)
class PinnedFrozenFold:
    request: FrozenOOSRequest
    result: FrozenOOSResult
    trusted_result_hash: str
    marks: tuple[PinnedValuation, ...]
    cancelled: bool


def _compatibility(request: FrozenOOSRequest) -> str:
    plan = request.spec.plan
    return digest(
        {
            "strategy": plan.strategy.to_dict(),
            "code": plan.code.to_dict(),
            "parameters": request.spec.candidate.parameters.to_dict(),
            "market": plan.market.value,
            "currency": plan.currency.value,
            "execution": plan.execution_policy.to_dict(),
            "risk": plan.risk_policy.to_dict(),
            "valuation": plan.valuation_policy.to_dict(),
            "methodology": wire(request.spec.methodology),
            "cohort": plan.partitions[2].cohort.to_dict(),
            "seed": plan.seed,
            "accounts": [
                {
                    "case_id": c.case_id,
                    "instrument": wire(c.instrument),
                    "balances": wire(c.initial_account.balances),
                    "positions": wire(c.initial_account.positions),
                }
                for c in request.observations.cases
            ],
        }
    )


def summarize_frozen_walk_forward(
    plan: WalkForwardPlan,
    trusted_plan_hash: str,
    folds: tuple[PinnedFrozenFold, ...],
) -> FrozenJSON:
    require(type(plan) is WalkForwardPlan, "original_walk_forward_plan_required")
    plan.__post_init__()
    require(plan.document.identifier == sha(trusted_plan_hash), "frozen_walk_forward_plan_pin")
    require(
        type(folds) is tuple
        and len(folds) <= len(plan.folds)
        and all(type(f) is PinnedFrozenFold for f in folds),
        "bounded_frozen_fold_owners_required",
    )
    ids = [f.request.spec.fold_id for f in folds]
    require(
        len(set(ids)) == len(ids) and set(ids) <= {f.fold_id for f in plan.folds},
        "duplicate_or_unknown_frozen_fold",
    )
    require(
        len({f.request.spec.cycle_id for f in folds}) == len(folds), "distinct_fold_cycles_required"
    )
    require(
        len({f.request.trusted_custody_hash for f in folds}) == len(folds), "fold_custody_reuse"
    )
    require(sum(len(f.request.observations.cases) for f in folds) <= 80, "frozen_fold_job_budget")
    by_id = {f.request.spec.fold_id: f for f in folds}
    rows: list[dict[str, Any]] = []
    compatible = set()
    values = []
    complete = 0
    for declared in plan.folds:
        source = by_id.get(declared.fold_id)
        if source is None:
            rows.append(
                {
                    "fold_id": declared.fold_id,
                    "status": "skipped",
                    "reason": "frozen_fold_evidence_missing",
                    "expected_cases": declared.expected_cases,
                    "result_hash": None,
                    "return_statistics": None,
                }
            )
            continue
        require(
            source.request.walk_forward == plan
            and source.request.trusted_walk_forward_hash == trusted_plan_hash,
            "frozen_fold_plan_mismatch",
        )
        verify_frozen_oos(
            source.result,
            source.trusted_result_hash,
            source.request,
            source.marks,
            cancelled=source.cancelled,
        )
        fold_report = source.result.report.to_dict()
        full = fold_report["return_statistics"]["evaluated_count"] == declared.expected_cases
        complete += int(full)
        compatible.add(_compatibility(source.request))
        for case in fold_report["cases"]:
            if case["return"] is not None:
                ratio = case["return"]
                values.append(Fraction(int(ratio["numerator"]), int(ratio["denominator"])))
        rows.append(
            {
                "fold_id": declared.fold_id,
                "status": "complete" if full else "incomplete",
                "reason": None if full else "incomplete_oos_coverage",
                "expected_cases": declared.expected_cases,
                "result_hash": source.trusted_result_hash,
                "spec_hash": source.request.trusted_spec_hash,
                "custody_hash": source.request.trusted_custody_hash,
                "return_statistics": fold_report["return_statistics"],
                "executed_cases": fold_report["executed_cases"],
                "completed_cases": fold_report["completed_cases"],
                "failed_cases": fold_report["failed_cases"],
                "cancelled_cases": fold_report["cancelled_cases"],
                "unavailable_returns": fold_report["unavailable_returns"],
            }
        )
    reason = (
        "heterogeneous_frozen_strategy_or_policies"
        if len(compatible) > 1
        else ("minimum_complete_folds_unmet" if complete < plan.min_folds else None)
    )
    value = {
        "version": "frozen_strategy_walk_forward_v1",
        "walk_forward_hash": trusted_plan_hash,
        "mode": plan.mode,
        "folds": rows,
        "expected_folds": len(plan.folds),
        "verified_folds": len(folds),
        "complete_folds": complete,
        "status": "complete" if complete == len(plan.folds) and reason is None else "incomplete",
        "expected_cases": sum(f.expected_cases for f in plan.folds),
        "executed_cases": sum(r.get("executed_cases", 0) for r in rows),
        "completed_cases": sum(r.get("completed_cases", 0) for r in rows),
        "failed_cases": sum(r.get("failed_cases", 0) for r in rows),
        "cancelled_cases": sum(r.get("cancelled_cases", 0) for r in rows),
        "unavailable_returns": sum(f.expected_cases for f in plan.folds) - len(values),
        "aggregate_statistics": _statistics(values, sum(f.expected_cases for f in plan.folds))
        if reason is None
        else None,
        "aggregate_unavailable_reason": reason,
        "coverage_warning": None
        if complete == len(plan.folds)
        else "missing_cases_remain_in_denominator",
        "historical_oos_eligible": False,
        "strict_pit_approved": False,
        "operational_eligible": False,
        "usage_restriction": "synthetic_research_only_no_tuning_feedback",
        "ranking": None,
        "walk_forward_v1_compatibility": "separate_adapter_no_schema_reinterpretation",
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, 4 * 1024 * 1024)
    return report


def verify_frozen_walk_forward(
    report: FrozenJSON,
    trusted_report_hash: str,
    plan: WalkForwardPlan,
    trusted_plan_hash: str,
    folds: tuple[PinnedFrozenFold, ...],
) -> None:
    bounded_json(report, 4 * 1024 * 1024)
    value = report.to_dict()
    require(
        value.get("content_fingerprint")
        == sha(trusted_report_hash)
        == digest({k: v for k, v in value.items() if k != "content_fingerprint"}),
        "frozen_walk_forward_report_pin",
    )
    require(
        report == summarize_frozen_walk_forward(plan, trusted_plan_hash, folds),
        "frozen_walk_forward_replay_mismatch",
    )
