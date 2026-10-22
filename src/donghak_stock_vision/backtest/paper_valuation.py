"""Explicit pinned synthetic valuation; execution and settlement remain owned by Paper Broker."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, Inexact, Rounded
from fractions import Fraction
from time import monotonic
from typing import Any, ClassVar

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import Cancellation, RunBudget
from donghak_stock_vision.backtest.paper_simulation import (
    PaperSimulationAdapter,
    verify_paper_result,
)
from donghak_stock_vision.backtest.simulation_spec import (
    ExperimentSpec,
    SimulationJob,
    bounded_json,
)
from donghak_stock_vision.backtest.validation import integer, require, sha, text, utc
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.paper_types import PaperFrame, exact, validate_owner, wire
from donghak_stock_vision.trading.risk import MarketQuote

VERSION = "paper_simulation_portfolio_valuation_v1"
POLICY = "paper_average_cost_exact_v1"
FLOW_SCOPE = "replayed_closed_paper_commands_v1"


@dataclass(frozen=True)
class ValuationEvidence:
    """Separately pinned price/policy evidence, not implicitly approved by construction.

    None cash_flow_scope deliberately withholds the closed-book return assertion.
    No caller can authorize arbitrary deposits/withdrawals through this interface.
    """

    evidence_id: str
    experiment_fingerprint: str
    run_id: str
    execution_fingerprint: str
    opening_at: datetime
    closing_at: datetime
    opening_marks: tuple[MarketQuote, ...]
    closing_marks: tuple[MarketQuote, ...]
    max_age_seconds: int
    cash_flow_scope: str | None
    version: ClassVar[str] = "synthetic_paper_valuation_evidence_v1"

    def __post_init__(self) -> None:
        text(self.evidence_id)
        for value in (self.experiment_fingerprint, self.run_id, self.execution_fingerprint):
            sha(value)
        object.__setattr__(self, "opening_at", utc(self.opening_at))
        object.__setattr__(self, "closing_at", utc(self.closing_at))
        require(self.opening_at <= self.closing_at, "valuation_time_order")
        integer(self.max_age_seconds)
        require(self.max_age_seconds <= 86400, "valuation_freshness_bound")
        require(self.cash_flow_scope in {None, FLOW_SCOPE}, "unsupported_external_cash_flows")
        for marks in (self.opening_marks, self.closing_marks):
            require(type(marks) is tuple and len(marks) <= 16, "bounded_immutable_marks_required")
            require(all(type(q) is MarketQuote for q in marks), "market_quote_required")
            require(len({q.instrument for q in marks}) == len(marks), "duplicate_valuation_mark")

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze({"version": self.version, "policy_version": POLICY, **wire(self)})


def _metric(value: Decimal | Fraction | None, reason: str | None = None) -> dict[str, Any]:
    """Exact rational allocation/ratios without inventing decimal rounding conventions."""
    if value is None:
        return {"available": False, "amount": None, "exact_ratio": None, "reason": reason}
    rational = Fraction(value)
    decimal: str | None = None
    try:
        with exact():
            decimal = wire(Decimal(rational.numerator) / Decimal(rational.denominator))
    except (Inexact, Rounded):
        pass  # Exact numerator/denominator remains available; no approximate money amount.
    return {
        "available": True,
        "amount": decimal,
        "exact_ratio": {
            "numerator": str(rational.numerator),
            "denominator": str(rational.denominator),
        },
        "reason": None,
        "decimal_unavailable_reason": None
        if decimal is not None
        else "nonterminating_or_precision_bound",
    }


def _return(pnl: Fraction | None, basis: Fraction | None, reason: str) -> dict[str, Any]:
    if pnl is None or basis is None:
        return _metric(None, reason)
    return _metric(pnl / basis) if basis > 0 else _metric(None, "nonpositive_return_denominator")


def _key(instrument: dict[str, Any]) -> str:
    return digest(instrument)  # Full exchange-qualified identity, never a ticker-only join.


def _snapshot(
    account: dict[str, Any], marks: tuple[MarketQuote, ...], at: datetime, age: int, stage: str
) -> tuple[dict[str, Any], Decimal | None, dict[str, Decimal | None]]:
    candidates = {_key(wire(q.instrument)): (i, q) for i, q in enumerate(marks)}
    cash = account["balances"][0]
    total_cash = Decimal(cash["available"]["amount"]) + Decimal(cash["reserved"]["amount"])
    marked: dict[str, Decimal | None] = {}
    positions: list[dict[str, Any]] = []
    total = total_cash
    for position in account["positions"]:
        quantity = position["quantity"]["shares"]
        if not quantity:
            continue
        key = _key(position["instrument"])
        candidate = candidates.get(key)
        price = value = None
        reason: str | None = "missing_mark"
        reference = None
        if candidate is not None:
            i, quote = candidate
            reference = {"source": f"valuation_evidence.{stage}_marks[{i}]", **wire(quote)}
            if quote.observed_at > at or quote.available_at > at:
                reason = "future_mark"
            elif at - quote.observed_at > timedelta(seconds=age):
                reason = "stale_mark"
            else:
                price = quote.price.amount
                value = price * quantity
                reason = None
                total += value
        marked[key] = value
        positions.append(
            {
                **position,
                "mark": reference,
                "market_value": _metric(value, reason),
            }
        )
    equity = total if all(v is not None for v in marked.values()) else None
    return (
        {
            "at": wire(at),
            "account_source_id": account["source_id"],
            "cash": {
                "available": cash["available"],
                "reserved": cash["reserved"],
                "total": wire(total_cash),
            },
            "positions": positions,
            "equity": _metric(equity, "position_marks_unavailable"),
        },
        equity,
        marked,
    )


def value_paper_simulation(
    experiment: ExperimentSpec,
    job: SimulationJob,
    result: FrozenJSON,
    trusted_result_fingerprint: str,
    adapter: PaperSimulationAdapter,
    evidence: ValuationEvidence,
    trusted_evidence_hash: str,
) -> FrozenJSON:
    """Verify/replay one completed job and project independently pinned native marks.

    Structural/provenance/replay errors raise. Missing/stale/future marks yield explicit
    unavailable metrics. No risk/account/order state or original result is changed.
    """
    require(type(adapter) is PaperSimulationAdapter, "paper_adapter_required")
    require(type(evidence) is ValuationEvidence, "valuation_evidence_required")
    validate_owner(evidence)
    sha(trusted_evidence_hash)
    require(
        evidence.document.identifier == trusted_evidence_hash, "valuation_evidence_pin_mismatch"
    )
    verify_paper_result(result, trusted_result_fingerprint)
    adapter.replay_validate(
        experiment, job, result, RunBudget(experiment, Cancellation(), monotonic)
    )
    document = result.to_dict()
    execution = document["execution"]
    require(execution["outcome"] == "completed", "completed_paper_result_required")
    require(
        evidence.experiment_fingerprint == experiment.fingerprint
        and evidence.run_id == experiment.run_id(job)
        and evidence.execution_fingerprint == document["execution_fingerprint"],
        "valuation_execution_binding_mismatch",
    )
    scenario = next(s for s in adapter.scenarios if s.reference() == execution["scenario"])
    final_frame = scenario.initial_frame
    for command in scenario.commands:
        if isinstance(command.payload, PaperFrame):
            final_frame = command.payload
    require(
        evidence.opening_at == scenario.initial_frame.now
        and evidence.closing_at == final_frame.now,
        "valuation_boundary_mismatch",
    )
    for quote in (*evidence.opening_marks, *evidence.closing_marks):
        require(
            quote.instrument in scenario.instruments
            and quote.instrument.market is scenario.market
            and quote.instrument.currency is scenario.currency,
            "valuation_instrument_mismatch",
        )
    with exact():
        return _project(experiment, job, result, evidence, trusted_evidence_hash)


def _project(
    experiment: ExperimentSpec,
    job: SimulationJob,
    result: FrozenJSON,
    evidence: ValuationEvidence,
    trusted_evidence_hash: str,
) -> FrozenJSON:
    document = result.to_dict()
    execution = document["execution"]
    flow_verified = evidence.cash_flow_scope == FLOW_SCOPE
    opening, initial_equity, _ = _snapshot(
        execution["starting_account"],
        evidence.opening_marks,
        evidence.opening_at,
        evidence.max_age_seconds,
        "opening",
    )
    closing, final_equity, terminal_values = _snapshot(
        execution["ending_account"],
        evidence.closing_marks,
        evidence.closing_at,
        evidence.max_age_seconds,
        "closing",
    )
    # This is a derived inventory-cost projection, never a second settlement ledger.
    # Initial Position has NO cost basis; a price mark cannot supply it.
    inventory: dict[str, tuple[int, Fraction | None]] = {
        _key(p["instrument"]): (
            p["quantity"]["shares"],
            None if p["quantity"]["shares"] else Fraction(0),
        )
        for p in execution["starting_account"]["positions"]
    }
    realized: Fraction | None = Fraction(0)
    disposed_basis: Fraction | None = Fraction(0)
    buy_gross = sell_gross = fees = taxes = Decimal(0)
    fill_rows = []
    for i, fill in enumerate(execution["fills"]):
        key = _key(fill["order"]["instrument"])
        shares, basis = inventory.get(key, (0, Fraction(0)))
        quantity = fill["quantity"]["shares"]
        gross = Decimal(fill["price"]["amount"]) * quantity
        charges = Decimal(fill["fees"]["amount"]) + Decimal(fill["taxes"]["amount"])
        fees += Decimal(fill["fees"]["amount"])
        taxes += Decimal(fill["taxes"]["amount"])
        allocated = pnl = None
        if fill["order"]["side"] == "BUY":
            buy_gross += gross
            basis = basis + Fraction(gross + charges) if basis is not None else None
            shares += quantity
        else:
            sell_gross += gross
            require(0 < quantity <= shares, "valuation_inventory_mismatch")
            allocated = basis * quantity / shares if basis is not None else None
            pnl = Fraction(gross - charges) - allocated if allocated is not None else None
            realized = realized + pnl if realized is not None and pnl is not None else None
            disposed_basis = (
                disposed_basis + allocated
                if disposed_basis is not None and allocated is not None
                else None
            )
            basis = basis - allocated if basis is not None and allocated is not None else None
            shares -= quantity
            if shares == 0:
                basis = Fraction(0)  # Only a fully exited unknown lot permits known reentry basis.
        inventory[key] = shares, basis
        fill_rows.append(
            {
                "fill_id": fill["fill_id"],
                "order_id": fill["order"]["order_id"],
                "source": f"execution.fills[{i}]",
                "source_hash": digest(fill),
                "receipt_references": [
                    {"source": f"execution.receipts[{ri}]", "source_hash": digest(receipt)}
                    for ri, receipt in enumerate(execution["receipts"])
                    if receipt["fill"] is not None and receipt["fill"]["fill_id"] == fill["fill_id"]
                ],
                "executed_at": fill["executed_at"],
                "available_at": fill["available_at"],
                "side": fill["order"]["side"],
                "quantity": quantity,
                "price": fill["price"],
                "gross_amount": wire(gross),
                "fees": fill["fees"],
                "taxes": fill["taxes"],
                "allocated_cost_basis": _metric(
                    allocated,
                    "not_a_sale"
                    if fill["order"]["side"] == "BUY"
                    else "initial_cost_basis_unavailable",
                ),
                "realized_pnl": _metric(
                    pnl,
                    "not_a_sale"
                    if allocated is None and fill["order"]["side"] == "BUY"
                    else "initial_cost_basis_unavailable",
                ),
            }
        )
    # Reconcile to authoritative snapshots and source costs without changing them.
    expected_quantities = {
        _key(p["instrument"]): p["quantity"]["shares"] for p in execution["ending_positions"]
    }
    require(
        all(shares == expected_quantities.get(k, 0) for k, (shares, _) in inventory.items())
        and all(inventory.get(k, (0, None))[0] == q for k, q in expected_quantities.items()),
        "valuation_ending_inventory_mismatch",
    )
    cash_delta = Decimal(closing["cash"]["total"]) - Decimal(opening["cash"]["total"])
    require(cash_delta == sell_gross - buy_gross - fees - taxes, "valuation_cash_flow_mismatch")
    require(
        fees == Decimal(execution["costs"]["fees"]["amount"])
        and taxes == Decimal(execution["costs"]["taxes"]["amount"]),
        "valuation_cost_mismatch",
    )
    unrealized: Fraction | None = Fraction(0)
    remaining_basis: Fraction | None = Fraction(0)
    for row in closing["positions"]:
        key = _key(row["instrument"])
        _, basis = inventory[key]
        mark_value = terminal_values[key]
        pnl = Fraction(mark_value) - basis if mark_value is not None and basis is not None else None
        unrealized = unrealized + pnl if unrealized is not None and pnl is not None else None
        remaining_basis = (
            remaining_basis + basis if remaining_basis is not None and basis is not None else None
        )
        row["cost_basis"] = _metric(basis, "initial_cost_basis_unavailable")
        row["unrealized_pnl"] = _metric(
            pnl, "initial_cost_basis_unavailable" if basis is None else "position_marks_unavailable"
        )
        row["unrealized_return"] = (
            _return(pnl, basis, "position_basis_or_mark_unavailable")
            if flow_verified
            else _metric(None, "external_cash_flows_unverified")
        )
    change = (
        final_equity - initial_equity
        if final_equity is not None and initial_equity is not None
        else None
    )
    total_pnl = Fraction(change) if change is not None and flow_verified else None
    total_reason = (
        "external_cash_flows_unverified"
        if not flow_verified
        else "opening_or_closing_marks_unavailable"
    )
    # No initial inventory implies exact reconciliation of all profit components.
    if (
        total_pnl is not None
        and realized is not None
        and unrealized is not None
        and not any(p["quantity"]["shares"] for p in execution["starting_account"]["positions"])
    ):
        require(total_pnl == realized + unrealized, "valuation_pnl_reconciliation_mismatch")
    value = {
        "version": VERSION,
        "policy_version": POLICY,
        "simulated": True,
        "usage_restriction": "synthetic_test_only",
        "currency": experiment.currency.value,
        "market": experiment.market.value,
        "experiment_id": experiment.experiment_id,
        "experiment_fingerprint": experiment.fingerprint,
        "run_id": experiment.run_id(job),
        "strategy": experiment.strategy.to_dict(),
        "dataset": experiment.dataset.to_dict(),
        "scenario": execution["scenario"],
        "sources": {
            "paper_result_fingerprint": document["result_fingerprint"],
            "execution_fingerprint": document["execution_fingerprint"],
            "paper_checkpoint": execution["paper_checkpoint"],
            "valuation_evidence_hash": trusted_evidence_hash,
            "valuation_evidence": evidence.document.to_dict(),
        },
        "verification": {
            "execution": "explicit_semantic_replay",
            "marks": "independently_pinned_synthetic_observations",
        },
        "opening": opening,
        "closing": closing,
        "fills": fill_rows,
        "execution_costs": execution["costs"],
        "executed_amounts": {"purchases_gross": wire(buy_gross), "sales_gross": wire(sell_gross)},
        "cash_flows": {
            "scope": evidence.cash_flow_scope,
            "verified": flow_verified,
            "net_external_flow": "0" if flow_verified else None,
            "reason": None if flow_verified else "external_cash_flows_unverified",
        },
        "performance": {
            "net_account_value_change": _metric(change, "opening_or_closing_marks_unavailable"),
            "total_pnl": _metric(total_pnl, total_reason),
            "total_return": _return(
                total_pnl,
                Fraction(initial_equity) if initial_equity is not None else None,
                total_reason,
            ),
            "realized_pnl": _metric(realized, "initial_cost_basis_unavailable"),
            "unrealized_pnl": _metric(unrealized, "position_basis_or_mark_unavailable"),
            "realized_return": (
                _return(realized, disposed_basis, "initial_cost_basis_unavailable")
                if flow_verified
                else _metric(None, "external_cash_flows_unverified")
            ),
            "unrealized_return": (
                _return(unrealized, remaining_basis, "position_basis_or_mark_unavailable")
                if flow_verified
                else _metric(None, "external_cash_flows_unverified")
            ),
        },
        "limitations": [
            "synthetic_not_real_investment_performance",
            "native_currency_only",
            "no_corporate_action_adjustments",
            "initial_position_basis_not_in_account_snapshot",
            "slippage_already_embedded_in_fill_prices",
            "no_hypothetical_exit_costs",
            "existing_diagnostic_v1_unchanged",
        ],
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, 1024 * 1024)
    return report


def verify_valuation_report(
    report: FrozenJSON,
    trusted_report_hash: str,
    experiment: ExperimentSpec,
    job: SimulationJob,
    result: FrozenJSON,
    trusted_result_fingerprint: str,
    adapter: PaperSimulationAdapter,
    evidence: ValuationEvidence,
    trusted_evidence_hash: str,
) -> None:
    """No hash-only or cached-owner shortcut for newly supplied valuation reports."""
    bounded_json(report, 1024 * 1024)
    sha(trusted_report_hash)
    value = report.to_dict()
    require(value.get("version") == VERSION, "unknown_valuation_report_version")
    require(
        value.get("content_fingerprint")
        == trusted_report_hash
        == digest({k: v for k, v in value.items() if k != "content_fingerprint"}),
        "valuation_report_hash_mismatch",
    )
    require(
        report
        == value_paper_simulation(
            experiment,
            job,
            result,
            trusted_result_fingerprint,
            adapter,
            evidence,
            trusted_evidence_hash,
        ),
        "valuation_report_replay_mismatch",
    )
