"""Explicit synthetic full fills; all economic changes go through the public ledger."""

from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    Context,
    Decimal,
    localcontext,
)
from typing import Any

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import MarketEvent, VirtualFill, VirtualOrder
from donghak_stock_vision.backtest.data import PROVENANCE, FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.historical_execution import (
    MODE,
    metadata,
    next_session,
    validate_input,
)
from donghak_stock_vision.backtest.ledger import (
    LedgerCommand,
    LedgerState,
    apply,
    checkpoint,
    restore,
)
from donghak_stock_vision.backtest.validation import fields, integer, money, require, text, utc
from donghak_stock_vision.data.learning import digest

ROUNDING = {"half_up": ROUND_HALF_UP, "down": ROUND_DOWN, "up": ROUND_CEILING}


def settings(policy: FrozenJSON | None) -> dict[str, Any]:
    require(policy is not None, "execution_policy_missing")
    assert policy is not None
    p = fields(
        policy.to_dict(),
        "version scope fill_mode liquidity execution_price_field slippage_rate "
        "buy_fee_rate sell_fee_rate sell_tax_rate buy_tax price_quantum price_rounding "
        "cost_quantum cost_rounding",
    )
    text(p["version"])
    require(p["scope"] == "research", "real_trading_forbidden")
    require(p["fill_mode"] == "full", "partial_fill_unsupported")
    require(p["liquidity"] in {"synthetic_explicit_full_fill", MODE}, "liquidity_unproven")
    if p["liquidity"] == MODE:
        require(p["execution_price_field"] == "open", "historical_open_only")
    require(
        p["execution_price_field"] in {"open", "high", "low", "close"}, "unsupported_price_field"
    )
    require(p["buy_tax"] == "not_applicable", "unsupported_buy_tax")
    require(p["price_rounding"] == "adverse", "unsupported_price_rounding")
    require(p["cost_rounding"] in ROUNDING, "unsupported_cost_rounding")
    for key in ("slippage_rate", "buy_fee_rate", "sell_fee_rate", "sell_tax_rate"):
        p[key] = money(p[key])
        require(Decimal(p[key]) < 1, "rate_out_of_range")
    for key in ("price_quantum", "cost_quantum"):
        p[key] = money(p[key], positive=True)
        require(Decimal(p[key]).as_tuple().digits == (1,), "power_of_ten_quantum_required")
    return p


def result(status: str, **values: Any) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "schema_version": 1,
            "scope": "research",
            "virtual": True,
            "operational_eligible": False,
            "executable": False,
            "usage_restriction": "synthetic_test_only",
            "status": status,
            **values,
        }
    )


def failure(error: Exception) -> FrozenJSON:
    return result(
        "rejected",
        reason=str(error) if isinstance(error, ValueError) else "malformed_execution_input",
    )


def synthetic(
    ledger: LedgerState,
    tape: FrozenTape,
    policy: dict[str, Any],
    historical_input: FrozenJSON | None = None,
) -> None:
    m = ledger.manifest.to_dict()
    require(
        ledger.manifest == tape.manifest and ledger.tape_hash == tape.identifier,
        "run_or_tape_mismatch",
    )
    require(m["information_mode"] == "historical_research", "pit_evidence_missing")
    if policy["liquidity"] == MODE:
        validate_input(tape, historical_input)
    else:
        require(
            m["data_origin"] == "synthetic" and m["usage_restriction"] == "synthetic_test_only",
            "liquidity_unproven",
        )
    require(restore(checkpoint(ledger)).identifier == ledger.identifier, "ledger_integrity_failure")


def command(
    kind: str,
    event_id: str,
    order_id: str,
    sequence: int,
    stamp: str,
    source_id: str,
    data: dict[str, Any],
) -> LedgerCommand:
    return LedgerCommand(
        FrozenJSON.freeze(
            {
                "event_id": event_id,
                "kind": kind,
                "order_id": order_id,
                "sequence": sequence,
                "effective_at": stamp,
                "known_at": stamp,
                "source_id": source_id,
                "data": data,
            }
        )
    )


def acceptance_key(
    admission: FrozenJSON,
    policy: dict[str, Any],
    decision: VirtualClock,
    candidate: VirtualClock,
    accepted: VirtualClock,
) -> str:
    return digest(
        {
            "admission": admission.identifier,
            "policy": policy,
            "clocks": [[c.cutoff, c.sequence] for c in (decision, candidate, accepted)],
        }
    )


def accept_candidate(
    admission: FrozenJSON,
    ledger: LedgerState,
    tape: FrozenTape,
    decision_clock: VirtualClock,
    candidate_clock: VirtualClock,
    clock: VirtualClock,
    policy: FrozenJSON | None,
    reservation: FrozenJSON,
    *,
    ledger_sequence: int,
    historical_input: FrozenJSON | None = None,
) -> tuple[FrozenJSON, LedgerState]:
    """Explicit candidate -> open order -> ledger reservation, without any fill.

    Admission is the trusted immutable output of PR-D. All clocks and reservation
    bounds are supplied by the caller, never inferred from future prices.
    """
    try:
        p = settings(policy)
        synthetic(ledger, tape, p, historical_input)
        a = admission.to_dict()
        require(
            a["status"] == "admitted" and a["virtual_order_eligible"] is True,
            "candidate_not_admitted",
        )
        require(
            a["scope"] == "research"
            and a["executable"] is False
            and a["operational_eligible"] is False,
            "real_trading_forbidden",
        )
        if p["liquidity"] == MODE:
            require(
                a.get("historical_policy_hash") == digest(p), "historical_admission_policy_mismatch"
            )
            require(
                a.get("historical_input_hash") == validate_input(tape, historical_input).identifier,
                "historical_admission_evidence_mismatch",
            )
        candidate = VirtualOrder.from_dict(a["candidate"])
        o = candidate.to_dict()
        require(candidate.identifier == a["order_id"], "candidate_hash_mismatch")
        require(o["run_id"] == ledger.manifest.identifier == a["run_id"], "run_mismatch")
        for key in PROVENANCE:
            require(o[key] == ledger.manifest.to_dict()[key] == a[key], "mixed_provenance")
        require(
            o["account_revision"] == a["account_revision"] == ledger.to_dict()["revision"],
            "account_revision_mismatch",
        )
        require(
            o["status"] == "submitted" and o["accepted_at"] is None, "submitted_candidate_required"
        )
        require(
            utc(a["decision_at"]) == utc(decision_clock.cutoff)
            and utc(a["admission_at"]) == utc(candidate_clock.cutoff) == utc(o["submitted_at"]),
            "candidate_clock_mismatch",
        )
        require(
            decision_clock.sequence <= candidate_clock.sequence <= clock.sequence
            and utc(decision_clock.cutoff) <= utc(candidate_clock.cutoff) <= utc(clock.cutoff),
            "acceptance_time_reversed",
        )
        require(
            tape.view(decision_clock).identifier == a["market_view_hash"], "decision_view_mismatch"
        )
        require(utc(ledger.to_dict()["known_at"]) <= utc(clock.cutoff), "future_account_state")
        r = fields(reservation.to_dict(), "max_notional_krw max_cost_krw")
        for key in r:
            r[key] = money(r[key])
        with localcontext(Context(prec=80)):
            if o["side"] == "BUY":
                require(Decimal(r["max_notional_krw"]) > 0, "reservation_required")
                require(
                    Decimal(r["max_notional_krw"]) + Decimal(r["max_cost_krw"])
                    <= Decimal(a["budget_krw"]),
                    "admission_budget_exceeded",
                )
            else:
                require(all(Decimal(v) == 0 for v in r.values()), "sell_cash_reservation_forbidden")
        o.update(status="open", accepted_at=clock.cutoff, policy_id=ledger.policy.identifier)
        opened = VirtualOrder.from_dict(o)
        data = (
            {"order": opened.to_dict(), **r} if o["side"] == "BUY" else {"order": opened.to_dict()}
        )
        reserve = command(
            "reserve_buy" if o["side"] == "BUY" else "reserve_sell",
            acceptance_key(admission, p, decision_clock, candidate_clock, clock),
            opened.identifier,
            ledger_sequence,
            clock.cutoff,
            o["decision_id"],
            data,
        )
        reserved = apply(ledger, reserve, cutoff=clock.cutoff)
        return result(
            "accepted",
            candidate_id=candidate.identifier,
            admission=admission.to_dict(),
            accepted_order=opened.to_dict(),
            order_id=opened.identifier,
            execution_policy=p,
            execution_policy_hash=digest(p),
            decision_clock={"cutoff": decision_clock.cutoff, "sequence": decision_clock.sequence},
            candidate_clock={
                "cutoff": candidate_clock.cutoff,
                "sequence": candidate_clock.sequence,
            },
            acceptance_clock={"cutoff": clock.cutoff, "sequence": clock.sequence},
            reservation_checkpoint=checkpoint(reserved).to_dict(),
            **(
                {"historical_input": historical_input.to_dict(), **metadata(tape)}
                if historical_input is not None
                else {}
            ),
        ), reserved
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        return FrozenJSON.freeze({**failure(error).to_dict(), **metadata(tape)}), ledger


def execute(
    acceptance: FrozenJSON,
    ledger: LedgerState,
    tape: FrozenTape,
    event: MarketEvent,
    clock: VirtualClock,
    *,
    expected_revision: int,
    ledger_sequence: int,
    quantity: int,
) -> tuple[FrozenJSON, LedgerState]:
    """Full fill at one explicitly selected future public event; no runner or I/O."""
    try:
        a = acceptance.to_dict()
        require(a["status"] == "accepted", "acceptance_required")
        require(
            a["scope"] == "research"
            and a["executable"] is False
            and a["operational_eligible"] is False,
            "real_trading_forbidden",
        )
        p = settings(FrozenJSON.freeze(a["execution_policy"]))
        require(digest(p) == a["execution_policy_hash"], "execution_policy_hash_mismatch")
        synthetic(
            ledger,
            tape,
            p,
            FrozenJSON.freeze(a["historical_input"]) if "historical_input" in a else None,
        )
        if p["liquidity"] == MODE:
            evidence = validate_input(tape, FrozenJSON.freeze(a["historical_input"]))
            require(
                a["admission"].get("historical_policy_hash") == digest(p)
                and a["admission"].get("historical_input_hash") == evidence.identifier,
                "historical_acceptance_evidence_mismatch",
            )
        reserved = restore(FrozenJSON.freeze(a["reservation_checkpoint"]))
        require(
            reserved.manifest == ledger.manifest and reserved.tape_hash == tape.identifier,
            "reservation_run_mismatch",
        )
        require(
            integer(expected_revision) == reserved.to_dict()["revision"],
            "account_revision_mismatch",
        )
        order = VirtualOrder.from_dict(a["accepted_order"])
        require(order.identifier == a["order_id"], "accepted_order_hash_mismatch")
        require(
            reserved.to_dict()["orders"][order.identifier]["original"] == order.to_dict(),
            "reservation_mismatch",
        )
        reservation_event = reserved.events[-1].document.to_dict()
        require(
            reservation_event["event_id"]
            == acceptance_key(
                FrozenJSON.freeze(a["admission"]),
                p,
                VirtualClock(**a["decision_clock"]),
                VirtualClock(**a["candidate_clock"]),
                VirtualClock(**a["acceptance_clock"]),
            )
            and reservation_event["order_id"] == order.identifier,
            "acceptance_policy_binding_mismatch",
        )
        original_candidate = VirtualOrder.from_dict(a["admission"]["candidate"])
        require(original_candidate.identifier == a["candidate_id"], "candidate_hash_mismatch")
        projected = original_candidate.to_dict()
        projected.update(
            status="open",
            accepted_at=a["acceptance_clock"]["cutoff"],
            policy_id=ledger.policy.identifier,
        )
        require(VirtualOrder.from_dict(projected) == order, "candidate_order_binding_mismatch")
        require(
            utc(a["decision_clock"]["cutoff"]) == utc(a["admission"]["decision_at"])
            and utc(a["candidate_clock"]["cutoff"]) == utc(a["admission"]["admission_at"]),
            "candidate_clock_mismatch",
        )
        require(
            tape.view(VirtualClock(**a["decision_clock"])).identifier
            == a["admission"]["market_view_hash"],
            "decision_view_mismatch",
        )
        o, e = order.to_dict(), event.to_dict()
        n = integer(quantity, positive=True)
        require(n == o["quantity"], "partial_fill_unsupported")
        require(any(x == event for x in tape.events), "event_not_in_tape")
        require(e["ticker"] == o["ticker"], "event_ticker_mismatch")
        for key in ("decision_clock", "candidate_clock", "acceptance_clock"):
            prior = VirtualClock(**a[key])
            require(e["sequence"] > prior.sequence, "same_or_past_event")
            require(utc(e["event_at"]) > utc(prior.cutoff), "same_or_past_event_time")
        require(clock.sequence == e["sequence"], "execution_sequence_mismatch")
        require(utc(e["event_at"]) <= utc(clock.cutoff), "future_market_event")
        view = tape.view(clock)
        items = [x for x in view.to_dict()["items"] if x["sequence"] == e["sequence"]]
        field = p["execution_price_field"]
        require(len(items) == 1 and field in items[0]["public_fields"], "price_not_public")
        release = next(
            x for x in tape.release_plan.to_dict()["releases"] if x["sequence"] == e["sequence"]
        )
        require(release["previous_revision"] is None, "revision_not_execution_event")
        if p["liquidity"] == MODE:
            next_session(
                tape,
                o["ticker"],
                VirtualClock(**a["decision_clock"]),
                VirtualClock(**a["acceptance_clock"]),
                e,
                clock,
            )
        require(
            p["liquidity"] == MODE
            or (
                e["quality_status"] == "verified"
                and not e["quality_flags"]
                and e["adjustment"] == "unadjusted"
                and e["session"] == "open"
                and e["halt_status"] == "trading"
                and e["listing_status"] == "listed"
            ),
            "market_quality_blocked",
        )
        require(utc(e["quality_available_at"]) <= utc(clock.cutoff), "quality_not_public")
        benchmark = Decimal(money(items[0]["public_fields"][field], positive=True))
        with localcontext(Context(prec=80)):
            slip = Decimal(p["slippage_rate"])
            price = (benchmark * (1 + slip if o["side"] == "BUY" else 1 - slip)).quantize(
                Decimal(p["price_quantum"]),
                rounding=ROUND_CEILING if o["side"] == "BUY" else ROUND_FLOOR,
            )
            require(price > 0, "nonpositive_execution_price")
            notional = price * n
            rounding = ROUNDING[p["cost_rounding"]]
            quantum = Decimal(p["cost_quantum"])
            fee = (
                notional * Decimal(p["buy_fee_rate"] if o["side"] == "BUY" else p["sell_fee_rate"])
            ).quantize(quantum, rounding=rounding)
            tax = (
                (notional * Decimal(p["sell_tax_rate"])).quantize(quantum, rounding=rounding)
                if o["side"] == "SELL"
                else Decimal(0)
            )
            slippage = abs(price - benchmark) * n
        fill_id = digest({"full_fill": order.identifier})
        fill = VirtualFill.from_dict(
            {
                "schema_version": 1,
                "scope": "research",
                "operational_eligible": False,
                "executable": False,
                **{k: o[k] for k in PROVENANCE},
                "run_id": o["run_id"],
                "order_id": order.identifier,
                "sequence": ledger_sequence,
                "ticker": o["ticker"],
                "quantity": n,
                "price_krw": str(price),
                "notional_krw": str(notional),
                "fee_krw": str(fee),
                "tax_krw": str(tax),
                "benchmark_price_krw": str(benchmark),
                "slippage_krw": str(slippage),
                "fill_at": clock.cutoff,
                "fill_known_at": clock.cutoff,
                "source_event_id": event.identifier,
                "liquidity_evidence_id": digest(p),
                "policy_id": ledger.policy.identifier,
                "cost_charge_id": fill_id,
                "virtual": True,
                "simulation_only": True,
                "broker_route": None,
            }
        )
        cmd = command(
            "fill",
            fill_id,
            order.identifier,
            ledger_sequence,
            clock.cutoff,
            event.identifier,
            {"fill": fill.to_dict()},
        )
        previous = [x for x in ledger.events if x.document.to_dict()["event_id"] == fill_id]
        if previous:
            require(previous == [cmd], "fill_identifier_conflict")
        else:
            require(ledger.identifier == reserved.identifier, "account_revision_mismatch")
        updated = apply(ledger, cmd, cutoff=clock.cutoff)
        return result(
            "filled",
            fill_id=fill_id,
            fill_hash=fill.identifier,
            fill=fill.to_dict(),
            ledger_command=cmd.document.to_dict(),
            execution_policy_hash=digest(p),
            market_view_hash=view.identifier,
            market_sequence=e["sequence"],
            candidate_id=a["candidate_id"],
            order_id=order.identifier,
            **(
                {
                    **metadata(tape),
                    "execution_session": e["trading_date"],
                    "source_open": items[0]["public_fields"]["open"],
                    "slippage_rate": p["slippage_rate"],
                }
                if p["liquidity"] == MODE
                else {}
            ),
        ), updated
    except (ValueError, KeyError, TypeError, ArithmeticError, StopIteration) as error:
        return FrozenJSON.freeze({**failure(error).to_dict(), **metadata(tape)}), ledger
