"""Research-only decision admission. Candidates never reserve assets or execute."""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import VirtualOrder
from donghak_stock_vision.backtest.data import PROVENANCE, FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.ledger import LedgerState, checkpoint, restore
from donghak_stock_vision.backtest.position_history import validate_position_history
from donghak_stock_vision.backtest.validation import fields, integer, money, utc, validate_run
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.decision import DecisionStore
from donghak_stock_vision.strategy.adapter import assemble
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy, DecisionResult
from donghak_stock_vision.strategy.engine import decide


@dataclass(frozen=True)
class DecisionBundle:
    inputs: DecisionInput
    policy: DecisionPolicy
    result: DecisionResult
    account_revision: int
    ledger_state_hash: str

    @classmethod
    def calculate(
        cls, inputs: DecisionInput, policy: DecisionPolicy, ledger: LedgerState
    ) -> "DecisionBundle":
        return cls(
            inputs, policy, decide(inputs, policy), ledger.to_dict()["revision"], ledger.identifier
        )

    @property
    def identifier(self) -> str:
        return digest(
            {
                "inputs": self.inputs.to_dict(),
                "policy": self.policy.to_dict(),
                "result": self.result.to_dict(),
                "revision": self.account_revision,
                "ledger": self.ledger_state_hash,
            }
        )


def prepare_decision(
    request: dict[str, Any],
    analysis_path: Path,
    account: dict[str, Any],
    orders: dict[str, Any],
    market: dict[str, Any],
    policy: DecisionPolicy,
    ledger: LedgerState,
    store: DecisionStore,
) -> DecisionBundle:
    """Explicit existing Phase 3 I/O boundary; admission below performs no I/O."""
    inputs = assemble(request, analysis_path, account, orders, market)
    bundle = DecisionBundle.calculate(inputs, policy, ledger)
    store.save(bundle.inputs, bundle.policy, bundle.result)
    return bundle


class AdmissionFailure(ValueError):
    def __init__(self, reason: str, status: str = "blocked") -> None:
        super().__init__(reason)
        self.status = status


def gate(condition: bool, reason: str, status: str = "blocked") -> None:
    if not condition:
        raise AdmissionFailure(reason, status)


def policy_values(raw: dict[str, Any] | None) -> dict[str, Any]:
    gate(raw is not None, "policy_missing")
    try:
        p = fields(
            raw,
            "version order_budget_krw max_order_quantity max_position_quantity "
            "max_position_notional_krw ordered_tickers marks max_delay_seconds expires_at",
        )
        gate(isinstance(p["version"], str) and bool(p["version"].strip()), "policy_invalid")
        p["order_budget_krw"] = money(p["order_budget_krw"], positive=True)
        p["max_order_quantity"] = integer(p["max_order_quantity"], positive=True)
        p["max_delay_seconds"] = integer(p["max_delay_seconds"])
        gate(
            isinstance(p["ordered_tickers"], list) and bool(p["ordered_tickers"]),
            "order_sequence_missing",
        )
        gate(len(set(p["ordered_tickers"])) == len(p["ordered_tickers"]), "policy_invalid")
        for name in ("max_position_quantity", "max_position_notional_krw", "marks"):
            gate(isinstance(p[name], dict), "policy_invalid")
        if p["expires_at"] is not None:
            utc(p["expires_at"])
        return p
    except (ValueError, KeyError, TypeError) as error:
        if isinstance(error, AdmissionFailure):
            raise
        raise AdmissionFailure("policy_incomplete_or_invalid") from error


def account_binding(
    bundle: DecisionBundle,
    ledger: LedgerState,
    market_items: list[dict[str, Any]],
    tape: FrozenTape,
    p: dict[str, Any],
    position_history: FrozenJSON | None = None,
) -> Decimal:
    b, s = bundle.inputs.to_dict(), ledger.to_dict()
    ticker = b["request"]["ticker"]
    gate(bundle.account_revision == s["revision"], "account_revision_conflict", "rejected")
    gate(bundle.ledger_state_hash == ledger.identifier, "account_state_conflict")
    gate(restore(checkpoint(ledger)).identifier == ledger.identifier, "ledger_integrity_failure")
    position = s["positions"].get(ticker, {"quantity": 0, "sellable_quantity": 0})
    account, orders = b["account"], b["orders"]
    for key in ("available_cash_krw", "reserved_cash_krw"):
        gate(Decimal(account[key]) == Decimal(s[key]), "account_snapshot_mismatch")
    for key in ("quantity", "sellable_quantity"):
        gate(account[key] == position[key], "account_snapshot_mismatch")
    gate(
        account["account_ref"] == orders["account_ref"] == s["account_id"],
        "account_snapshot_mismatch",
    )
    expected = sorted(
        (
            o["current"]["ticker"],
            "cancelled" if o["current"]["status"] == "expired" else o["current"]["status"],
            o["current"]["remaining_quantity"],
            Decimal(o["current"]["reserved_cash_krw"]),
        )
        for o in s["orders"].values()
    )
    observed = sorted(
        (o["ticker"], o["state"], o["remaining_quantity"], Decimal(o["reserved_cash_krw"]))
        for o in orders["items"]
    )
    gate(expected == observed, "orders_snapshot_mismatch")
    if position_history is None:
        gate(
            not s["fills"] and orders["history_complete"] is True and orders["last_exit"] is None,
            "exit_history_projection_unavailable",
        )
    else:
        try:
            verified = validate_position_history(
                position_history, ledger, tape, cutoff=b["request"]["decision_as_of"]
            )
        except (ValueError, KeyError, TypeError) as error:
            raise AdmissionFailure(f"position_history_invalid:{error}") from error
        gate(
            orders["history_complete"] is True
            and orders["last_exit"] == verified["last_exits"].get(ticker),
            "exit_history_snapshot_mismatch",
        )
    prices: dict[str, Decimal] = {}
    needed = {ticker} | {t for t, pos in s["positions"].items() if pos["quantity"]}
    for symbol in sorted(needed):
        gate(symbol in p["marks"], "valuation_evidence_missing")
        mark = fields(p["marks"][symbol], "sequence field")
        candidates = [
            e for e in market_items if e["ticker"] == symbol and e["sequence"] == mark["sequence"]
        ]
        gate(
            len(candidates) == 1 and mark["field"] in candidates[0]["public_fields"],
            "data_not_public",
        )
        gate(mark["field"] in {"open", "high", "low", "close", "price"}, "policy_invalid")
        e = candidates[0]
        prices[symbol] = Decimal(money(e["public_fields"][mark["field"]], positive=True))
        if symbol == ticker:
            market = b["market"]
            gate(
                Decimal(market["reference_price_krw"]) == prices[symbol]
                and market["adjustment"] == e["adjustment"],
                "market_snapshot_mismatch",
            )
            release = next(
                r
                for r in tape.release_plan.to_dict()["releases"]
                if r["sequence"] == mark["sequence"]
            )
            gate(
                utc(market["price_observed_at"]) == utc(release["field_times"][mark["field"]]),
                "price_time_mismatch",
            )
    gross = sum(prices[t] * pos["quantity"] for t, pos in s["positions"].items() if pos["quantity"])
    gate(
        Decimal(account["gross_exposure_krw"]) == gross
        and Decimal(account["position_exposure_krw"]) == prices[ticker] * position["quantity"]
        and Decimal(account["equity_krw"]) == Decimal(s["total_cash_krw"]) + gross
        and account["positions_count"]
        == sum(pos["quantity"] > 0 for pos in s["positions"].values()),
        "valuation_snapshot_mismatch",
    )
    return prices[ticker]


def admit(
    bundle: DecisionBundle,
    decision_id: str,
    ledger: LedgerState,
    tape: FrozenTape,
    analysis: FrozenAnalysis,
    decision_clock: VirtualClock,
    admission_clock: VirtualClock,
    policy: dict[str, Any] | None,
    history: FrozenJSON,
    position_history: FrozenJSON | None = None,
) -> tuple[FrozenJSON, FrozenJSON]:
    """Return immutable result and caller-held admission history; never change a ledger."""
    m, state, r = ledger.manifest.to_dict(), ledger.to_dict(), bundle.result.to_dict()
    entries = history.to_dict()
    base: dict[str, Any] = {
        "schema_version": 1,
        "decision_id": decision_id,
        "run_id": ledger.manifest.identifier,
        "account_revision": state["revision"],
        **{k: m[k] for k in PROVENANCE},
        "execution_mode": m["execution_mode"],
        "scope": "research",
        "operational_eligible": False,
        "executable": False,
        "virtual_order_eligible": False,
        "candidate": None,
        "budget_krw": None,
        "decision_diagnostics": r.get("diagnostics"),
        "phase3_blocking_reasons": r.get("blocking_reasons", []),
        "policy_hash": None,
        "decision_at": decision_clock.cutoff,
        "admission_at": admission_clock.cutoff,
    }
    try:
        prior = entries.get(decision_id)
        gate(
            prior is None or prior["bundle_hash"] == bundle.identifier,
            "decision_id_conflict",
            "rejected",
        )
        gate(decision_id == bundle.result.identifier, "decision_id_conflict", "rejected")
        gate(decide(bundle.inputs, bundle.policy) == bundle.result, "decision_result_mismatch")
        gate(r["operational_eligible"] is False and r["executable"] is False, "execution_forbidden")
        gate(r["decision_scope"] == "research", "operational_forbidden")
        upstream = r.get("blocking_reasons", [])
        upstream_reason = (
            "insufficient_cash"
            if "insufficient_cash" in upstream
            else "insufficient_quantity"
            if "insufficient_sellable_quantity" in upstream
            else "pending_order"
            if "pending_order" in upstream
            else "phase3_decision_blocked"
        )
        gate(r["status"] == "virtual" and r["decision_eligible"] is True, upstream_reason)
        gate(r["action"] in {"BUY", "SELL"}, "no_order_action", "rejected")
        p = policy_values(policy)
        base["policy_hash"] = digest(p)
        gate(utc(r["decision_as_of"]) == utc(decision_clock.cutoff), "decision_clock_mismatch")
        delay = (utc(admission_clock.cutoff) - utc(decision_clock.cutoff)).total_seconds()
        gate(
            0 <= delay <= p["max_delay_seconds"]
            and admission_clock.sequence >= decision_clock.sequence,
            "admission_time_invalid",
        )
        gate(utc(state["known_at"]) <= utc(decision_clock.cutoff), "future_account_state")
        gate(
            ledger.manifest == tape.manifest == analysis.manifest
            and ledger.tape_hash == tape.identifier,
            "run_or_tape_mismatch",
        )
        market_view = tape.view(decision_clock)
        signal_view = analysis.view(decision_clock)
        gate(
            signal_view.to_dict()["status"] != "blocked",
            "pit_evidence_missing"
            if m["information_mode"] == "point_in_time"
            else "analysis_not_public",
        )
        gate(m["information_mode"] == "historical_research", "pit_evidence_missing")
        gate(m["data_origin"] == "synthetic", "market_state_evidence_unavailable")
        frozen = analysis.bundle.to_dict()
        gate(
            bundle.inputs.to_dict()["analysis"] == frozen["signal"]
            and r["analysis_id"] == frozen["analysis_id"],
            "analysis_binding_mismatch",
        )
        for key in ("data_origin", "usage_restriction"):
            gate(r[key] == m[key], "origin_mismatch")
        gate(r["mode"] == m["information_mode"], "mode_mismatch")
        # Pending orders receive their own reason even if Phase 3 inputs omitted them.
        ticker = r["ticker"]
        gate(
            not any(o["current"]["status"] == "unknown" for o in state["orders"].values()),
            "orders_unknown",
        )
        gate(
            not any(
                o["current"]["ticker"] == ticker
                and o["current"]["status"] not in {"filled", "cancelled", "expired", "rejected"}
                for o in state["orders"].values()
            ),
            "pending_order",
            "rejected",
        )
        with localcontext(Context(prec=80)):
            price = account_binding(
                bundle, ledger, market_view.to_dict()["items"], tape, p, position_history
            )
            qty = integer(r["proposed_quantity"], positive=True)
            gate(qty <= p["max_order_quantity"], "order_quantity_limit", "rejected")
            gate(ticker in p["ordered_tickers"], "order_sequence_missing")
            rank = p["ordered_tickers"].index(ticker)
            attempt = digest(
                {
                    "bundle": bundle.identifier,
                    "state": ledger.identifier,
                    "policy": p,
                    "decision_clock": [decision_clock.cutoff, decision_clock.sequence],
                    "admission_clock": [admission_clock.cutoff, admission_clock.sequence],
                }
            )
            if prior and prior["attempt"] == attempt:
                return FrozenJSON.freeze(prior["result"]), history
            planned = [
                v["result"]
                for v in entries.values()
                if v["result"]["virtual_order_eligible"]
                and v["result"]["run_id"] == ledger.manifest.identifier
                and v["result"]["account_revision"] == state["revision"]
            ]
            gate(
                not prior or not prior["result"]["virtual_order_eligible"],
                "decision_already_admitted",
                "rejected",
            )
            gate(
                all(x["policy_hash"] == base["policy_hash"] for x in planned),
                "plan_policy_conflict",
            )
            gate(
                all(x["order_rank"] < rank for x in planned), "order_sequence_conflict", "rejected"
            )
            gate(
                all(x["candidate"]["ticker"] != ticker for x in planned),
                "duplicate_candidate",
                "rejected",
            )
            position = state["positions"].get(ticker, {"quantity": 0, "sellable_quantity": 0})
            notional = price * qty
            cap = Decimal(p["order_budget_krw"])
            gate(notional <= cap, "order_budget_limit", "rejected")
            if r["action"] == "BUY":
                gate(position["quantity"] == 0, "duplicate_buy", "rejected")
                cash = Decimal(money(r["estimated_cost"]["cash"], positive=True))
                gate(cash <= cap, "order_budget_limit", "rejected")
                planned_cash = sum(
                    Decimal(x["budget_krw"]) for x in planned if x["candidate"]["side"] == "BUY"
                )
                gate(
                    cash + planned_cash <= Decimal(state["available_cash_krw"]),
                    "insufficient_cash",
                    "rejected",
                )
                after = position["quantity"] + qty
            else:
                gate(qty <= position["sellable_quantity"], "insufficient_quantity", "rejected")
                cash = Decimal(0)
                after = position["quantity"] - qty
            gate(
                ticker in p["max_position_quantity"] and ticker in p["max_position_notional_krw"],
                "position_policy_missing",
            )
            gate(
                after <= integer(p["max_position_quantity"][ticker])
                and after * price <= Decimal(money(p["max_position_notional_krw"][ticker])),
                "position_limit",
                "rejected",
            )
            gate(
                p["expires_at"] is None or utc(p["expires_at"]) > utc(admission_clock.cutoff),
                "candidate_expired",
            )
            candidate = VirtualOrder.from_dict(
                {
                    "schema_version": 1,
                    "scope": "research",
                    "operational_eligible": False,
                    "executable": False,
                    **{k: m[k] for k in PROVENANCE},
                    "run_id": ledger.manifest.identifier,
                    "decision_id": decision_id,
                    "idempotency_key": digest(
                        {
                            "decision": decision_id,
                            "revision": state["revision"],
                            "policy": base["policy_hash"],
                        }
                    ),
                    "account_revision": state["revision"],
                    "ticker": ticker,
                    "side": r["action"],
                    "quantity": qty,
                    "remaining_quantity": qty,
                    "filled_quantity": 0,
                    "cancelled_quantity": 0,
                    "status": "submitted",
                    "submitted_at": admission_clock.cutoff,
                    "accepted_at": None,
                    "expires_at": p["expires_at"],
                    "cancelled_at": None,
                    "reserved_cash_krw": "0",
                    "reserved_quantity": 0,
                    "policy_id": base["policy_hash"],
                    "virtual": True,
                    "broker_route": None,
                }
            )
            validate_run(ledger.manifest, tape.policy, [candidate])
            base.update(
                status="admitted",
                reason="research_candidate",
                virtual_order_eligible=True,
                candidate=candidate.to_dict(),
                order_id=candidate.identifier,
                order_rank=rank,
                budget_krw=str(cash),
                reference_notional_krw=str(notional),
                market_view_hash=market_view.identifier,
                analysis_view_hash=signal_view.identifier,
            )
            entries[decision_id] = {
                "bundle_hash": bundle.identifier,
                "attempt": attempt,
                "result": base,
            }
            return FrozenJSON.freeze(base), FrozenJSON.freeze(entries)
    except AdmissionFailure as error:
        base.update(status=error.status, reason=str(error))
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
        base.update(status="blocked", reason="invalid_input_or_policy")
    return FrozenJSON.freeze(base), history
