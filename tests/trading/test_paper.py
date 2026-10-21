"""Explicit synthetic liquidity, prices and rates. No real market/broker claims."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Any

import pytest
from tests.trading.test_risk import KR, US, T
from tests.trading.test_risk import fixture as risk_fixture

from donghak_stock_vision.trading.broker import ExecutionMode, PaperBroker, RiskDecision
from donghak_stock_vision.trading.domain import (
    CashBalance,
    Currency,
    Exchange,
    Instrument,
    Money,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Quantity,
)
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_types import (
    CurrencyMechanics,
    FillEvent,
    FixturePolicy,
    MarketSession,
    PaperFrame,
    PaperReceipt,
    PaperState,
)
from donghak_stock_vision.trading.risk import FXRate, evaluate_risk


def fixture(
    instrument: Instrument = KR, **rates: Any
) -> tuple[DeterministicPaperBroker, OrderIntent, PaperFrame]:
    order, context, risk = risk_fixture(instrument, instrument.currency)
    policy = FixturePolicy(
        "fixture-only",
        (risk,),
        (CurrencyMechanics(instrument.currency, Decimal("0.01"), Decimal("0.01")),),
        Decimal("0.001"),
        Decimal("0.001"),
        Decimal("0.002"),
        Decimal("0.01"),
        60,
        100,
    )
    if rates:
        policy = replace(policy, **rates)
    assert context.valuation is not None
    frame = PaperFrame(
        "initial",
        T,
        context.quotes,
        (),
        (context.valuation,),
        (MarketSession(instrument.exchange, True, T, T, "fixture-session"),),
        True,
    )
    return DeterministicPaperBroker(context.account, policy, frame), order, frame


def next_frame(frame: PaperFrame, step: int = 1, price: str = "10") -> PaperFrame:
    now = T + timedelta(seconds=step)
    quotes = tuple(
        replace(
            q,
            price=Money(Decimal(price), q.instrument.currency),
            observed_at=now,
            available_at=now,
            source_id=f"quote-{step}",
        )
        for q in frame.quotes
    )
    return replace(
        frame,
        frame_id=f"frame-{step}",
        now=now,
        quotes=quotes,
        valuations=tuple(replace(v, observed_at=now, available_at=now) for v in frame.valuations),
        sessions=tuple(replace(s, observed_at=now, available_at=now) for s in frame.sessions),
    )


def submit(
    broker: DeterministicPaperBroker, order: OrderIntent, key: str = "submission"
) -> PaperReceipt:
    return broker.submit(order, idempotency_key=key, risk=broker.preflight(order).to_decision())


def event(order: OrderIntent, quantity: int = 5, step: int = 1, key: str = "fill1") -> FillEvent:
    return FillEvent(
        key, order.order_id, Quantity(quantity), T + timedelta(seconds=step), "synthetic-liquidity"
    )


def cash(broker: DeterministicPaperBroker, currency: Currency) -> CashBalance:
    return next(b for b in broker.account("account1").balances if b.available.currency is currency)


@pytest.mark.parametrize(
    "instrument",
    [KR, US, replace(KR, exchange=Exchange.KOSDAQ), replace(US, exchange=Exchange.NYSE)],
)
def test_market_buy_full_fill_and_native_cash(instrument: Instrument) -> None:
    broker, order, frame = fixture(instrument)
    assert isinstance(broker, PaperBroker) and broker.mode is ExecutionMode.PAPER
    accepted = submit(broker, order)
    assert accepted.risk is not None
    assert accepted.outcome == "accepted" and accepted.risk.allowed
    assert accepted.order_status is OrderStatus.OPEN and broker.fills() == ()
    balance = cash(broker, instrument.currency)
    assert balance.available.amount == Decimal("849.40")
    assert balance.reserved.amount == Decimal("50.60")
    broker.advance(next_frame(frame))
    result = broker.execute(event(order))
    assert result.outcome == "filled" and result.order_status is OrderStatus.FILLED
    assert result.fill is not None
    assert result.fill.price.amount == Decimal("10.10")
    assert result.fill.fees.amount == Decimal("0.06") and result.fill.taxes.amount == 0
    assert result.slippage is not None
    assert result.slippage.amount == Decimal("0.50")
    assert result.to_dict()["simulated"] is True
    assert broker.simulated is True
    assert result.frame_id == "frame-1" and result.policy_id == "fixture-only"
    assert result.quote_source_id == "quote-1"
    assert result.session_source_id == "fixture-session" and result.command_key == "fill1"
    assert result.to_dict()["risk"]["allowed"] is True
    balance = cash(broker, instrument.currency)
    assert balance.available.amount == Decimal("849.44") and balance.reserved.amount == 0
    position = broker.account("account1").positions[0]
    assert position.quantity.shares == position.sellable.shares == 15
    assert position.reserved.shares == 0
    assert result.fill.order == order and result.fill.source_id == "synthetic-liquidity"


@pytest.mark.parametrize("instrument", [KR, US])
def test_sell_reservation_execution_and_costs(instrument: Instrument) -> None:
    broker, order, frame = fixture(instrument)
    order = replace(order, side=OrderSide.SELL)
    assert submit(broker, order).outcome == "accepted"
    position = broker.account("account1").positions[0]
    assert position.sellable.shares == position.reserved.shares == 5
    broker.advance(next_frame(frame))
    result = broker.execute(event(order))
    assert result.fill is not None
    assert result.fill.price.amount == Decimal("9.90")
    assert result.fill.fees.amount == Decimal("0.05")
    assert result.fill.taxes.amount == Decimal("0.10")
    assert cash(broker, instrument.currency).available.amount == Decimal("949.35")
    position = broker.account("account1").positions[0]
    assert (
        position.quantity.shares == position.sellable.shares == 5 and position.reserved.shares == 0
    )


def test_buy_then_sell_round_trip() -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    broker.advance(next_frame(frame))
    broker.execute(event(order))
    sale = replace(order, order_id="sale", side=OrderSide.SELL, created_at=T + timedelta(seconds=1))
    submit(broker, sale, "sale-request")
    broker.advance(next_frame(frame, 2))
    broker.execute(event(sale, step=2, key="sale-fill"))
    assert cash(broker, Currency.KRW).available.amount == Decimal("898.79")
    assert broker.account("account1").positions[0].quantity.shares == 10
    assert len(broker.fills()) == 2


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_partial_fill_then_cancel_releases_only_remainder(side: OrderSide) -> None:
    broker, order, frame = fixture()
    order = replace(order, side=side)
    submit(broker, order)
    broker.advance(next_frame(frame))
    partial = broker.execute(event(order, 2))
    assert partial.order_status is OrderStatus.PARTIAL
    assert broker.order(order.order_id).remaining == 3
    if side is OrderSide.BUY:
        assert cash(broker, Currency.KRW).reserved.amount == Decimal("30.36")
    else:
        assert broker.account("account1").positions[0].reserved.shares == 3
    result = broker.cancel(order.order_id, idempotency_key="cancel", at=T + timedelta(seconds=1))
    assert result.outcome == "cancelled" and result.order_status is OrderStatus.CANCELLED
    record = broker.order(order.order_id)
    assert record.remaining == 0 and record.filled == 2 and record.closed == 3
    assert cash(broker, Currency.KRW).reserved.amount == 0
    assert broker.account("account1").positions[0].reserved.shares == 0
    assert len(broker.fills()) == 1
    if side is OrderSide.BUY:
        assert cash(broker, Currency.KRW).available.amount == Decimal("879.77")


def test_per_share_reservation_covers_fee_rounding_across_all_partial_fills() -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    broker.advance(next_frame(frame))
    for i in range(5):
        result = broker.execute(event(order, 1, key=f"unit-{i}"))
        assert result.outcome == "filled"
    assert broker.order(order.order_id).status is OrderStatus.FILLED
    assert cash(broker, Currency.KRW).reserved.amount == 0
    assert cash(broker, Currency.KRW).available.amount == Decimal("849.40")
    assert sum(f.fees.amount for f in broker.fills()) == Decimal("0.10")


def test_idempotent_submission_fill_and_cancellation() -> None:
    broker, order, frame = fixture()
    proof = broker.preflight(order).to_decision()
    first = broker.submit(order, idempotency_key="request", risk=proof)
    head = broker.state_id
    assert broker.submit(order, idempotency_key="request", risk=proof) == first
    assert broker.state_id == head
    with pytest.raises(ValueError, match="duplicate_order_id"):
        broker.submit(order, idempotency_key="different-key", risk=proof)
    with pytest.raises(ValueError, match="idempotency_conflict"):
        broker.submit(replace(order, quantity=Quantity(4)), idempotency_key="request", risk=proof)
    broker.advance(next_frame(frame))
    fill = broker.execute(event(order, 2))
    head = broker.state_id
    assert broker.execute(event(order, 2)) == fill and broker.state_id == head
    with pytest.raises(ValueError, match="idempotency_conflict"):
        broker.execute(event(order, 3))
    first_cancel = broker.cancel(
        order.order_id, idempotency_key="cancel", at=T + timedelta(seconds=1)
    )
    head = broker.state_id
    assert (
        broker.cancel(order.order_id, idempotency_key="cancel", at=T + timedelta(seconds=1))
        == first_cancel
    )
    assert broker.state_id == head
    assert (
        broker.cancel(
            order.order_id, idempotency_key="another-cancel", at=T + timedelta(seconds=1)
        ).outcome
        == "unchanged"
    )


def test_completed_fill_cannot_be_executed_again_with_a_new_event() -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    broker.advance(next_frame(frame))
    original = broker.execute(event(order))
    head = broker.state_id
    assert broker.execute(event(order)) == original
    with pytest.raises(ValueError, match="order_not_open"):
        broker.execute(event(order, key="other-event"))
    assert broker.state_id == head and len(broker.fills()) == 1


@pytest.mark.parametrize("proof_kind", ["missing", "forged", "stale"])
def test_no_caller_can_bypass_internal_risk_evaluation(
    proof_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.trading import paper

    broker, order, frame = fixture()
    proof = broker.preflight(order).to_decision()
    if proof_kind == "missing":
        proof = None
    elif proof_kind == "forged":
        proof = RiskDecision(
            order.order_id, allowed=True, reasons=(), evidence_id="forged", order=order
        )
    else:
        broker.advance(next_frame(frame))
    original = evaluate_risk
    calls = []

    def traced(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(paper, "evaluate_risk", traced)
    result = broker.submit(order, idempotency_key="request", risk=proof)
    assert len(calls) == 1
    assert result.outcome == "rejected"
    assert "risk_evidence_missing" in result.reasons or "risk_evidence_mismatch" in result.reasons
    assert cash(broker, Currency.KRW).available.amount == 900
    assert cash(broker, Currency.KRW).reserved.amount == 0
    assert broker.fills() == ()


@pytest.mark.parametrize(
    "side,quantity,reason",
    [
        (OrderSide.BUY, 90, "insufficient_available_cash"),
        (OrderSide.SELL, 11, "insufficient_sellable_position"),
    ],
)
def test_insufficient_funds_or_shares(side: OrderSide, quantity: int, reason: str) -> None:
    broker, order, frame = fixture()
    checkpoint = broker.checkpoint()
    risk = replace(
        checkpoint.policy.risk_policies[0],
        max_order_notional=Money(Decimal(2000), Currency.KRW),
        max_exposure_fraction=Decimal(1),
        max_position_fraction=Decimal(1),
    )
    valuation = replace(frame.valuations[0], equity=Money(Decimal(2000), Currency.KRW))
    policy = replace(checkpoint.policy, risk_policies=(risk,))
    broker = DeterministicPaperBroker(
        checkpoint.initial_account, policy, replace(frame, valuations=(valuation,))
    )
    order = replace(order, side=side, quantity=Quantity(quantity))
    result = submit(broker, order)
    assert result.outcome == "rejected" and reason in result.reasons
    assert result.risk is not None
    assert any(r.code.value == reason for r in result.risk.rejections)
    assert (
        cash(broker, Currency.KRW).available.amount == 900
        and cash(broker, Currency.KRW).reserved.amount == 0
    )


def test_pending_orders_cannot_double_spend_cash_or_shares() -> None:
    broker, order, _ = fixture()
    sale = replace(order, side=OrderSide.SELL, quantity=Quantity(6))
    assert submit(broker, sale).outcome == "accepted"
    another = replace(sale, order_id="other", quantity=Quantity(5))
    assert "insufficient_sellable_position" in submit(broker, another, "other").reasons
    broker.cancel(sale.order_id, idempotency_key="cancel", at=T)
    third = replace(another, order_id="third")
    assert submit(broker, third, "third").outcome == "accepted"


@pytest.mark.parametrize(
    "buy,limit,price,expected",
    [
        (True, "9", "10", "pending"),
        (True, "11", "10", "filled"),
        (False, "11", "10", "pending"),
        (False, "9", "10", "filled"),
    ],
)
def test_limit_orders(buy: bool, limit: str, price: str, expected: str) -> None:
    broker, order, frame = fixture()
    order = replace(
        order,
        side=OrderSide.BUY if buy else OrderSide.SELL,
        order_type=OrderType.LIMIT,
        limit_price=Money(Decimal(limit), Currency.KRW),
    )
    assert submit(broker, order).outcome == "accepted"
    broker.advance(next_frame(frame, price=price))
    result = broker.execute(event(order))
    assert result.outcome == expected
    assert result.reasons == (("limit_not_marketable",) if expected == "pending" else ())
    if result.fill is not None:
        assert (
            result.fill.price.amount <= Decimal(limit)
            if buy
            else result.fill.price.amount >= Decimal(limit)
        )


def test_missing_stale_future_or_old_execution_prices_remain_pending() -> None:
    for kind in ("missing", "stale", "future", "old"):
        broker, order, frame = fixture()
        submit(broker, order)
        later = next_frame(frame, step=120)
        if kind == "missing":
            later = replace(later, quotes=())
        elif kind == "stale":
            later = replace(
                later, quotes=(replace(later.quotes[0], observed_at=T + timedelta(seconds=1)),)
            )
        elif kind == "future":
            later = replace(
                later,
                quotes=(replace(later.quotes[0], available_at=later.now + timedelta(seconds=1)),),
            )
        else:
            later = replace(later, quotes=frame.quotes)
        broker.advance(later)
        result = broker.execute(event(order, step=120))
        assert result.outcome == "pending" and result.fill is None
        assert broker.order(order.order_id).status is OrderStatus.OPEN
        assert cash(broker, Currency.KRW).reserved.amount == Decimal("50.60")


def test_closed_unknown_and_stale_sessions_do_not_fill() -> None:
    for kind in (False, None, "missing", "stale"):
        broker, order, frame = fixture()
        submit(broker, order)
        later = next_frame(frame, 120)
        if kind in (False, None):
            later = replace(later, sessions=(replace(later.sessions[0], is_open=kind),))
        elif kind == "missing":
            later = replace(later, sessions=())
        else:
            later = replace(later, sessions=(replace(later.sessions[0], observed_at=T),))
        broker.advance(later)
        assert broker.execute(event(order, step=120)).outcome == "pending"
        assert broker.fills() == ()


def test_risk_approval_does_not_mean_same_tick_execution_eligibility() -> None:
    broker, order, _ = fixture()
    acceptance = submit(broker, order)
    assert acceptance.risk is not None and acceptance.risk.allowed
    result = broker.execute(event(order, step=0))
    assert result.outcome == "pending" and "later_execution_event_required" in result.reasons


def test_risk_is_rechecked_at_fill_and_can_block_previously_accepted_order() -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    later = next_frame(frame)
    later = replace(
        later,
        valuations=(replace(later.valuations[0], daily_pnl=Money(Decimal(-50), Currency.KRW)),),
    )
    broker.advance(later)
    result = broker.execute(event(order))
    assert result.outcome == "pending" and "daily_loss_limit" in result.reasons
    broker.cancel(order.order_id, idempotency_key="cancel", at=later.now)
    assert cash(broker, Currency.KRW).available.amount == 900


def test_cap_rejection_releases_reservation_and_preserves_partial_fills() -> None:
    for partial in (False, True):
        broker, order, frame = fixture()
        submit(broker, order)
        if partial:
            broker.advance(next_frame(frame))
            broker.execute(event(order, 2))
        broker.advance(next_frame(frame, 2, "11"))
        result = broker.execute(event(order, 3 if partial else 5, 2, "cap-event"))
        assert result.outcome == "rejected" and result.reasons == ("accepted_price_cap_exceeded",)
        assert result.order_status is (OrderStatus.CANCELLED if partial else OrderStatus.REJECTED)
        assert cash(broker, Currency.KRW).reserved.amount == 0
        assert len(broker.fills()) == (1 if partial else 0)


def test_invalid_commands_are_atomic_and_retry_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    broker.advance(next_frame(frame))
    head, revision = broker.state_id, broker.revision
    with pytest.raises(ValueError, match="overfill"):
        broker.execute(event(order, 6))
    assert broker.state_id == head
    original = broker._validate_state

    def fail_before_commit(state: PaperState) -> None:
        original(state)
        if state.revision > revision:
            raise ValueError("injected_validation_failure")

    monkeypatch.setattr(broker, "_validate_state", fail_before_commit)
    with pytest.raises(ValueError, match="injected_validation_failure"):
        broker.execute(event(order))
    assert broker.state_id == head and broker.fills() == ()
    monkeypatch.setattr(broker, "_validate_state", original)
    assert broker.execute(event(order)).outcome == "filled"


def test_checkpoint_replay_restores_idempotency_and_rejects_tampering() -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    broker.advance(next_frame(frame))
    original = broker.execute(event(order, 2))
    checkpoint = broker.checkpoint()
    restored = DeterministicPaperBroker.restore(checkpoint)
    assert restored.state_id == broker.state_id
    assert restored.execute(event(order, 2)) == original
    assert restored.state_id == broker.state_id
    restored.cancel(order.order_id, idempotency_key="cancel", at=T + timedelta(seconds=1))
    for invalid in (
        replace(checkpoint, state_id="0" * 64),
        replace(checkpoint, commands=checkpoint.commands[:-1]),
        replace(checkpoint, commands=tuple(reversed(checkpoint.commands))),
        replace(checkpoint, commands=(*checkpoint.commands, checkpoint.commands[-1])),
    ):
        with pytest.raises(ValueError):
            DeterministicPaperBroker.restore(invalid)


def test_inconsistent_private_state_fails_closed() -> None:
    broker, order, _ = fixture()
    state, seal = broker._head
    changed = replace(
        state.account,
        balances=(CashBalance(Money(Decimal(901), Currency.KRW), Money(Decimal(0), Currency.KRW)),),
    )
    broker._head = (replace(state, account=changed), seal)
    with pytest.raises(ValueError, match="paper_state_integrity_failure"):
        broker.preflight(order)


def test_reproducibility_and_ambient_decimal_context() -> None:
    first, order, frame = fixture()
    submit(first, order)
    first.advance(next_frame(frame))
    expected = first.execute(event(order))
    with localcontext() as context:
        context.prec = 1
        context.rounding = "ROUND_DOWN"
        second, order, frame = fixture()
        submit(second, order)
        second.advance(next_frame(frame))
        actual = second.execute(event(order))
        assert context.prec == 1
    assert actual == expected and second.state_id == first.state_id


def test_protocol_and_unknown_order_reads_are_safe() -> None:
    broker, order, _ = fixture()
    assert broker.order_status("account1", "unknown") is OrderStatus.UNKNOWN
    proof = broker.preflight(order).to_decision()
    assert proof is not None
    assert broker.submit_paper(order, proof) is OrderStatus.OPEN
    assert not hasattr(broker, "submit_live")
    with pytest.raises(ValueError, match="unknown_account"):
        broker.account("other")
    with pytest.raises(ValueError, match="unknown_order"):
        broker.cancel("unknown", idempotency_key="cancel", at=T)


def test_clock_regression_and_conflicting_frames_fail_without_mutation() -> None:
    broker, _, frame = fixture()
    later = next_frame(frame)
    broker.advance(later)
    head = broker.state_id
    broker.advance(later)
    assert broker.state_id == head
    with pytest.raises(ValueError, match="idempotency_conflict"):
        broker.advance(replace(later, trading_enabled=False))
    with pytest.raises(ValueError, match="clock_regression"):
        broker.advance(replace(frame, frame_id="older"))
    assert broker.state_id == head


def mixed_fixture(
    usd_cash: str = "900",
) -> tuple[DeterministicPaperBroker, OrderIntent, OrderIntent, PaperFrame]:
    kr, kr_order, kr_frame = fixture(KR)
    us, us_order, us_frame = fixture(US)
    us_order = replace(us_order, order_id="us-order")
    kr_root, us_root = kr.checkpoint(), us.checkpoint()
    account = replace(
        kr_root.initial_account,
        balances=(
            *kr_root.initial_account.balances,
            CashBalance(Money(Decimal(usd_cash), Currency.USD), Money(Decimal(0), Currency.USD)),
        ),
        positions=(*kr_root.initial_account.positions, *us_root.initial_account.positions),
    )
    policy = replace(
        kr_root.policy,
        risk_policies=(*kr_root.policy.risk_policies, *us_root.policy.risk_policies),
        currencies=(*kr_root.policy.currencies, *us_root.policy.currencies),
    )
    valuations = tuple(
        replace(v, equity=Money(Decimal(2000), v.equity.currency))
        for v in (*kr_frame.valuations, *us_frame.valuations)
    )
    frame = replace(
        kr_frame,
        quotes=(*kr_frame.quotes, *us_frame.quotes),
        valuations=valuations,
        sessions=(*kr_frame.sessions, *us_frame.sessions),
        fx_rates=(
            FXRate(Currency.USD, Currency.KRW, Decimal(1), T, T, "fixture-fx1"),
            FXRate(Currency.KRW, Currency.USD, Decimal(1), T, T, "fixture-fx2"),
        ),
    )
    return DeterministicPaperBroker(account, policy, frame), kr_order, us_order, frame


def test_single_account_holds_krw_usd_and_both_instruments() -> None:
    broker, kr_order, us_order, frame = mixed_fixture()
    assert submit(broker, kr_order, "kr").outcome == "accepted"
    assert submit(broker, us_order, "us").outcome == "accepted"
    assert all(cash(broker, c).reserved.amount == Decimal("50.60") for c in Currency)
    broker.advance(next_frame(frame))
    assert broker.execute(event(kr_order, key="kr-fill")).outcome == "filled"
    assert broker.execute(event(us_order, key="us-fill")).outcome == "filled"
    for currency in Currency:
        assert cash(broker, currency).available.amount == Decimal("849.44")
        assert cash(broker, currency).reserved.amount == 0
    assert {p.instrument for p in broker.account("account1").positions} == {KR, US}
    assert all(p.quantity.shares == 15 for p in broker.account("account1").positions)


def test_fx_risk_conversion_does_not_convert_cash_for_settlement() -> None:
    broker, _, order, _ = mixed_fixture("1")
    result = submit(broker, order)
    assert result.outcome == "rejected" and "insufficient_available_cash" in result.reasons
    assert cash(broker, Currency.KRW).available.amount == 900
    assert cash(broker, Currency.USD).available.amount == 1


def test_second_pending_buy_cannot_consume_reserved_cash() -> None:
    base, order, frame = fixture()
    initial = base.checkpoint()
    risk = replace(
        initial.policy.risk_policies[0],
        max_order_notional=Money(Decimal(2000), Currency.KRW),
        max_position_fraction=Decimal(1),
        max_exposure_fraction=Decimal(1),
    )
    valuation = replace(frame.valuations[0], equity=Money(Decimal(3000), Currency.KRW))
    policy = replace(initial.policy, risk_policies=(risk,))
    broker = DeterministicPaperBroker(
        initial.initial_account, policy, replace(frame, valuations=(valuation,))
    )
    first = replace(order, quantity=Quantity(50))
    second = replace(order, order_id="second", quantity=Quantity(40))
    assert submit(broker, first, "first").outcome == "accepted"
    assert cash(broker, Currency.KRW).reserved.amount == Decimal("506.00")
    result = submit(broker, second, "second")
    assert result.outcome == "rejected" and "insufficient_available_cash" in result.reasons
    assert cash(broker, Currency.KRW).reserved.amount == Decimal("506.00")


@pytest.mark.parametrize("kind", ["missing", "stale", "future"])
def test_submission_price_evidence_failures_never_reserve(kind: str) -> None:
    base, order, frame = fixture()
    root = base.checkpoint()
    if kind == "missing":
        frame = replace(frame, quotes=())
    elif kind == "stale":
        frame = replace(
            frame, quotes=(replace(frame.quotes[0], observed_at=T - timedelta(seconds=61)),)
        )
    else:
        frame = replace(
            frame, quotes=(replace(frame.quotes[0], available_at=T + timedelta(seconds=1)),)
        )
    broker = DeterministicPaperBroker(root.initial_account, root.policy, frame)
    result = submit(broker, order)
    assert result.outcome == "rejected" and result.risk is not None and not result.risk.allowed
    assert cash(broker, Currency.KRW).reserved.amount == 0


@pytest.mark.parametrize("enabled", [False, None])
def test_disabled_or_unknown_fixture_state_denies_acceptance(enabled: bool | None) -> None:
    base, order, frame = fixture()
    root = base.checkpoint()
    broker = DeterministicPaperBroker(
        root.initial_account, root.policy, replace(frame, trading_enabled=enabled)
    )
    result = submit(broker, order)
    assert result.outcome == "rejected" and "trading_disabled_or_unknown" in result.reasons


def test_closed_session_can_accept_risk_approved_pending_order_but_cannot_execute() -> None:
    base, order, frame = fixture()
    root = base.checkpoint()
    frame = replace(frame, sessions=(replace(frame.sessions[0], is_open=False),))
    broker = DeterministicPaperBroker(root.initial_account, root.policy, frame)
    accepted = submit(broker, order)
    assert accepted.outcome == "accepted" and accepted.risk is not None and accepted.risk.allowed
    broker.advance(next_frame(frame))
    assert broker.execute(event(order)).outcome == "pending"
    assert broker.fills() == ()


def test_quantum_mismatch_rejects_before_reserving() -> None:
    broker, order, _ = fixture()
    order = replace(
        order, order_type=OrderType.LIMIT, limit_price=Money(Decimal("10.001"), Currency.KRW)
    )
    result = submit(broker, order)
    assert result.outcome == "rejected" and "limit_price_quantum_mismatch" in result.reasons
    assert cash(broker, Currency.KRW).reserved.amount == 0


def test_tiny_sell_price_cannot_create_negative_proceeds() -> None:
    broker, order, frame = fixture()
    order = replace(order, side=OrderSide.SELL)
    submit(broker, order)
    broker.advance(next_frame(frame, price="0.001"))
    result = broker.execute(event(order))
    assert result.outcome == "rejected" and "invalid_execution_economics" in result.reasons
    position = broker.account("account1").positions[0]
    assert (
        position.quantity.shares == position.sellable.shares == 10 and position.reserved.shares == 0
    )
    assert cash(broker, Currency.KRW).available.amount == 900


def test_command_budget_keeps_bounded_cancellation_cleanup_available() -> None:
    broker, order, frame = fixture(max_commands=2)
    submit(broker, order)
    broker.advance(next_frame(frame))
    head = broker.state_id
    with pytest.raises(ValueError, match="paper_command_limit"):
        broker.execute(event(order))
    assert broker.state_id == head
    result = broker.cancel(order.order_id, idempotency_key="cleanup", at=T + timedelta(seconds=1))
    assert result.outcome == "cancelled" and cash(broker, Currency.KRW).reserved.amount == 0
    with pytest.raises(ValueError, match="paper_command_limit"):
        broker.cancel(order.order_id, idempotency_key="extra", at=T + timedelta(seconds=1))
    assert DeterministicPaperBroker.restore(broker.checkpoint()).state_id == broker.state_id


def test_balanced_tampering_is_detected_even_if_private_digest_is_replaced() -> None:
    broker, _, _ = fixture()
    state, _ = broker._head
    changed = replace(
        state.account,
        balances=(CashBalance(Money(Decimal(901), Currency.KRW), Money(Decimal(0), Currency.KRW)),),
    )
    invalid = replace(state, account=changed)
    broker._head = (invalid, broker._identity(invalid))
    with pytest.raises(ValueError, match="cash_conservation"):
        broker.account("account1")


def test_concurrent_identical_requests_have_only_one_reservation() -> None:
    from concurrent.futures import ThreadPoolExecutor

    broker, order, _ = fixture()
    proof = broker.preflight(order).to_decision()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: broker.submit(order, idempotency_key="one", risk=proof), range(2))
        )
    assert results[0] == results[1]
    assert broker.revision == 1
    assert cash(broker, Currency.KRW).reserved.amount == Decimal("50.60")


@pytest.mark.parametrize(
    "changes",
    [
        {"buy_fee_rate": Decimal(-1)},
        {"slippage_rate": Decimal(1)},
        {"sell_fee_rate": Decimal("0.9"), "sell_tax_rate": Decimal("0.2")},
        {"max_commands": 0},
        {"max_session_age_seconds": True},
    ],
)
def test_invalid_fixture_policy_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        fixture(**changes)


def test_initial_foreign_holds_or_future_account_are_not_silently_adopted() -> None:
    base, _, frame = fixture()
    root = base.checkpoint()
    account = root.initial_account
    invalids = (
        replace(
            account,
            balances=(
                CashBalance(Money(Decimal(899), Currency.KRW), Money(Decimal(1), Currency.KRW)),
            ),
        ),
        replace(
            account,
            positions=(replace(account.positions[0], sellable=Quantity(9), reserved=Quantity(1)),),
        ),
        replace(account, available_at=T + timedelta(seconds=1)),
    )
    for invalid in invalids:
        with pytest.raises(ValueError):
            DeterministicPaperBroker(invalid, root.policy, frame)


def test_paper_import_has_no_framework_database_or_model_loading() -> None:
    import subprocess
    import sys

    source = """
import sys
import donghak_stock_vision.trading.paper
for prefix in ('fastapi', 'httpx', 'sqlite3', 'sklearn',
               'donghak_stock_vision.api', 'donghak_stock_vision.storage.sqlite',
               'donghak_stock_vision.storage.analysis', 'donghak_stock_vision.models'):
    assert not any(m == prefix or m.startswith(prefix + '.') for m in sys.modules), prefix
"""
    subprocess.run([sys.executable, "-c", source], check=True, timeout=10)


@pytest.mark.parametrize("extra,expected", [(0, "filled"), (1, "pending")])
def test_session_age_boundary_is_exact_to_microsecond(extra: int, expected: str) -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    later = next_frame(frame, 61)
    session = replace(later.sessions[0], observed_at=T + timedelta(seconds=1, microseconds=-extra))
    broker.advance(replace(later, sessions=(session,)))
    result = broker.execute(event(order, step=61))
    assert result.outcome == expected
    if extra:
        assert "session_evidence_unavailable" in result.reasons


def test_malformed_frame_does_not_modify_open_order_or_reservations() -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    head = broker.state_id
    later = next_frame(frame)
    quote = replace(later.quotes[0])
    object.__setattr__(quote, "price", Money(Decimal(-1), Currency.KRW))
    malformed = replace(later, quotes=(quote,))
    with pytest.raises(ValueError):
        broker.advance(malformed)
    assert broker.state_id == head
    assert cash(broker, Currency.KRW).reserved.amount == Decimal("50.60")


def test_checkpoint_missing_risk_or_changed_policy_cannot_restore_original_state() -> None:
    broker, order, _ = fixture()
    submit(broker, order)
    checkpoint = broker.checkpoint()
    command = checkpoint.commands[0]
    from donghak_stock_vision.trading.paper_types import Submission

    assert isinstance(command.payload, Submission)
    missing = replace(command, payload=replace(command.payload, risk=None))
    altered = replace(checkpoint.policy, slippage_rate=Decimal(0))
    for invalid in (replace(checkpoint, commands=(missing,)), replace(checkpoint, policy=altered)):
        with pytest.raises(ValueError, match="checkpoint_replay_mismatch"):
            DeterministicPaperBroker.restore(invalid)


def test_unsupported_native_currency_is_rejected_without_spending_other_cash() -> None:
    broker, order, _ = fixture()
    order = replace(order, instrument=US)
    result = submit(broker, order)
    assert result.outcome == "rejected"
    assert "native_currency_unsupported" in result.reasons
    assert cash(broker, Currency.KRW).available.amount == 900


def test_fill_event_rejection_and_cancel_do_not_invent_terminal_fills() -> None:
    broker, order, frame = fixture()
    submit(broker, order)
    broker.cancel(order.order_id, idempotency_key="cancel", at=T)
    broker.advance(next_frame(frame))
    head = broker.state_id
    with pytest.raises(ValueError, match="order_not_open"):
        broker.execute(event(order))
    assert broker.state_id == head and broker.fills() == ()


def test_fee_rounding_cannot_create_a_negative_net_sale() -> None:
    broker, order, frame = fixture()
    order = replace(order, side=OrderSide.SELL, quantity=Quantity(1))
    submit(broker, order)
    broker.advance(next_frame(frame, price="0.02"))
    result = broker.execute(event(order, 1))
    assert result.outcome == "rejected" and result.reasons == ("invalid_execution_economics",)
    assert broker.account("account1").positions[0].reserved.shares == 0
    assert cash(broker, Currency.KRW).available.amount == 900 and broker.fills() == ()


def test_high_precision_price_and_cost_quantization_are_explicit() -> None:
    base, order, frame = fixture()
    root = base.checkpoint()
    quantum = Decimal("0.000000000001")
    policy = replace(root.policy, currencies=(CurrencyMechanics(Currency.KRW, quantum, quantum),))
    quote = replace(frame.quotes[0], price=Money(Decimal("0.123456789123"), Currency.KRW))
    frame = replace(frame, quotes=(quote,))
    broker = DeterministicPaperBroker(root.initial_account, policy, frame)
    submit(broker, order)
    broker.advance(next_frame(frame, price="0.123456789123"))
    result = broker.execute(event(order))
    assert result.fill is not None
    assert result.fill.price.amount == Decimal("0.124691357015")
    assert result.fill.fees.amount == Decimal("0.000623456786")
    assert cash(broker, Currency.KRW).available.amount == Decimal("899.375919758139")
