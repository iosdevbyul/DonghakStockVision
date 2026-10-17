"""Explicit account-level execution adapters over the existing ledger and validators.

No model scheduler, cash allocator or second economic ledger. The caller admits
and reserves sequentially against each current account revision.
"""

from decimal import Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import MarketEvent
from donghak_stock_vision.backtest.data import PROVENANCE, FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.execution import (
    acceptance_binding,
    command,
    execute,
    failure,
    result,
    settings,
    synthetic,
)
from donghak_stock_vision.backtest.historical_execution import metadata
from donghak_stock_vision.backtest.ledger import (
    LedgerState,
    apply,
    compare_account,
    initialize,
    reservation_prefix,
    restore,
)
from donghak_stock_vision.backtest.performance import policy_values, valuation
from donghak_stock_vision.backtest.position_history import build_position_history
from donghak_stock_vision.backtest.validation import require, utc
from donghak_stock_vision.data.learning import digest


def transition(expected_revision: int, expected_ledger_hash: str) -> FrozenJSON:
    """Caller-supplied optimistic concurrency token, checked again by execute()."""
    return FrozenJSON.freeze(
        {
            "version": "shared_account_execution_v1",
            "expected_revision": expected_revision,
            "expected_ledger_hash": expected_ledger_hash,
        }
    )


def execute_pending(
    ledger: LedgerState,
    tape: FrozenTape,
    requests: tuple[tuple[FrozenJSON, MarketEvent, VirtualClock], ...],
    *,
    expected_revision: int,
    expected_ledger_hash: str,
) -> tuple[tuple[FrozenJSON, ...], LedgerState]:
    """Finite explicit fill attempts, canonically ordered before any mutation.

    Each order appears at most once per batch. Rejections keep its reservation.
    Call release_pending explicitly to cancel/expire/reject an unchanged order.
    """
    compare_account(ledger, expected_revision, expected_ledger_hash)
    ids = [a.to_dict()["order_id"] for a, _, _ in requests]
    require(len(ids) == len(set(ids)), "duplicate_batch_order")

    def key(request: tuple[FrozenJSON, MarketEvent, VirtualClock]) -> tuple[Any, ...]:
        acceptance, event, clock = request
        e = event.to_dict()
        return (
            utc(clock.cutoff),
            utc(e["event_at"]),
            event.kind,
            e["ticker"],
            acceptance.to_dict()["order_id"],
            event.identifier,
        )

    outputs = []
    for acceptance, event, clock in sorted(requests, key=key):
        output, ledger = execute(
            acceptance,
            ledger,
            tape,
            event,
            clock,
            expected_revision=ledger.to_dict()["revision"],
            ledger_sequence=ledger.to_dict()["last_sequence"] + 1,
            quantity=acceptance.to_dict()["accepted_order"]["quantity"],
            account_transition=transition(ledger.to_dict()["revision"], ledger.identifier),
        )
        outputs.append(output)
    return tuple(outputs), ledger


def release_pending(
    acceptance: FrozenJSON,
    ledger: LedgerState,
    tape: FrozenTape,
    clock: VirtualClock,
    *,
    expected_revision: int,
    expected_ledger_hash: str,
    status: str,
) -> tuple[FrozenJSON, LedgerState]:
    """Explicit cancellation/expiry/rejection; no implicit refund on a failed fill."""
    try:
        compare_account(ledger, expected_revision, expected_ledger_hash)
        a = acceptance.to_dict()
        require(a["status"] == "accepted", "acceptance_required")
        p = settings(FrozenJSON.freeze(a["execution_policy"]))
        require(digest(p) == a["execution_policy_hash"], "execution_policy_hash_mismatch")
        synthetic(
            ledger,
            tape,
            p,
            FrozenJSON.freeze(a["historical_input"]) if "historical_input" in a else None,
        )
        reserved = restore(FrozenJSON.freeze(a["reservation_checkpoint"]))
        reservation_prefix(ledger, reserved, a["order_id"])
        order = acceptance_binding(a, ledger, tape, p, reserved)
        require(clock.sequence >= a["acceptance_clock"]["sequence"], "release_clock_reversed")
        require(utc(clock.cutoff) >= utc(a["acceptance_clock"]["cutoff"]), "release_clock_reversed")
        cmd = command(
            "release",
            digest(
                {
                    "release": order.identifier,
                    "state": ledger.identifier,
                    "status": status,
                    "clock": [clock.cutoff, clock.sequence],
                }
            ),
            order.identifier,
            ledger.to_dict()["last_sequence"] + 1,
            clock.cutoff,
            acceptance.identifier,
            {"status": status},
        )
        updated = apply(ledger, cmd, cutoff=clock.cutoff)
        return result(
            "released",
            **metadata(tape),
            version="shared_account_release_v1",
            release_status=status,
            acceptance_hash=acceptance.identifier,
            order_id=order.identifier,
            previous_ledger_hash=ledger.identifier,
            ledger_hash=updated.identifier,
            ledger_command=cmd.document.to_dict(),
        ), updated
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        return FrozenJSON.freeze({**failure(error).to_dict(), **metadata(tape)}), ledger


def account_view(
    ledger: LedgerState,
    tape: FrozenTape,
    evidence: tuple[FrozenJSON, ...],
    clock: VirtualClock,
    valuation_policy: FrozenJSON,
) -> FrozenJSON:
    """Existing history + marks, with per-ticker realized deltas from ledger replay."""
    history = build_position_history(ledger, tape, evidence, cutoff=clock.cutoff)
    state = initialize(
        ledger.manifest,
        ledger.tape_hash,
        ledger.policy,
        ledger.events[0],
        cutoff=ledger.events[0].document.to_dict()["known_at"],
    )
    realized: dict[str, Decimal] = {t: Decimal(0) for t in ledger.to_dict()["positions"]}
    with localcontext(Context(prec=80)):
        for event in ledger.events[1:]:
            before = Decimal(state.to_dict()["realized_pnl_krw"])
            state = apply(state, event, cutoff=event.document.to_dict()["known_at"])
            if event.document.to_dict()["kind"] == "fill":
                ticker = event.document.to_dict()["data"]["fill"]["ticker"]
                realized[ticker] = (
                    realized.get(ticker, Decimal(0))
                    + Decimal(state.to_dict()["realized_pnl_krw"])
                    - before
                )
        require(
            sum(realized.values()) == Decimal(ledger.to_dict()["realized_pnl_krw"]),
            "account_pnl_reconciliation",
        )
        marks = valuation(ledger, tape, clock, policy_values(valuation_policy), "account")
    return FrozenJSON.freeze(
        {
            "version": "shared_account_view_v1",
            "scope": "research",
            "virtual": True,
            "operational_eligible": False,
            "executable": False,
            **{k: tape.manifest.to_dict()[k] for k in PROVENANCE},
            "run_id": ledger.manifest.identifier,
            "ledger_hash": ledger.identifier,
            "history_hash": history.identifier,
            "account": ledger.to_dict(),
            "valuation": marks,
            "realized_pnl_by_ticker": {t: str(v) for t, v in sorted(realized.items())},
        }
    )
