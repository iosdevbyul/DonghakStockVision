"""Read-only factual diagnostics over pinned checkpoints and explicitly replayed paper runs."""

from collections import Counter
from decimal import Decimal
from math import gcd
from time import monotonic
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import (
    BatchScheduler,
    Cancellation,
    FailureCode,
    RunBudget,
    SimulationFault,
)
from donghak_stock_vision.backtest.paper_simulation import (
    PaperSimulationAdapter,
    verify_paper_result,
)
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, bounded_json
from donghak_stock_vision.backtest.validation import integer, require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.paper_types import exact, wire
from donghak_stock_vision.trading.risk import RiskCode

VERSION = "simulation_diagnostic_report_v1"
_AUTHORITY = object()


class VerifiedDiagnosticReport:
    """Factory-owned immutable evidence projection, not a user-supplied performance summary.

    Only analyze_simulations produces these owners. This is a Python API boundary,
    not an authentication mechanism against arbitrary in-process code execution.
    """

    __slots__ = ("_document", "_pin", "_authority")
    _document: FrozenJSON
    _pin: str
    _authority: object

    def __init__(self, document: FrozenJSON, *, _authority: object = None) -> None:
        require(_authority is _AUTHORITY, "verified_diagnostic_factory_required")
        object.__setattr__(self, "_document", document)
        object.__setattr__(self, "_pin", document.identifier)
        object.__setattr__(self, "_authority", _authority)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("immutable_diagnostic_report")

    def to_dict(self) -> dict[str, Any]:
        require(self._authority is _AUTHORITY, "unverified_diagnostic_owner")
        require(self._document.identifier == self._pin, "diagnostic_owner_integrity_mismatch")
        result = self._document.to_dict()
        require(result["version"] == VERSION, "unknown_diagnostic_version")
        require(
            digest({k: v for k, v in result.items() if k != "content_fingerprint"})
            == result["content_fingerprint"],
            "diagnostic_fingerprint_mismatch",
        )
        return result

    @property
    def fingerprint(self) -> str:
        return str(self.to_dict()["content_fingerprint"])


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator or None,
        "unavailable_reason": None if denominator else "zero_denominator",
    }


def failure_category(code: FailureCode, detail: str) -> str:
    """Classify recorded codes, never infer an exception's economic cause."""
    if code is FailureCode.INCOMPATIBLE_INPUT:
        return {
            "invalid_paper_instruction": "invalid_trading_instruction",
            "scenario_binding_invalid": "invalid_scenario_input",
        }.get(detail, "unknown")
    return {
        FailureCode.INVALID_CONFIGURATION: "experiment_configuration_failure",
        FailureCode.MISSING_INPUT: "missing_input",
        FailureCode.EXECUTION_EXCEPTION: "simulation_adapter_failure",
        FailureCode.RISK_DENIED: "risk_denial",
        FailureCode.TIMEOUT: "orchestration_timeout",
        FailureCode.BUDGET_EXHAUSTED: "execution_budget_exhausted",
        FailureCode.CANCELLED: "orchestration_cancellation",
        FailureCode.INTERRUPTED: "orchestration_interruption",
        FailureCode.CHECKPOINT_MISMATCH: "result_integrity_failure",
        FailureCode.CHECKPOINT_CORRUPTION: "result_integrity_failure",
        FailureCode.RESULT_INTEGRITY: "result_integrity_failure",
    }.get(code, "unknown")


MARKET_CODES = {
    RiskCode.MISSING_PRICE.value,
    RiskCode.INVALID_PRICE.value,
    RiskCode.STALE_PRICE.value,
    "later_price_evidence_required",
    "session_evidence_unavailable",
    "market_closed_or_unknown",
}
CONSTRAINT_CODES = {
    "limit_not_marketable",
    "accepted_price_cap_exceeded",
    "invalid_execution_economics",
    "later_execution_event_required",
    "limit_price_quantum_mismatch",
    "native_currency_unsupported",
    "risk_evidence_missing",
    "risk_evidence_mismatch",
}
VALUATION_CODES = {
    RiskCode.MISSING_VALUATION.value,
    RiskCode.STALE_VALUATION.value,
    RiskCode.INVALID_VALUATION.value,
}


def observation_category(code: str) -> str:
    if code == RiskCode.FUTURE.value:
        return "information_availability_issue"
    if code in MARKET_CODES:
        return "market_observation_issue"
    if code in CONSTRAINT_CODES:
        return "execution_constraint_violation"
    if code in VALUATION_CODES:
        return "risk_valuation_evidence_issue"
    if code in {RiskCode.CASH.value, RiskCode.POSITION.value}:
        return "insufficient_buying_power_or_quantity"
    if code in {c.value for c in RiskCode}:
        return "risk_denial"
    return "unknown"


def analyze_simulations(
    experiment: ExperimentSpec,
    checkpoint: FrozenJSON,
    trusted_checkpoint_hash: str,
    adapter: PaperSimulationAdapter,
    *,
    max_jobs: int,
) -> VerifiedDiagnosticReport:
    """Explicit offline verification/replay, then factual projection. No resume/run here.

    Invalid/tampered/replay-inconsistent evidence raises and produces no trusted report.
    Non-success attempts are pinned recorded observations, not independently replayed causes.
    """
    integer(max_jobs, positive=True)
    require(max_jobs <= 300 and len(experiment.jobs) <= max_jobs, "diagnostic_job_budget")
    require(type(adapter) is PaperSimulationAdapter, "paper_adapter_required")
    require(
        experiment.runner == adapter.reference
        and experiment.dataset == adapter.dataset_reference
        and experiment.execution_policy == adapter.execution_policy,
        "diagnostic_adapter_binding",
    )
    require(experiment.data_origin == "synthetic", "synthetic_paper_evidence_required")
    verified = BatchScheduler(
        experiment, checkpoint=checkpoint, expected_checkpoint_hash=trusted_checkpoint_hash
    )
    records = verified.checkpoint().to_dict()["records"]
    latest: dict[str, dict[str, Any]] = {}
    indices: dict[str, int] = {}
    attempt_counts: Counter[str] = Counter()
    facts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    risk_codes: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    fees = taxes = slippage = Decimal(0)

    def fact(category: str, record: dict[str, Any], source: str, code: str) -> None:
        facts.append(
            {
                "category": category,
                "run_id": record["run_id"],
                "attempt": record["attempt"],
                "source": source,
                "code": code,
            }
        )

    for i, record in enumerate(records):
        latest[record["job_key"]] = record
        indices[record["job_key"]] = i
        attempt_counts[record["status"]] += 1
        if record["status"] != "succeeded":
            failure = record["failure"]
            fact(
                failure_category(FailureCode(failure["code"]), failure["detail"]),
                record,
                f"records[{i}].failure",
                failure["code"],
            )

    for job in experiment.jobs:
        record = latest.get(job.key)
        if record is None:
            continue
        row = {
            "job_key": job.key,
            "run_id": record["run_id"],
            "attempt": record["attempt"],
            "status": record["status"],
            "result_hash": record["result_hash"],
            "failure": record["failure"],
            "paper_result_fingerprint": None,
            "execution_fingerprint": None,
            "scenario": None,
        }
        rows.append(row)
        if record["status"] != "succeeded":
            continue
        result = FrozenJSON.freeze(record["output"])
        value = result.to_dict()
        try:
            verify_paper_result(result, value["result_fingerprint"])
            # The existing replay verifies all experiment/run/scenario/dataset/strategy
            # bindings and original broker risk, fills and conservation semantics.
            adapter.replay_validate(
                experiment, job, result, RunBudget(experiment, Cancellation(), monotonic)
            )
        except (ValueError, TypeError, KeyError) as error:
            raise SimulationFault(
                FailureCode.RESULT_INTEGRITY, "diagnostic_semantic_replay_failed"
            ) from error
        row.update(
            paper_result_fingerprint=value["result_fingerprint"],
            execution_fingerprint=value["execution_fingerprint"],
            scenario=value["execution"]["scenario"],
        )
        execution = value["execution"]
        index = indices[job.key]
        source = f"records[{index}].output.execution"
        for key in ("orders", "fills", "risk_denied_orders"):
            counts[key] += execution["counts"][key]
        for oi, order in enumerate(execution["orders"]):
            ref = f"{source}.orders[{oi}]"
            if order["status"] == "cancelled":
                counts["cancelled_orders"] += 1
                fact("order_cancellation", record, ref, "cancelled")
            filled, requested = order["filled"], order["intent"]["quantity"]["shares"]
            if filled > 0:
                counts["orders_with_fills"] += 1
            if 0 < filled < requested:
                counts["partially_filled_orders"] += 1
                fact("partial_fill", record, ref, "partially_filled_quantity")
            if order["accepted_at"] is not None and filled < requested:
                counts["unfilled_orders"] += 1
                fact("unfilled_order", record, ref, "accepted_quantity_not_fully_filled")
        for ri, receipt in enumerate(execution["receipts"]):
            ref = f"{source}.receipts[{ri}]"
            rejections = receipt["risk"]["rejections"] if receipt["risk"] is not None else []
            if receipt["risk"] is not None:
                counts["risk_evaluated_receipts"] += 1
            if rejections:
                counts["risk_rejected_receipts"] += 1
            seen = set()
            for qi, rejection in enumerate(rejections):
                code = rejection["code"]
                if code not in seen:
                    seen.add(code)
                    risk_codes[code] += 1
                    category = observation_category(code)
                    fact(category, record, f"{ref}.risk.rejections[{qi}]", code)
                    if category in {
                        "market_observation_issue",
                        "risk_valuation_evidence_issue",
                        "information_availability_issue",
                    }:
                        warnings[code] += 1
            for code in dict.fromkeys(receipt["reasons"]):
                if code in seen or code == "explicit_cancellation":
                    continue
                category = observation_category(code)
                fact(category, record, f"{ref}.reasons", code)
                warnings[code] += 1
        with exact():
            fees += Decimal(execution["costs"]["fees"]["amount"])
            taxes += Decimal(execution["costs"]["taxes"]["amount"])
            slippage += Decimal(execution["costs"]["slippage"]["amount"])
        fact("execution_cost_observation", record, f"{source}.costs", "receipt_derived_components")
        fact(
            "missing_performance_evidence",
            record,
            f"{source}.valuation",
            execution["valuation"]["reason"],
        )

    job_counts = Counter(r["status"] for r in latest.values())
    completed = job_counts["succeeded"]
    categories: dict[str, Any] = {}
    for category in sorted({f["category"] for f in facts}):
        relevant = [f for f in facts if f["category"] == category]
        categories[category] = {
            "observation_count": len(relevant),
            "affected_run_ids": sorted({f["run_id"] for f in relevant}),
        }
    mix = Counter(job.parameters.payload_json for job in experiment.jobs)
    scope = {
        "market": experiment.market.value,
        "currency": experiment.currency.value,
        "dataset": experiment.dataset.to_dict(),
        "parameters": experiment.parameters.to_dict(),
        "execution_policy_hash": experiment.execution_policy.identifier,
        "scenario_mix": [
            {
                "parameters": FrozenJSON(p).to_dict(),
                "numerator": n // gcd(n, len(experiment.jobs)),
                "denominator": len(experiment.jobs) // gcd(n, len(experiment.jobs)),
            }
            for p, n in sorted(mix.items())
        ],
    }
    value = {
        "version": VERSION,
        "simulated": True,
        "usage_restriction": "synthetic_test_only",
        "experiment_id": experiment.experiment_id,
        "experiment_fingerprint": experiment.fingerprint,
        "strategy": experiment.strategy.to_dict(),
        "code": experiment.code.to_dict(),
        "scope": scope,
        "source_checkpoint": {
            "version": BatchScheduler.version,
            "trusted_pin": trusted_checkpoint_hash,
        },
        "verification": {
            "checkpoint": "full_validation",
            "successful_outputs": "explicit_semantic_replay",
            "replayed_jobs": completed,
            "non_success_causes": "pinned_record_only",
        },
        "job_counts": {
            "total": len(experiment.jobs),
            "observed": len(latest),
            "completed": completed,
            "failed": job_counts["failed"],
            "cancelled": job_counts["cancelled"],
            "interrupted": job_counts["interrupted"],
            "unexamined": len(experiment.jobs) - len(latest),
        },
        "evidence_availability": {
            "semantically_verified_jobs": completed,
            "recorded_non_success_jobs": len(latest) - completed,
            "unexamined_jobs": len(experiment.jobs) - len(latest),
            "independent_terminal_valuation": False,
        },
        "attempt_counts": dict(sorted(attempt_counts.items())),
        "runs": rows,
        "categories": categories,
        "observations": facts,
        "risk_rejection_counts": dict(sorted(risk_codes.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "execution_counts": {
            k: counts[k]
            for k in (
                "orders",
                "fills",
                "risk_denied_orders",
                "orders_with_fills",
                "cancelled_orders",
                "partially_filled_orders",
                "unfilled_orders",
                "risk_evaluated_receipts",
                "risk_rejected_receipts",
            )
        },
        "rates": {
            "completion": _ratio(completed, len(experiment.jobs)),
            "failure": _ratio(job_counts["failed"], len(experiment.jobs)),
            "risk_rejection": _ratio(
                counts["risk_rejected_receipts"], counts["risk_evaluated_receipts"]
            ),
            "order_fill": _ratio(counts["orders_with_fills"], counts["orders"]),
            "order_cancellation": _ratio(counts["cancelled_orders"], counts["orders"]),
        },
        "execution_costs": {
            "currency": experiment.currency.value,
            "fees": wire(fees),
            "taxes": wire(taxes),
            "slippage": wire(slippage),
            "slippage_already_in_fill_prices": True,
            "coverage": "completed_jobs_only",
        },
        "performance": {
            "available": False,
            "pnl": None,
            "return": None,
            "reason": "independent_valuation_evidence_unavailable",
        },
        "limitations": [
            "synthetic_not_investment_performance",
            "no_strategy_ranking_or_promotion",
            "non_success_causes_not_independently_replayed",
            "costs_cover_completed_jobs_only",
            "observation_warnings_are_not_official_data_quality_approval",
        ],
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, 16 * 1024 * 1024)
    return VerifiedDiagnosticReport(report, _authority=_AUTHORITY)


def compare_diagnostics(reports: tuple[VerifiedDiagnosticReport, ...]) -> FrozenJSON:
    """Factual request-order comparison; incompatible scopes fail, no ranking or profit metric."""
    require(type(reports) is tuple and 2 <= len(reports) <= 5, "comparison_report_count")
    require(
        all(type(r) is VerifiedDiagnosticReport for r in reports),
        "verified_diagnostic_owners_required",
    )
    values = [r.to_dict() for r in reports]
    require(
        len({r["content_fingerprint"] for r in values}) == len(values),
        "duplicate_diagnostic_report",
    )
    require(all(r["scope"] == values[0]["scope"] for r in values), "incompatible_diagnostic_scopes")
    items = [
        {
            key: r[key]
            for key in (
                "experiment_id",
                "experiment_fingerprint",
                "strategy",
                "code",
                "content_fingerprint",
                "job_counts",
                "rates",
                "execution_counts",
                "execution_costs",
                "risk_rejection_counts",
                "warning_counts",
                "verification",
                "evidence_availability",
                "limitations",
                "performance",
            )
        }
        for r in values
    ]
    value = {
        "version": "simulation_diagnostic_comparison_v1",
        "scope": values[0]["scope"],
        "items": items,
        "notices": [
            "no_profitability_ranking",
            "rejection_frequency_is_not_strategy_quality",
            "costs_are_not_normalized_across_batch_sizes",
        ],
        "report_hashes": [r["content_fingerprint"] for r in values],
    }
    value["content_fingerprint"] = digest(value)
    return FrozenJSON.freeze(value)
