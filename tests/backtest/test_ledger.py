"""Explicit synthetic accounting examples; no trading recommendations or fills inferred."""

from decimal import localcontext
from typing import Any

import pytest

from donghak_stock_vision.backtest.contracts import RunManifest, VirtualFill, VirtualOrder
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.ledger import (
    LedgerCommand,
    LedgerPolicy,
    LedgerState,
    apply,
    checkpoint,
    initialize,
    restore,
)
from tests.backtest.helpers import H, T, manifest, payload


def policy(**changes: Any) -> LedgerPolicy:
    return LedgerPolicy(
        FrozenJSON.freeze(
            {
                "version": "fixture",
                "currency": "KRW",
                "cost_method": "average",
                "settlement": "immediate_virtual",
                "costs": "supplied_fill_buy_capitalize_sell_expense",
                "cost_quantum": "0.01",
                "cost_rounding": "half_up",
                "reservation_shortfall": "reject",
                "expiry_boundary": "exclusive",
                **changes,
            }
        )
    )


def command(
    kind: str,
    seq: int,
    data: dict[str, Any],
    order_id: str | None = None,
    event_id: str | None = None,
    effective: str = T,
    known: str = T,
) -> LedgerCommand:
    return LedgerCommand(
        FrozenJSON.freeze(
            {
                "event_id": event_id or f"event-{seq}",
                "kind": kind,
                "sequence": seq,
                "order_id": order_id,
                "effective_at": effective,
                "known_at": known,
                "source_id": H,
                "data": data,
            }
        )
    )


def initial(cash: str = "1000", held: int = 0, basis: str = "0") -> LedgerState:
    m = manifest().to_dict()
    m["initial_account"]["available_cash_krw"] = cash
    m["initial_account"]["positions"] = [
        {
            "ticker": "005930",
            "quantity": held,
            "sellable_quantity": held,
            "reserved_quantity": 0,
            "cost_basis_krw": basis,
        }
    ]
    return initialize(RunManifest.from_dict(m), H, policy(), command("initialize", 0, {}), cutoff=T)


def order(
    state: LedgerState,
    side: str = "BUY",
    quantity: int = 4,
    ticker: str = "005930",
    key: str = "order-1",
) -> VirtualOrder:
    o = payload(VirtualOrder)
    o.update(
        run_id=state.manifest.identifier,
        policy_id=state.policy.identifier,
        side=side,
        quantity=quantity,
        remaining_quantity=quantity,
        status="open",
        accepted_at=T,
        account_revision=state.to_dict()["revision"],
        ticker=ticker,
        idempotency_key=key,
    )
    return VirtualOrder.from_dict(o)


def reserve_event(o: VirtualOrder, seq: int, cash: str = "440") -> LedgerCommand:
    d = o.to_dict()
    return command(
        "reserve_buy" if d["side"] == "BUY" else "reserve_sell",
        seq,
        {"order": d, "max_notional_krw": cash, "max_cost_krw": "0"}
        if d["side"] == "BUY"
        else {"order": d},
        o.identifier,
    )


def fill_event(
    state: LedgerState,
    o: VirtualOrder,
    seq: int,
    n: int = 2,
    price: str = "100",
    fee: str = "2",
    known: str = T,
    effective: str = T,
) -> LedgerCommand:
    from decimal import Decimal

    f = payload(VirtualFill)
    f.update(
        run_id=state.manifest.identifier,
        order_id=o.identifier,
        sequence=seq,
        policy_id=state.policy.identifier,
        quantity=n,
        price_krw=price,
        notional_krw=str(Decimal(price) * n),
        fee_krw=fee,
        tax_krw="0",
        cost_charge_id=f"{seq:064x}",
        ticker=o.to_dict()["ticker"],
        fill_known_at=known,
        fill_at=effective,
    )
    return command(
        "fill",
        seq,
        {"fill": VirtualFill.from_dict(f).to_dict()},
        o.identifier,
        effective=effective,
        known=known,
    )


def test_initial_cash_and_basis_are_not_double_counted() -> None:
    s = initial("1000", 5, "450").to_dict()
    assert s["total_cash_krw"] == s["available_cash_krw"] == "1000"
    assert s["positions"]["005930"]["cost_basis_krw"] == "450"
    assert s["realized_pnl_krw"] == "0"
    assert s["executable"] is False and s["operational_eligible"] is False


@pytest.mark.parametrize("cash", ["-1", "NaN", "Infinity", "-Infinity"])
def test_bad_initial_cash(cash: str) -> None:
    with pytest.raises(ValueError):
        initial(cash)


@pytest.mark.parametrize("held,basis", [(-1, "10"), (1.5, "10"), (True, "10"), (0, "1"), (2, "-1")])
def test_bad_initial_positions(held: Any, basis: str) -> None:
    with pytest.raises(ValueError):
        initial(held=held, basis=basis)


@pytest.mark.parametrize(
    "key,value",
    [
        ("currency", "USD"),
        ("cost_method", "fifo"),
        ("settlement", "T+2"),
        ("costs", None),
        ("cost_quantum", "0.03"),
        ("cost_rounding", "guess"),
    ],
)
def test_unsupported_policy(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        policy(**{key: value})


@pytest.mark.parametrize("key", ["costs", "settlement", "cost_method", "cost_rounding"])
def test_missing_policy(key: str) -> None:
    p = policy().document.to_dict()
    del p[key]
    with pytest.raises(ValueError):
        LedgerPolicy(FrozenJSON.freeze(p))


def test_cash_reserve_amend_release_no_economic_fill() -> None:
    s = initial()
    o = order(s)
    reserved = apply(s, reserve_event(o, 1), cutoff=T)
    assert reserved.to_dict()["available_cash_krw"] == "560"
    amended = apply(
        reserved,
        command("amend_cash", 2, {"max_notional_krw": "490", "max_cost_krw": "10"}, o.identifier),
        cutoff=T,
    )
    assert amended.to_dict()["available_cash_krw"] == "500"
    released = apply(
        amended, command("release", 3, {"status": "cancelled"}, o.identifier), cutoff=T
    )
    assert released.to_dict()["total_cash_krw"] == "1000"
    assert released.to_dict()["reserved_cash_krw"] == "0"
    assert released.to_dict()["positions"] == s.to_dict()["positions"]
    assert released.to_dict()["realized_pnl_krw"] == "0"


def test_cash_cannot_be_reserved_twice_and_failure_is_immutable() -> None:
    s = initial()
    o = order(s)
    s = apply(s, reserve_event(o, 1, "800"), cutoff=T)
    before = checkpoint(s)
    other = order(s, ticker="000001", key="other")
    with pytest.raises(ValueError, match="insufficient_cash"):
        apply(s, reserve_event(other, 2, "300"), cutoff=T)
    assert checkpoint(s) == before
    with pytest.raises(ValueError, match="duplicate_order"):
        apply(s, reserve_event(o, 2), cutoff=T)


def test_sell_reserve_cannot_overbook_and_partial_keeps_reservation() -> None:
    s = initial(held=10, basis="900")
    o = order(s, "SELL", 6)
    s = apply(s, reserve_event(o, 1), cutoff=T)
    assert s.to_dict()["positions"]["005930"]["sellable_quantity"] == 4
    other = order(s, "SELL", 5, key="second")
    with pytest.raises(ValueError, match="insufficient_shares"):
        apply(s, reserve_event(other, 2), cutoff=T)
    s = apply(s, fill_event(s, o, 2), cutoff=T)
    p = s.to_dict()["positions"]["005930"]
    assert (p["quantity"], p["sellable_quantity"], p["reserved_quantity"]) == (8, 4, 4)
    assert p["cost_basis_krw"] == "720"
    assert s.to_dict()["realized_pnl_krw"] == "18"
    assert s.to_dict()["available_cash_krw"] == "1198"
    s = apply(s, command("release", 3, {"status": "cancelled"}, o.identifier), cutoff=T)
    assert s.to_dict()["positions"]["005930"]["sellable_quantity"] == 8


def test_buy_partial_costs_and_slippage_not_charged_twice() -> None:
    s = initial()
    o = order(s)
    s = apply(s, reserve_event(o, 1), cutoff=T)
    first = fill_event(s, o, 2)
    s = apply(s, first, cutoff=T)
    assert s.to_dict()["reserved_cash_krw"] == "238"
    assert s.to_dict()["available_cash_krw"] == "560"
    assert s.to_dict()["positions"]["005930"]["cost_basis_krw"] == "202"
    s = apply(s, fill_event(s, o, 3), cutoff=T)
    assert s.to_dict()["reserved_cash_krw"] == "0"
    assert s.to_dict()["available_cash_krw"] == "596"
    assert s.to_dict()["positions"]["005930"]["quantity"] == 4
    assert s.to_dict()["orders"][o.identifier]["current"]["status"] == "filled"


def test_event_and_fill_idempotency_and_event_conflict() -> None:
    s = initial()
    o = order(s)
    reserve = reserve_event(o, 1)
    s = apply(s, reserve, cutoff=T)
    assert apply(s, reserve, cutoff=T) is s
    f = fill_event(s, o, 2)
    s = apply(s, f, cutoff=T)
    assert apply(s, f, cutoff=T) is s
    repeated = f.document.to_dict()
    repeated["event_id"] = "redelivery"
    alias = LedgerCommand(FrozenJSON.freeze(repeated))
    redelivered = apply(s, alias, cutoff=T)
    assert redelivered.to_dict() == s.to_dict()
    assert apply(redelivered, alias, cutoff=T) is redelivered
    assert restore(checkpoint(redelivered)).identifier == redelivered.identifier
    repeated["data"]["fill"]["fee_krw"] = "3"
    with pytest.raises(ValueError, match="event_id_conflict"):
        apply(redelivered, LedgerCommand(FrozenJSON.freeze(repeated)), cutoff=T)
    changed = f.document.to_dict()
    changed["data"]["fill"]["fee_krw"] = "3"
    with pytest.raises(ValueError, match="event_id_conflict"):
        apply(s, LedgerCommand(FrozenJSON.freeze(changed)), cutoff=T)


def test_fill_known_at_gate_and_time_reversal() -> None:
    s = initial()
    o = order(s)
    s = apply(s, reserve_event(o, 1), cutoff=T)
    later = "2026-01-05T01:00:00Z"
    f = fill_event(s, o, 2, known=later)
    with pytest.raises(ValueError, match="event_not_known"):
        apply(s, f, cutoff=T)
    advanced = apply(s, f, cutoff=later)
    with pytest.raises(ValueError, match="known_time_reversed"):
        apply(advanced, command("release", 3, {"status": "cancelled"}, o.identifier), cutoff=later)
    with pytest.raises(ValueError, match="sequence_reversed"):
        apply(
            s,
            command("release", 0, {"status": "cancelled"}, o.identifier, event_id="late"),
            cutoff=T,
        )


@pytest.mark.parametrize(
    "case", ["overfill", "cash", "notional", "cost", "negative", "unknown", "terminal"]
)
def test_fill_failure_preserves_state(case: str) -> None:
    s = initial()
    o = order(s)
    s = apply(s, reserve_event(o, 1), cutoff=T)
    f = fill_event(s, o, 2).document.to_dict()
    if case == "overfill":
        f["data"]["fill"].update(quantity=5, notional_krw="500")
    elif case == "cash":
        f["data"]["fill"]["fee_krw"] = "1000"
    elif case == "notional":
        f["data"]["fill"]["notional_krw"] = "1"
    elif case == "cost":
        del f["data"]["fill"]["fee_krw"]
    elif case == "negative":
        f["data"]["fill"]["tax_krw"] = "-1"
    elif case == "unknown":
        f["kind"] = "release"
        f["data"] = {"status": "unknown"}
    else:
        s = apply(s, command("release", 2, {"status": "cancelled"}, o.identifier), cutoff=T)
        f["sequence"] = 3
        f["event_id"] = "after-close"
    before = checkpoint(s)
    with pytest.raises(ValueError):
        apply(s, LedgerCommand(FrozenJSON.freeze(f)), cutoff=T)
    assert checkpoint(s) == before


def test_checkpoint_replay_resume_and_ambient_decimal_context() -> None:
    s = initial(held=3, basis="100")
    o = order(s, "SELL", 3)
    first = apply(s, reserve_event(o, 1), cutoff=T)
    mid = apply(first, fill_event(first, o, 2, n=1), cutoff=T)
    remaining = fill_event(mid, o, 3, n=2)
    uninterrupted = apply(mid, remaining, cutoff=T)
    with localcontext() as ctx:
        ctx.prec = 2
        resumed = apply(restore(checkpoint(mid)), remaining, cutoff=T)
    assert resumed.identifier == uninterrupted.identifier
    assert resumed.to_dict()["positions"]["005930"]["cost_basis_krw"] == "0"
    assert resumed.to_dict()["realized_pnl_krw"] == "196"


@pytest.mark.parametrize(
    "field", ["state", "events", "manifest", "tape_hash", "policy", "state_hash"]
)
def test_checkpoint_tampering(field: str) -> None:
    c = checkpoint(initial()).to_dict()
    if field == "state":
        c[field]["available_cash_krw"] = "9000"
    elif field == "events":
        c[field][0]["source_id"] = "b" * 64
    elif field == "manifest":
        c[field]["initial_account"]["available_cash_krw"] = "2000"
    elif field == "policy":
        c[field]["version"] = "changed"
    else:
        c[field] = "b" * 64
    with pytest.raises(ValueError):
        restore(FrozenJSON.freeze(c))


def test_no_additional_buy_and_duplicate_cost_charge() -> None:
    held = initial(held=1, basis="100")
    with pytest.raises(ValueError, match="additional_buy"):
        apply(held, reserve_event(order(held), 1), cutoff=T)
    s = initial()
    o = order(s)
    s = apply(s, reserve_event(o, 1), cutoff=T)
    s = apply(s, fill_event(s, o, 2), cutoff=T)
    f = fill_event(s, o, 3).document.to_dict()
    f["data"]["fill"]["cost_charge_id"] = f"{2:064x}"
    with pytest.raises(ValueError, match="duplicate_cost_charge"):
        apply(s, LedgerCommand(FrozenJSON.freeze(f)), cutoff=T)


def test_cancel_expire_reject_require_explicit_consistent_states() -> None:
    s = initial()
    o = order(s)
    s = apply(s, reserve_event(o, 1), cutoff=T)
    with pytest.raises(ValueError, match="premature_expiry"):
        apply(s, command("release", 2, {"status": "expired"}, o.identifier), cutoff=T)
    expiry = o.to_dict()["expires_at"]
    expired = apply(
        s,
        command("release", 2, {"status": "expired"}, o.identifier, effective=expiry, known=expiry),
        cutoff=expiry,
    )
    assert expired.to_dict()["available_cash_krw"] == "1000"
    rejected = apply(s, command("release", 2, {"status": "rejected"}, o.identifier), cutoff=T)
    assert rejected.to_dict()["reserved_cash_krw"] == "0"
    partial = apply(s, fill_event(s, o, 2), cutoff=T)
    with pytest.raises(ValueError, match="cannot_reject_filled"):
        apply(partial, command("release", 3, {"status": "rejected"}, o.identifier), cutoff=T)


def test_canonical_offset_and_detached_account() -> None:
    s = initial()
    detached = s.to_dict()
    detached["available_cash_krw"] = "99999"
    assert s.to_dict()["available_cash_krw"] == "1000"
    a = command("initialize", 0, {})
    b = command(
        "initialize", 0, {}, effective="2026-01-04T19:00:00-05:00", known="2026-01-05T00:00:00Z"
    )
    assert a.identifier == b.identifier


def test_unknown_initial_order_and_short_sell_are_blocked() -> None:
    s = initial()
    sell = order(s, "SELL", 1)
    with pytest.raises(ValueError, match="insufficient_shares"):
        apply(s, reserve_event(sell, 1), cutoff=T)
    unknown = order(s).to_dict()
    unknown["status"] = "unknown"
    with pytest.raises(ValueError, match="new_open_order"):
        apply(s, reserve_event(VirtualOrder.from_dict(unknown), 1), cutoff=T)
