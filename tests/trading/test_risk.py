"""Deterministic synthetic preflight. Prices/FX below are mechanics, not market facts."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

import pytest

from donghak_stock_vision.trading.broker import DenialReason, ExecutionMode
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    CashBalance,
    Currency,
    Exchange,
    Instrument,
    Market,
    Money,
    OrderIntent,
    OrderSide,
    OrderType,
    Position,
    Quantity,
)
from donghak_stock_vision.trading.risk import (
    FXRate,
    MarketQuote,
    PortfolioValuation,
    RiskCode,
    RiskContext,
    RiskPolicy,
    evaluate_risk,
)

T = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
KR = Instrument("00088K", Exchange.KOSPI, Currency.KRW)
US = Instrument("AAPL", Exchange.NASDAQ, Currency.USD)


def money(value: str, currency: Currency = Currency.KRW) -> Money:
    return Money(Decimal(value), currency)


def fixture(
    instrument: Instrument = KR, currency: Currency = Currency.KRW
) -> tuple[OrderIntent, RiskContext, RiskPolicy]:
    order = OrderIntent(
        "order1",
        "account1",
        instrument,
        OrderSide.BUY,
        OrderType.MARKET,
        Quantity(5),
        T,
        "decision1",
    )
    account = AccountSnapshot(
        "account1",
        (CashBalance(money("900", currency), money("0", currency)),),
        (Position(instrument, Quantity(10), Quantity(10), Quantity(0)),),
        T,
        T,
        "account-source",
    )
    quote = MarketQuote(instrument, money("10", instrument.currency), T, T, "quote-source")
    valuation = PortfolioValuation(
        "account1",
        money("1000", currency),
        money("0", currency),
        T.date(),
        T,
        T,
        "valuation-source",
    )
    rates = (
        ()
        if instrument.currency is currency
        else (FXRate(instrument.currency, currency, Decimal("2"), T, T, "synthetic-fx"),)
    )
    context = RiskContext(
        account, currency, True, valuation, (quote,), rates, (), money("1", currency), True, T
    )
    policy = RiskPolicy(
        "policy1",
        currency,
        frozenset({Market.KR, Market.US}),
        money("100", currency),
        Decimal("0.5"),
        Decimal("0.8"),
        money("50", currency),
        60,
        60,
        60,
        60,
        "Asia/Seoul" if currency is Currency.KRW else "America/New_York",
    )
    return order, context, policy


def codes(
    order: OrderIntent | None, context: RiskContext | None, policy: RiskPolicy | None
) -> tuple[RiskCode, ...]:
    return tuple(r.code for r in evaluate_risk(order, context, policy).rejections)


def forged(value: Any, **changes: Any) -> Any:
    result = deepcopy(value)
    for name, field in changes.items():
        object.__setattr__(result, name, field)
    return result


@pytest.mark.parametrize(
    "instrument,currency",
    [(KR, Currency.KRW), (US, Currency.USD), (US, Currency.KRW), (KR, Currency.USD)],
)
def test_allowed_kr_us_and_direct_fx(instrument: Instrument, currency: Currency) -> None:
    order, context, policy = fixture(instrument, currency)
    before = repr((order, context, policy))
    result = evaluate_risk(order, context, policy)
    assert result.allowed and result.rejections == ()
    rate = Decimal(1 if instrument.currency is currency else 2)
    assert result.order_notional == Decimal(50) * rate
    assert result.projected_position == result.projected_exposure == Decimal(150) * rate
    assert result.currency is currency
    decision = result.to_decision()
    assert decision is not None and decision.mode is ExecutionMode.PAPER
    decision.validate_for(order)
    assert decision.evidence_id == result.evidence_id
    assert repr((order, context, policy)) == before


@pytest.mark.parametrize(
    "code,changes",
    [
        (RiskCode.ORDER_NOTIONAL, {"max_order_notional": money("49.999999999999")}),
        (RiskCode.CONCENTRATION, {"max_position_fraction": Decimal("0.149999999999")}),
        (RiskCode.EXPOSURE, {"max_exposure_fraction": Decimal("0.149999999999")}),
        (RiskCode.MARKET, {"allowed_markets": frozenset({Market.US})}),
    ],
)
def test_individual_policy_rejection(code: RiskCode, changes: dict[str, Any]) -> None:
    order, context, policy = fixture()
    assert codes(order, context, replace(policy, **changes)) == (code,)


@pytest.mark.parametrize("cash,expected", [("51", ()), ("50.999999999999", (RiskCode.CASH,))])
def test_cash_includes_explicit_cost_buffer(cash: str, expected: tuple[RiskCode, ...]) -> None:
    order, context, policy = fixture()
    account = replace(context.account, balances=(CashBalance(money(cash), money("100")),))
    assert codes(order, replace(context, account=account), policy) == expected


@pytest.mark.parametrize(
    "pnl,expected",
    [
        ("-49.999999999999", ()),
        ("-50", (RiskCode.DAILY_LOSS,)),
        ("-51", (RiskCode.DAILY_LOSS,)),
        ("100", ()),
    ],
)
def test_daily_loss_inclusive_threshold(pnl: str, expected: tuple[RiskCode, ...]) -> None:
    order, context, policy = fixture()
    assert context.valuation is not None
    valuation = replace(context.valuation, daily_pnl=money(pnl))
    assert codes(order, replace(context, valuation=valuation), policy) == expected


def test_exact_limit_boundaries() -> None:
    order, context, policy = fixture()
    policy = replace(
        policy,
        max_order_notional=money("50"),
        max_position_fraction=Decimal("0.15"),
        max_exposure_fraction=Decimal("0.15"),
    )
    assert evaluate_risk(order, context, policy).allowed


@pytest.mark.parametrize("state", [False, None])
def test_disabled_and_unknown_state(state: bool | None) -> None:
    order, context, policy = fixture()
    assert codes(order, replace(context, trading_enabled=state), policy) == (RiskCode.DISABLED,)
    assert codes(order, replace(context, account_complete=state), policy) == (
        RiskCode.INVALID_CONTEXT,
    )


def test_missing_essential_inputs() -> None:
    order, context, policy = fixture()
    assert codes(order, context, None) == (RiskCode.MISSING_POLICY,)
    assert codes(None, context, policy) == (RiskCode.INVALID_ORDER,)
    assert codes(order, None, policy) == (RiskCode.INVALID_CONTEXT,)
    assert codes(order, replace(context, valuation=None), policy) == (RiskCode.MISSING_VALUATION,)
    assert codes(order, replace(context, quotes=()), policy) == (RiskCode.MISSING_PRICE,)
    assert codes(order, replace(context, buy_cost_buffer=None), policy) == (RiskCode.MISSING_COST,)
    assert codes(
        order, replace(context, account=replace(context.account, balances=())), policy
    ) == (RiskCode.MISSING_CASH,)
    result = evaluate_risk(None, None, None)
    assert not result.allowed and result.to_decision() is None and result.evidence_id is None


@pytest.mark.parametrize(
    "quantity",
    [
        Quantity(0),
        forged(Quantity(1), shares=-1),
        forged(Quantity(1), shares=True),
        forged(Quantity(1), shares=0.5),
    ],
)
def test_invalid_or_nonpositive_order_quantity(quantity: Quantity) -> None:
    order, context, policy = fixture()
    assert codes(forged(order, quantity=quantity), context, policy) == (RiskCode.QUANTITY,)


@pytest.mark.parametrize(
    "price",
    [money("0"), money("-1"), forged(money("1"), amount=Decimal("NaN")), money("1", Currency.USD)],
)
def test_invalid_reference_price(price: Money) -> None:
    order, context, policy = fixture()
    quote = forged(context.quotes[0], price=price)
    assert codes(order, replace(context, quotes=(quote,)), policy) == (RiskCode.INVALID_PRICE,)


@pytest.mark.parametrize(
    "field,code",
    [
        ("quotes", RiskCode.STALE_PRICE),
        ("account", RiskCode.STALE_ACCOUNT),
        ("valuation", RiskCode.STALE_VALUATION),
    ],
)
def test_freshness_inclusive_boundary(field: str, code: RiskCode) -> None:
    order, context, policy = fixture()
    original = context.quotes[0] if field == "quotes" else getattr(context, field)
    for extra, expected in ((0, ()), (1, (code,))):
        value = replace(original, observed_at=T - timedelta(seconds=60, microseconds=extra))
        updates: dict[str, Any] = {field: (value,) if field == "quotes" else value}
        changed = replace(context, **updates)
        assert codes(order, changed, policy) == expected


@pytest.mark.parametrize("field", ["quotes", "account", "valuation"])
def test_future_availability(field: str) -> None:
    order, context, policy = fixture()
    value = context.quotes[0] if field == "quotes" else getattr(context, field)
    changed = replace(value, available_at=T + timedelta(microseconds=1))
    updates: dict[str, Any] = {field: (changed,) if field == "quotes" else changed}
    context = replace(context, **updates)
    assert RiskCode.FUTURE in codes(order, context, policy)


def test_fx_missing_inverse_invalid_and_stale() -> None:
    order, context, policy = fixture(US, Currency.KRW)
    assert codes(order, replace(context, fx_rates=()), policy) == (RiskCode.MISSING_FX,)
    inverse = FXRate(Currency.KRW, Currency.USD, Decimal("0.5"), T, T, "inverse")
    assert codes(order, replace(context, fx_rates=(inverse,)), policy) == (RiskCode.MISSING_FX,)
    for invalid in (Decimal(0), Decimal(-1), Decimal("Infinity"), 1.0):
        invalid_fx = forged(context.fx_rates[0], rate=invalid)
        assert codes(order, replace(context, fx_rates=(invalid_fx,)), policy) == (
            RiskCode.INVALID_FX,
        )
    stale = replace(context.fx_rates[0], observed_at=T - timedelta(seconds=60, microseconds=1))
    assert codes(order, replace(context, fx_rates=(stale,)), policy) == (RiskCode.STALE_FX,)
    exact = replace(stale, observed_at=T - timedelta(seconds=60))
    assert evaluate_risk(order, replace(context, fx_rates=(exact,)), policy).allowed
    future = replace(exact, available_at=T + timedelta(microseconds=1))
    assert codes(order, replace(context, fx_rates=(future,)), policy) == (RiskCode.FUTURE,)


def test_currency_account_and_instrument_mismatches() -> None:
    order, context, policy = fixture()
    assert codes(order, replace(context, account_currency=Currency.USD), policy) == (
        RiskCode.CURRENCY,
    )
    assert codes(
        order, replace(context, account=replace(context.account, account_id="other")), policy
    ) == (RiskCode.ACCOUNT,)
    assert context.valuation is not None
    valuation = replace(
        context.valuation, equity=money("1000", Currency.USD), daily_pnl=money("0", Currency.USD)
    )
    assert codes(order, replace(context, valuation=valuation), policy) == (RiskCode.CURRENCY,)
    invalid = forged(order, instrument=forged(KR, currency=Currency.USD))
    assert codes(invalid, context, policy) == (RiskCode.INVALID_ORDER,)
    other_exchange = replace(KR, exchange=Exchange.KOSDAQ)
    quote = replace(context.quotes[0], instrument=other_exchange)
    assert codes(order, replace(context, quotes=(quote,)), policy) == (RiskCode.MISSING_PRICE,)


def test_daily_session_and_timezone() -> None:
    order, context, policy = fixture()
    assert context.valuation is not None
    wrong_day = replace(context.valuation, risk_date=T.date() - timedelta(days=1))
    assert codes(order, replace(context, valuation=wrong_day), policy) == (
        RiskCode.INVALID_VALUATION,
    )
    equivalent = replace(context, evaluated_at=T.astimezone(UTC))
    assert evaluate_risk(order, context, policy) == evaluate_risk(order, equivalent, policy)


def test_limit_order_uses_conservative_buy_price() -> None:
    order, context, policy = fixture()
    order = replace(order, order_type=OrderType.LIMIT, limit_price=money("30"))
    result = evaluate_risk(order, context, policy)
    assert result.order_notional == Decimal(150)
    assert tuple(r.code for r in result.rejections) == (RiskCode.ORDER_NOTIONAL,)
    assert result.projected_position == Decimal(250)


def test_pending_buys_count_toward_exposure_without_netting_pending_sells() -> None:
    order, context, policy = fixture()
    pending = replace(order, order_id="pending", quantity=Quantity(40))
    result = evaluate_risk(order, replace(context, pending_orders=(pending,)), policy)
    assert result.projected_position == Decimal(550)
    assert tuple(r.code for r in result.rejections) == (RiskCode.CONCENTRATION,)
    sale = replace(pending, side=OrderSide.SELL)
    assert evaluate_risk(
        order, replace(context, pending_orders=(sale,)), policy
    ).projected_exposure == Decimal(150)
    assert RiskCode.ACCOUNT in codes(order, replace(context, pending_orders=(order,)), policy)


def test_other_position_needs_mark_and_can_block_concentration() -> None:
    order, context, policy = fixture()
    other = Position(US, Quantity(60), Quantity(60), Quantity(0))
    account = replace(context.account, positions=(*context.account.positions, other))
    context = replace(context, account=account)
    assert codes(order, context, policy) == (RiskCode.MISSING_PRICE,)
    quote = MarketQuote(US, money("10", Currency.USD), T, T, "other-quote")
    context = replace(context, quotes=(*context.quotes, quote))
    assert codes(order, context, policy) == (RiskCode.MISSING_FX,)
    context = replace(
        context, fx_rates=(FXRate(Currency.USD, Currency.KRW, Decimal(1), T, T, "fx"),)
    )
    assert codes(order, context, policy) == (RiskCode.CONCENTRATION,)
    assert evaluate_risk(order, context, policy).projected_exposure == Decimal(750)


def test_sells_do_not_require_cash_proceeds_or_cost_buffer() -> None:
    order, context, policy = fixture()
    order = replace(order, side=OrderSide.SELL)
    context = replace(context, buy_cost_buffer=None)
    result = evaluate_risk(order, context, policy)
    assert result.allowed and result.projected_position == result.projected_exposure == Decimal(50)
    assert codes(
        replace(order, quantity=Quantity(11)),
        context,
        replace(policy, max_order_notional=money("1000")),
    ) == (RiskCode.POSITION,)
    position = replace(context.account.positions[0], sellable=Quantity(4), reserved=Quantity(6))
    assert codes(
        order, replace(context, account=replace(context.account, positions=(position,))), policy
    ) == (RiskCode.POSITION,)


def test_combined_reasons_are_deterministic_and_bound_to_paper_order() -> None:
    order, context, policy = fixture()
    assert context.valuation is not None
    valuation = replace(context.valuation, equity=money("100"), daily_pnl=money("-50"))
    account = replace(context.account, balances=(CashBalance(money("0"), money("0")),))
    context = replace(context, account=account, valuation=valuation, trading_enabled=False)
    policy = replace(
        policy,
        max_order_notional=money("40"),
        max_position_fraction=Decimal("0.2"),
        max_exposure_fraction=Decimal("0.5"),
    )
    expected = (
        RiskCode.DISABLED,
        RiskCode.DAILY_LOSS,
        RiskCode.ORDER_NOTIONAL,
        RiskCode.CASH,
        RiskCode.CONCENTRATION,
        RiskCode.EXPOSURE,
    )
    result = evaluate_risk(order, context, policy)
    assert tuple(r.code for r in result.rejections) == expected
    assert result == evaluate_risk(order, context, policy)
    decision = result.to_decision()
    assert decision is not None and not decision.allowed
    assert decision.reasons == (DenialReason.ELIGIBILITY_DENIED, DenialReason.RISK_LIMIT)
    with pytest.raises(ValueError, match="risk_denied"):
        decision.validate_for(order)


def test_decimal_context_independence_and_fractional_reference_values() -> None:
    order, context, policy = fixture()
    quote = replace(context.quotes[0], price=money("0.123456789123"))
    context = replace(context, quotes=(quote,))
    expected = Decimal("0.617283945615")
    baseline = evaluate_risk(order, context, policy)
    with localcontext() as ambient:
        ambient.prec = 1
        ambient.rounding = "ROUND_UP"
        actual = evaluate_risk(order, context, policy)
    assert actual == baseline and actual.allowed and actual.order_notional == expected
    assert actual.to_dict()["order_notional"] == str(expected)


def test_evidence_changes_for_policy_account_quote_fx_and_order() -> None:
    order, context, policy = fixture(US)
    baseline = evaluate_risk(order, context, policy).evidence_id
    assert baseline is not None and len(baseline) == 64
    variants = (
        (replace(order, quantity=Quantity(4)), context, policy),
        (order, context, replace(policy, policy_id="policy2")),
        (order, replace(context, account=replace(context.account, source_id="other")), policy),
        (order, replace(context, quotes=(replace(context.quotes[0], source_id="other"),)), policy),
        (
            order,
            replace(context, fx_rates=(replace(context.fx_rates[0], source_id="other"),)),
            policy,
        ),
    )
    assert all(evaluate_risk(*variant).evidence_id != baseline for variant in variants)


@pytest.mark.parametrize(
    "changes",
    [
        {"allowed_markets": frozenset()},
        {"max_position_fraction": Decimal(0)},
        {"max_position_fraction": Decimal("NaN")},
        {"max_exposure_fraction": Decimal("1.01")},
        {"max_order_notional": money("0")},
        {"max_daily_loss": money("-1")},
        {"max_daily_loss": money("1", Currency.USD)},
        {"max_price_age_seconds": -1},
        {"max_fx_age_seconds": True},
        {"risk_timezone": "not/a/zone"},
    ],
)
def test_policy_invalid_and_no_permissive_defaults(changes: dict[str, Any]) -> None:
    order, context, policy = fixture()
    with pytest.raises((ValueError, KeyError)):
        replace(policy, **changes)
    assert codes(order, context, forged(policy, **changes)) == (RiskCode.INVALID_POLICY,)


def test_duplicate_evidence_and_malformed_context_rejected() -> None:
    order, context, policy = fixture(US)
    for changed in (
        forged(context, quotes=(*context.quotes, *context.quotes)),
        forged(context, fx_rates=(*context.fx_rates, *context.fx_rates)),
        forged(context, pending_orders=(order, order)),
        forged(context, evaluated_at=T.replace(tzinfo=None)),
        forged(context, account_complete=1),
    ):
        assert codes(order, changed, policy) == (RiskCode.INVALID_CONTEXT,)


@pytest.mark.parametrize(
    "buffer,code",
    [(money("-1"), RiskCode.INVALID_COST), (money("1", Currency.USD), RiskCode.CURRENCY)],
)
def test_invalid_cost_buffer(buffer: Money, code: RiskCode) -> None:
    order, context, policy = fixture()
    assert codes(order, replace(context, buy_cost_buffer=buffer), policy) == (code,)


def test_invalid_valuation_and_nested_account_fail_closed() -> None:
    order, context, policy = fixture()
    assert context.valuation is not None
    valuation = forged(context.valuation, equity=money("0"))
    assert codes(order, replace(context, valuation=valuation), policy) == (
        RiskCode.INVALID_VALUATION,
    )
    valuation = forged(context.valuation, observed_at=T.replace(tzinfo=None))
    assert codes(order, replace(context, valuation=valuation), policy) == (
        RiskCode.INVALID_VALUATION,
    )
    position = forged(context.account.positions[0], reserved=Quantity(11))
    account = forged(context.account, positions=(position,))
    assert codes(order, replace(context, account=account), policy) == (RiskCode.INVALID_CONTEXT,)


def test_risk_calendar_date_uses_configured_timezone() -> None:
    order, context, policy = fixture(US, Currency.USD)
    now = datetime(2026, 1, 6, 1, tzinfo=UTC)  # Still January 5 in New York.
    quote = replace(context.quotes[0], observed_at=now, available_at=now)
    account = replace(context.account, observed_at=now, available_at=now)
    assert context.valuation is not None
    valuation = replace(context.valuation, observed_at=now, available_at=now)
    context = replace(
        context, account=account, valuation=valuation, quotes=(quote,), evaluated_at=now
    )
    result = evaluate_risk(order, context, policy)
    assert result.allowed
    wrong = replace(valuation, risk_date=now.date())
    assert codes(order, replace(context, valuation=wrong), policy) == (RiskCode.INVALID_VALUATION,)


def test_future_order_and_pending_order() -> None:
    order, context, policy = fixture()
    future = replace(order, created_at=T + timedelta(microseconds=1))
    assert codes(future, context, policy) == (RiskCode.FUTURE,)
    pending = replace(future, order_id="pending")
    assert codes(order, replace(context, pending_orders=(pending,)), policy) == (RiskCode.FUTURE,)


def test_deterministic_json_and_allowed_decision_exact_order_binding() -> None:
    import json

    order, context, policy = fixture()
    result = evaluate_risk(order, context, policy)
    first = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    second = json.dumps(
        evaluate_risk(order, context, policy).to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert first == second
    assert json.loads(first)["currency"] == "KRW"
    decision = result.to_decision()
    assert decision is not None
    with pytest.raises(ValueError, match="risk_order_mismatch"):
        decision.validate_for(replace(order, quantity=Quantity(6)))


def test_risk_import_does_not_load_adapters_or_inference() -> None:
    import subprocess
    import sys

    program = """
import sys
import donghak_stock_vision.trading.risk
for prefix in ('fastapi', 'httpx', 'sqlite3', 'sklearn',
               'donghak_stock_vision.storage.sqlite', 'donghak_stock_vision.storage.analysis',
               'donghak_stock_vision.storage.decision', 'donghak_stock_vision.models'):
    assert not any(m == prefix or m.startswith(prefix + '.') for m in sys.modules), prefix
"""
    subprocess.run([sys.executable, "-c", program], check=True, timeout=10)


def test_foreign_daily_loss_is_not_compared_without_account_currency() -> None:
    order, context, policy = fixture()
    assert context.valuation is not None
    valuation = replace(
        context.valuation,
        equity=money("1000", Currency.USD),
        daily_pnl=money("-1000", Currency.USD),
    )
    assert codes(order, replace(context, valuation=valuation), policy) == (RiskCode.CURRENCY,)


def test_large_exact_fx_product_without_rounding() -> None:
    order, context, policy = fixture(US)
    quote = replace(context.quotes[0], price=money("123456789123.123456789123", Currency.USD))
    fx = replace(context.fx_rates[0], rate=Decimal("123456789123.123456789123"))
    context = replace(context, quotes=(quote,), fx_rates=(fx,))
    order = replace(order, quantity=Quantity(2**63 - 1))
    with localcontext() as exact:
        exact.prec = 128
        expected = quote.price.amount * fx.rate * order.quantity.shares
    with localcontext() as ambient:
        ambient.prec = 1
        result = evaluate_risk(order, context, policy)
    assert result.order_notional == expected
    assert RiskCode.ORDER_NOTIONAL in tuple(r.code for r in result.rejections)
    assert RiskCode.NUMERIC not in tuple(r.code for r in result.rejections)


def test_parseable_but_untyped_timestamp_owners_are_rejected() -> None:
    order, context, policy = fixture()
    assert codes(forged(order, created_at=T.isoformat()), context, policy) == (
        RiskCode.INVALID_ORDER,
    )
    assert codes(order, forged(context, evaluated_at=T.isoformat()), policy) == (
        RiskCode.INVALID_CONTEXT,
    )
    account = forged(context.account, available_at=T.isoformat())
    assert codes(order, replace(context, account=account), policy) == (RiskCode.INVALID_CONTEXT,)
    quote = forged(context.quotes[0], observed_at=T.isoformat())
    assert codes(order, replace(context, quotes=(quote,)), policy) == (RiskCode.INVALID_PRICE,)
