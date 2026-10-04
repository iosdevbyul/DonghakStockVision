"""Adapter from baseline events to the existing immutable ledger; no pricing rules."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from donghak_stock_vision.backtest.contracts import VirtualFill, VirtualOrder
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.ledger import LedgerCommand, LedgerState, apply
from donghak_stock_vision.data.learning import digest


def command(
    state: LedgerState,
    kind: str,
    order_id: str,
    source: str,
    effective: datetime,
    known: datetime,
    data: dict[str, Any],
) -> LedgerState:
    sequence = state.to_dict()["last_sequence"] + 1
    body = dict(
        kind=kind,
        order_id=order_id,
        sequence=sequence,
        effective_at=effective.isoformat(),
        known_at=known.isoformat(),
        source_id=source,
        data=data,
    )
    event = LedgerCommand(FrozenJSON.freeze(dict(body, event_id=digest(body))))
    return apply(state, event, cutoff=known.isoformat())


def common(state: LedgerState) -> dict[str, Any]:
    return dict(
        schema_version=1,
        scope="research",
        executable=False,
        operational_eligible=False,
        virtual=True,
        broker_route=None,
        run_id=state.manifest.identifier,
        **{
            k: state.manifest.to_dict()[k]
            for k in ("information_mode", "data_origin", "usage_restriction")
        },
    )


def reserve(
    state: LedgerState,
    *,
    side: str,
    ticker: str,
    quantity: int,
    source: str,
    effective: datetime,
    known: datetime,
    notional: Decimal,
    fee: Decimal,
) -> tuple[LedgerState, str]:
    order = VirtualOrder.from_dict(
        dict(
            common(state),
            decision_id=source,
            idempotency_key=digest([source, side, ticker]),
            account_revision=state.to_dict()["revision"],
            ticker=ticker,
            side=side,
            quantity=quantity,
            remaining_quantity=quantity,
            filled_quantity=0,
            cancelled_quantity=0,
            status="open",
            submitted_at=effective.isoformat(),
            accepted_at=effective.isoformat(),
            expires_at=None,
            cancelled_at=None,
            reserved_cash_krw="0",
            reserved_quantity=0,
            policy_id=state.policy.identifier,
        )
    )
    data: dict[str, Any] = {"order": order.to_dict()}
    if side == "BUY":
        data.update(max_notional_krw=str(notional), max_cost_krw=str(fee))
    return command(
        state,
        "reserve_buy" if side == "BUY" else "reserve_sell",
        order.identifier,
        source,
        effective,
        known,
        data,
    ), order.identifier


def confirm_fill(
    state: LedgerState,
    *,
    order_id: str,
    source: str,
    ticker: str,
    quantity: int,
    reference: Decimal,
    price: Decimal,
    fee: Decimal,
    tax: Decimal,
    effective: datetime,
    known: datetime,
) -> tuple[LedgerState, str]:
    sequence = state.to_dict()["last_sequence"] + 1
    fill = VirtualFill.from_dict(
        dict(
            common(state),
            order_id=order_id,
            sequence=sequence,
            ticker=ticker,
            quantity=quantity,
            price_krw=str(price),
            notional_krw=str(price * quantity),
            fee_krw=str(fee),
            tax_krw=str(tax),
            benchmark_price_krw=str(reference),
            slippage_krw=str(abs(price - reference) * quantity),
            fill_at=effective.isoformat(),
            fill_known_at=known.isoformat(),
            source_event_id=source,
            liquidity_evidence_id=source,
            policy_id=state.policy.identifier,
            cost_charge_id=digest([order_id, source, "cost"]),
            simulation_only=True,
        )
    )
    return command(
        state, "fill", order_id, source, effective, known, {"fill": fill.to_dict()}
    ), fill.identifier


def release(state: LedgerState, order_id: str, effective: datetime, known: datetime) -> LedgerState:
    return command(state, "release", order_id, order_id, effective, known, {"status": "cancelled"})
