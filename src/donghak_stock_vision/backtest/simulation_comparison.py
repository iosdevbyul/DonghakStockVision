"""Pinned cohort comparisons; no execution, valuation formula, ranking or promotion policy."""

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from time import monotonic
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler, Cancellation, RunBudget
from donghak_stock_vision.backtest.paper_scenario import PaperScenario
from donghak_stock_vision.backtest.paper_simulation import (
    PaperSimulationAdapter,
    verify_paper_result,
)
from donghak_stock_vision.backtest.paper_valuation import (
    FLOW_SCOPE,
    POLICY,
    ValuationEvidence,
    verify_valuation_report,
)
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, bounded_json
from donghak_stock_vision.backtest.validation import fields, integer, require, sha, text
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.paper_types import PaperFrame, wire

VERSION = "simulation_performance_comparison_v1"


@dataclass(frozen=True)
class ValuationSource:
    """Original typed source owners plus independently retained pins; never a summary."""

    job_key: str
    report: FrozenJSON
    trusted_report_hash: str
    result: FrozenJSON
    trusted_result_fingerprint: str
    evidence: ValuationEvidence
    trusted_evidence_hash: str


@dataclass(frozen=True)
class PerformanceInput:
    experiment: ExperimentSpec
    checkpoint: FrozenJSON
    trusted_checkpoint_hash: str
    adapter: PaperSimulationAdapter
    valuations: tuple[ValuationSource, ...]


def _ratio(value: Fraction | None) -> dict[str, str] | None:
    return (
        {"numerator": str(value.numerator), "denominator": str(value.denominator)}
        if value is not None
        else None
    )


def _statistics(values: list[Fraction], expected: int) -> dict[str, Any]:
    ordered = sorted(values)
    count = len(values)
    mean = sum(values, Fraction(0)) / count if count else None
    median = ((ordered[(count - 1) // 2] + ordered[count // 2]) / 2) if count else None
    variance = (
        sum(((v - mean) ** 2 for v in values), Fraction(0)) / count if mean is not None else None
    )
    return {
        "expected_count": expected,
        "evaluated_count": count,
        "coverage": _ratio(Fraction(count, expected)),
        "positive_count": sum(v > 0 for v in values),
        "negative_count": sum(v < 0 for v in values),
        "zero_count": sum(v == 0 for v in values),
        "mean": _ratio(mean),
        "median": _ratio(median),
        "population_variance": _ratio(variance),
        "minimum": _ratio(ordered[0]) if count else None,
        "maximum": _ratio(ordered[-1]) if count else None,
        "range": _ratio(ordered[-1] - ordered[0]) if count else None,
        "unavailable_reason": None if count else "no_evaluated_returns",
    }


def _fraction(metric: dict[str, Any]) -> Fraction | None:
    if not metric["available"]:
        return None
    ratio = metric["exact_ratio"]
    return Fraction(int(ratio["numerator"]), int(ratio["denominator"]))


def _case(scenario: PaperScenario) -> dict[str, Any]:
    # The shared registry freezes BOTH variants' full action scripts. Pair on the
    # same exogenous market path/book/policy, not strategy-generated orders/fills.
    return {
        "version": "synthetic_paper_evaluation_case_v1",
        "market": scenario.market.value,
        "currency": scenario.currency.value,
        "initial_account": wire(scenario.initial_account),
        "initial_frame": wire(scenario.initial_frame),
        "market_frames": [
            wire(c.payload) for c in scenario.commands if isinstance(c.payload, PaperFrame)
        ],
        "execution_policy": wire(scenario.policy),
    }


def _scope(source: PerformanceInput) -> dict[str, Any]:
    exp, adapter = source.experiment, source.adapter
    require(type(adapter) is PaperSimulationAdapter, "paper_adapter_required")
    require(
        exp.runner == adapter.reference
        and exp.dataset == adapter.dataset_reference
        and exp.execution_policy == adapter.execution_policy
        and exp.data_origin == "synthetic",
        "comparison_adapter_binding",
    )
    # Independently reconstruct the existing immutable registry owner. This also
    # validates unused/failed-job inputs instead of trusting an old cached manifest.
    fresh = PaperSimulationAdapter(adapter.scenarios)
    require(
        exp.dataset == fresh.dataset_reference
        and exp.runner == fresh.reference
        and exp.execution_policy == fresh.execution_policy,
        "comparison_registry_pin_mismatch",
    )
    scenarios = {s.scenario_id: s for s in adapter.scenarios}
    mix: Counter[str] = Counter()
    cases = {}
    for job in exp.jobs:
        params = fields(job.parameters.to_dict(), "scenario_id scenario_hash")
        scenario = scenarios.get(params["scenario_id"])
        require(scenario is not None, "comparison_scenario_not_registered")
        assert scenario is not None
        require(
            params["scenario_hash"] == scenario.fingerprint
            and scenario.market is exp.market
            and scenario.currency is exp.currency,
            "comparison_scenario_binding",
        )
        case = _case(scenario)
        case_hash = digest(case)
        mix[case_hash] += 1
        cases[case_hash] = case
    return {
        "market": exp.market.value,
        "currency": exp.currency.value,
        "dataset": exp.dataset.to_dict(),
        "runner": exp.runner.to_dict(),
        "parameters": exp.parameters.to_dict(),
        "execution_policy": exp.execution_policy.to_dict(),
        "limits": exp.limits.to_dict(),
        "seed": exp.seed,
        "evaluation_case_counts": dict(sorted(mix.items())),
        "evaluation_cases": dict(sorted(cases.items())),
    }


def _input(
    source: PerformanceInput,
    policy: dict[str, Any],
    shared_marks: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, tuple[dict[str, Any], Fraction | None]]]:
    exp, adapter = source.experiment, source.adapter
    scheduler = BatchScheduler(
        exp, checkpoint=source.checkpoint, expected_checkpoint_hash=source.trusted_checkpoint_hash
    )
    records = scheduler.checkpoint().to_dict()["records"]
    latest = {r["job_key"]: (i, r) for i, r in enumerate(records)}
    require(
        type(source.valuations) is tuple and len(source.valuations) <= len(exp.jobs),
        "bounded_valuation_sources_required",
    )
    sources: dict[str, ValuationSource] = {}
    for value in source.valuations:
        require(type(value) is ValuationSource, "valuation_source_required")
        text(value.job_key)
        require(value.job_key not in sources, "duplicate_valuation_source")
        require(
            value.job_key in latest and latest[value.job_key][1]["status"] == "succeeded",
            "valuation_source_without_completed_job",
        )
        sources[value.job_key] = value
    rows = []
    values = []
    paired = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    for i, r in enumerate(records):
        histories.setdefault(r["job_key"], []).append(
            {
                "source": f"records[{i}]",
                "result_hash": r["result_hash"],
                "attempt": r["attempt"],
                "status": r["status"],
                "failure": r["failure"],
            }
        )
    cases = {s.scenario_id: digest(_case(s)) for s in adapter.scenarios}
    for job in exp.jobs:
        history = histories.get(job.key, [])
        case_hash = cases[job.parameters.to_dict()["scenario_id"]]
        observed = latest.get(job.key)
        record = observed[1] if observed is not None else None
        pair_id = digest(
            {"job_key": job.key, "evaluation_case_hash": case_hash, "seed": exp.run_seed(job)}
        )
        row: dict[str, Any] = {
            "pair_id": pair_id,
            "evaluation_case_hash": case_hash,
            "job_key": job.key,
            "run_id": exp.run_id(job),
            "scenario": job.parameters.to_dict(),
            "seed": exp.run_seed(job),
            "status": record["status"] if record is not None else "unexamined",
            "failure": record["failure"] if record is not None else None,
            "attempt_history": history,
            "valuation_fingerprint": None,
            "valuation_status": "unavailable",
            "return": None,
            "unavailable_reason": "job_not_completed",
        }
        performance = None
        if record is not None and record["status"] == "succeeded":
            output = FrozenJSON.freeze(record["output"])
            supplied = sources.get(job.key)
            if supplied is None:
                # A missing valuation is not permission to skip successful source validation.
                verify_paper_result(output, output.to_dict()["result_fingerprint"])
                adapter.replay_validate(exp, job, output, RunBudget(exp, Cancellation(), monotonic))
                row["unavailable_reason"] = "valuation_not_supplied"
            else:
                require(supplied.result == output, "valuation_checkpoint_output_mismatch")
                verify_valuation_report(
                    supplied.report,
                    supplied.trusted_report_hash,
                    exp,
                    job,
                    supplied.result,
                    supplied.trusted_result_fingerprint,
                    adapter,
                    supplied.evidence,
                    supplied.trusted_evidence_hash,
                )
                evidence = supplied.evidence
                require(
                    evidence.max_age_seconds == policy["max_age_seconds"]
                    and evidence.cash_flow_scope == policy["cash_flow_scope"],
                    "incompatible_valuation_policy",
                )
                v = supplied.report.to_dict()
                row["valuation_diagnostics"] = [
                    {
                        "source": f"{stage}.positions[{index}].market_value",
                        "reason": position["market_value"]["reason"],
                    }
                    for stage in ("opening", "closing")
                    for index, position in enumerate(v[stage]["positions"])
                    if not position["market_value"]["available"]
                ]
                performance = _fraction(v["performance"]["total_return"])
                if performance is not None:
                    # All included returns use the same independently pinned mark
                    # set, even for flat variants. Unavailable runs stay excluded.
                    marks = {
                        "opening": wire(evidence.opening_marks),
                        "closing": wire(evidence.closing_marks),
                    }
                    marks = {stage: sorted(items, key=digest) for stage, items in marks.items()}
                    require(
                        case_hash not in shared_marks or shared_marks[case_hash] == marks,
                        "incompatible_valuation_marks",
                    )
                    shared_marks[case_hash] = marks
                row.update(
                    valuation_fingerprint=v["content_fingerprint"],
                    valuation_status="evaluated" if performance is not None else "unavailable",
                    return_value=_ratio(performance),
                    unavailable_reason=v["performance"]["total_return"]["reason"],
                )
                row["return"] = row.pop("return_value")
                row["sources"] = {
                    "result_fingerprint": supplied.trusted_result_fingerprint,
                    "valuation_evidence_hash": supplied.trusted_evidence_hash,
                }
                if performance is not None:
                    values.append(performance)
        rows.append(row)
        paired[pair_id] = row, performance
    counts = Counter(r["status"] for r in rows)
    stats = _statistics(values, len(exp.jobs))
    stats.update(
        wins=stats["positive_count"], losses=stats["negative_count"], flat=stats["zero_count"]
    )
    return {
        "experiment_id": exp.experiment_id,
        "experiment_fingerprint": exp.fingerprint,
        "strategy": exp.strategy.to_dict(),
        "code": exp.code.to_dict(),
        "source_checkpoint_hash": source.trusted_checkpoint_hash,
        "action_script_counts": dict(
            sorted(Counter(job.parameters.to_dict()["scenario_hash"] for job in exp.jobs).items())
        ),
        "outcomes": {
            k: counts[k] for k in ("succeeded", "failed", "cancelled", "interrupted", "unexamined")
        },
        "unavailable_succeeded_count": counts["succeeded"] - len(values),
        "return_statistics": stats,
        "runs": rows,
    }, paired


def compare_performance(
    inputs: tuple[PerformanceInput, ...],
    valuation_policy: FrozenJSON,
    trusted_policy_hash: str,
    *,
    max_jobs: int,
) -> FrozenJSON:
    """Compare 2–5 pinned synthetic experiments; all work is explicit and bounded.

    Coverage includes every expected job. Statistics are conditional on available
    returns; paired differences use only the intersection, with exclusions retained.
    """
    require(type(inputs) is tuple and 2 <= len(inputs) <= 5, "comparison_input_count")
    require(all(type(i) is PerformanceInput for i in inputs), "performance_input_owners_required")
    integer(max_jobs, positive=True)
    require(sum(len(i.experiment.jobs) for i in inputs) <= max_jobs <= 300, "comparison_job_budget")
    sha(trusted_policy_hash)
    bounded_json(valuation_policy, 4096)
    require(valuation_policy.identifier == trusted_policy_hash, "comparison_policy_pin_mismatch")
    policy = fields(
        valuation_policy.to_dict(),
        "version cohort_id valuation_policy_version max_age_seconds cash_flow_scope",
    )
    require(
        policy["version"] == "simulation_performance_comparison_policy_v1"
        and policy["valuation_policy_version"] == POLICY,
        "unknown_comparison_policy",
    )
    require(len(text(policy["cohort_id"])) <= 128, "comparison_cohort_id_bound")
    require(integer(policy["max_age_seconds"]) <= 86400, "comparison_freshness_bound")
    require(
        policy["cash_flow_scope"] in {None, FLOW_SCOPE}, "unsupported_comparison_cash_flow_scope"
    )
    require(len({i.experiment.fingerprint for i in inputs}) == len(inputs), "duplicate_experiment")
    scopes = [_scope(i) for i in inputs]
    require(all(s == scopes[0] for s in scopes), "incompatible_evaluation_cohort")
    shared_marks: dict[str, dict[str, Any]] = {}
    verified = [_input(i, policy, shared_marks) for i in inputs]
    pairs = []
    for left_index, (left, left_runs) in enumerate(verified):
        for right, right_runs in verified[left_index + 1 :]:
            differences = []
            rows = []
            for pair_id in sorted(left_runs.keys() | right_runs.keys()):
                a, b = left_runs.get(pair_id), right_runs.get(pair_id)
                difference = (
                    b[1] - a[1]
                    if a is not None and b is not None and a[1] is not None and b[1] is not None
                    else None
                )
                if difference is not None:
                    differences.append(difference)
                rows.append(
                    {
                        "pair_id": pair_id,
                        "left": {
                            k: a[0][k]
                            for k in ("run_id", "status", "valuation_status", "unavailable_reason")
                        }
                        if a is not None
                        else None,
                        "right": {
                            k: b[0][k]
                            for k in ("run_id", "status", "valuation_status", "unavailable_reason")
                        }
                        if b is not None
                        else None,
                        "right_minus_left": _ratio(difference),
                        "status": "evaluated"
                        if difference is not None
                        else "unmatched"
                        if a is None or b is None
                        else "unavailable",
                    }
                )
            pairs.append(
                {
                    "left_experiment": left["experiment_fingerprint"],
                    "right_experiment": right["experiment_fingerprint"],
                    "difference_definition": "right_return_minus_left_return",
                    "unmatched_count": sum(r["status"] == "unmatched" for r in rows),
                    "unavailable_matched_count": sum(r["status"] == "unavailable" for r in rows),
                    "statistics": _statistics(differences, len(rows)),
                    "coverage_warning": None
                    if len(differences) == len(rows)
                    else "paired_subset_only",
                    "scenarios": rows,
                }
            )
    value = {
        "version": VERSION,
        "simulated": True,
        "usage_restriction": "synthetic_test_only",
        "cohort": scopes[0],
        "valuation_policy": valuation_policy.to_dict(),
        "trusted_policy_hash": trusted_policy_hash,
        "experiments": [v[0] for v in verified],
        "paired_comparisons": pairs,
        "limitations": [
            "conditional_on_evaluated_returns",
            "missing_outcomes_are_not_zero_returns",
            "population_variance_not_sample_estimate",
            "no_profitability_ranking_or_strategy_selection",
            "non_success_causes_pinned_record_only",
            "no_real_world_investment_performance_claim",
            "no_annualization_or_significance_tests",
            "explicit_variant_action_and_fill_scripts_not_a_liquidity_model",
        ],
    }
    value["content_fingerprint"] = digest(value)
    result = FrozenJSON.freeze(value)
    bounded_json(result, 4 * 1024 * 1024)
    return result


def verify_performance_comparison(
    report: FrozenJSON,
    trusted_report_hash: str,
    inputs: tuple[PerformanceInput, ...],
    valuation_policy: FrozenJSON,
    trusted_policy_hash: str,
    *,
    max_jobs: int,
) -> None:
    """Revalidate all original owners, pins and economic replay, including rehashed documents."""
    bounded_json(report, 4 * 1024 * 1024)
    sha(trusted_report_hash)
    value = report.to_dict()
    require(value.get("version") == VERSION, "unknown_performance_comparison_version")
    require(
        value.get("content_fingerprint")
        == trusted_report_hash
        == digest({k: v for k, v in value.items() if k != "content_fingerprint"}),
        "performance_comparison_hash_mismatch",
    )
    require(
        report
        == compare_performance(inputs, valuation_policy, trusted_policy_hash, max_jobs=max_jobs),
        "performance_comparison_replay_mismatch",
    )
