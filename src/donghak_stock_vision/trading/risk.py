"""Deterministic paper preflight over supplied evidence; no execution or I/O."""

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    localcontext,
)
from enum import Enum
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from donghak_stock_vision.backtest.validation import integer, money, require, sha, text, utc
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.broker import DenialReason, RiskDecision
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    Currency,
    Instrument,
    Market,
    Money,
    OrderIntent,
    OrderSide,
    Quantity,
    validate_order_batch,
)


class RiskCode(Enum):
    INVALID_ORDER = "invalid_order"
    QUANTITY = "invalid_or_nonpositive_quantity"
    MISSING_POLICY = "missing_policy"
    INVALID_POLICY = "invalid_policy"
    INVALID_CONTEXT = "invalid_context"
    DISABLED = "trading_disabled_or_unknown"
    MARKET = "instrument_market_not_allowed"
    CURRENCY = "currency_mismatch"
    ACCOUNT = "account_mismatch"
    MISSING_PRICE = "reference_price_missing"
    INVALID_PRICE = "reference_price_invalid"
    STALE_PRICE = "market_data_stale"
    FUTURE = "future_evidence"
    MISSING_FX = "fx_missing"
    INVALID_FX = "fx_invalid"
    STALE_FX = "fx_stale"
    STALE_ACCOUNT = "account_stale"
    MISSING_VALUATION = "valuation_missing"
    INVALID_VALUATION = "valuation_invalid"
    STALE_VALUATION = "valuation_stale"
    MISSING_CASH = "cash_balance_missing"
    MISSING_COST = "buy_cost_buffer_missing"
    INVALID_COST = "buy_cost_buffer_invalid"
    ORDER_NOTIONAL = "maximum_order_notional"
    CASH = "insufficient_available_cash"
    CONCENTRATION = "maximum_position_concentration"
    EXPOSURE = "maximum_portfolio_exposure"
    DAILY_LOSS = "daily_loss_limit"
    POSITION = "insufficient_sellable_position"
    NUMERIC = "numeric_bounds_exceeded"


@dataclass(frozen=True)
class MarketQuote:
    instrument: Instrument
    price: Money
    observed_at: datetime
    available_at: datetime
    source_id: str

    def __post_init__(self) -> None:
        require(isinstance(self.instrument, Instrument), "instrument_required")
        require(isinstance(self.price, Money) and self.price.amount > 0, "positive_price")
        require(self.price.currency is self.instrument.currency, "price_currency_mismatch")
        _times(self)


@dataclass(frozen=True)
class FXRate:
    """Direct target-currency units per one source-currency unit; never inverted."""

    source_currency: Currency
    target_currency: Currency
    rate: Decimal
    observed_at: datetime
    available_at: datetime
    source_id: str

    def __post_init__(self) -> None:
        require(isinstance(self.source_currency, Currency), "invalid_currency")
        require(isinstance(self.target_currency, Currency), "invalid_currency")
        require(self.source_currency is not self.target_currency, "distinct_fx_currencies")
        require(type(self.rate) is Decimal, "decimal_required")
        object.__setattr__(self, "rate", Decimal(money(self.rate, positive=True)))
        _times(self)


@dataclass(frozen=True)
class PortfolioValuation:
    """Authoritative supplied equity and daily net P&L, not recomputed by this engine."""

    account_id: str
    equity: Money
    daily_pnl: Money
    risk_date: date
    observed_at: datetime
    available_at: datetime
    source_id: str

    def __post_init__(self) -> None:
        text(self.account_id)
        require(isinstance(self.equity, Money) and self.equity.amount > 0, "positive_equity")
        require(isinstance(self.daily_pnl, Money), "pnl_required")
        require(self.equity.currency is self.daily_pnl.currency, "valuation_currency_mismatch")
        require(type(self.risk_date) is date, "risk_date_required")
        _times(self)


def _times(value: Any) -> None:
    text(value.source_id)
    object.__setattr__(value, "observed_at", utc(value.observed_at))
    object.__setattr__(value, "available_at", utc(value.available_at))
    require(value.observed_at <= value.available_at, "evidence_time_order")


@dataclass(frozen=True)
class RiskPolicy:
    """All limits and clocks must be explicitly supplied. No trading defaults."""

    policy_id: str
    account_currency: Currency
    allowed_markets: frozenset[Market]
    max_order_notional: Money
    max_position_fraction: Decimal
    max_exposure_fraction: Decimal
    max_daily_loss: Money
    max_price_age_seconds: int
    max_fx_age_seconds: int
    max_account_age_seconds: int
    max_valuation_age_seconds: int
    risk_timezone: str
    version: ClassVar[str] = "paper_pre_trade_policy_v1"

    def __post_init__(self) -> None:
        text(self.policy_id)
        require(isinstance(self.account_currency, Currency), "invalid_currency")
        require(
            type(self.allowed_markets) is frozenset and bool(self.allowed_markets),
            "markets_required",
        )
        require(all(isinstance(m, Market) for m in self.allowed_markets), "invalid_market")
        for amount in (self.max_order_notional, self.max_daily_loss):
            require(isinstance(amount, Money) and amount.amount > 0, "positive_limit")
            require(amount.currency is self.account_currency, "limit_currency_mismatch")
        for fraction in (self.max_position_fraction, self.max_exposure_fraction):
            require(type(fraction) is Decimal, "decimal_required")
            money(fraction, positive=True)
            require(fraction <= 1, "unlevered_fraction_required")
        for age in (
            self.max_price_age_seconds,
            self.max_fx_age_seconds,
            self.max_account_age_seconds,
            self.max_valuation_age_seconds,
        ):
            integer(age)
        text(self.risk_timezone)
        ZoneInfo(self.risk_timezone)


@dataclass(frozen=True)
class RiskContext:
    account: AccountSnapshot
    account_currency: Currency
    account_complete: bool | None
    valuation: PortfolioValuation | None
    quotes: tuple[MarketQuote, ...]
    fx_rates: tuple[FXRate, ...]
    pending_orders: tuple[OrderIntent, ...]
    buy_cost_buffer: Money | None
    trading_enabled: bool | None
    evaluated_at: datetime

    def __post_init__(self) -> None:
        require(isinstance(self.account, AccountSnapshot), "account_required")
        require(isinstance(self.account_currency, Currency), "invalid_currency")
        for state in (self.account_complete, self.trading_enabled):
            require(state is None or type(state) is bool, "boolean_required")
        require(
            self.valuation is None or isinstance(self.valuation, PortfolioValuation),
            "valuation_required",
        )
        require(
            self.buy_cost_buffer is None or isinstance(self.buy_cost_buffer, Money),
            "money_required",
        )
        require(
            type(self.quotes) is tuple and all(isinstance(q, MarketQuote) for q in self.quotes),
            "quotes_required",
        )
        require(
            type(self.fx_rates) is tuple and all(isinstance(f, FXRate) for f in self.fx_rates),
            "fx_required",
        )
        require(len({q.instrument for q in self.quotes}) == len(self.quotes), "duplicate_quote")
        require(
            len({(f.source_currency, f.target_currency) for f in self.fx_rates})
            == len(self.fx_rates),
            "duplicate_fx",
        )
        validate_order_batch(self.pending_orders)
        object.__setattr__(self, "evaluated_at", utc(self.evaluated_at))


@dataclass(frozen=True)
class RiskRejection:
    code: RiskCode
    field: str

    def __post_init__(self) -> None:
        require(isinstance(self.code, RiskCode), "invalid_risk_code")
        text(self.field)


@dataclass(frozen=True)
class RiskAssessment:
    order: OrderIntent | None
    rejections: tuple[RiskRejection, ...]
    evidence_id: str | None
    order_notional: Decimal | None = None
    projected_position: Decimal | None = None
    projected_exposure: Decimal | None = None
    currency: Currency | None = None
    version: ClassVar[str] = "paper_pre_trade_risk_v1"

    def __post_init__(self) -> None:
        require(type(self.rejections) is tuple, "immutable_rejections")
        require(all(isinstance(r, RiskRejection) for r in self.rejections), "invalid_rejection")
        require(len(set(self.rejections)) == len(self.rejections), "duplicate_rejection")
        require(self.order is None or isinstance(self.order, OrderIntent), "order_intent_required")
        require(self.currency is None or isinstance(self.currency, Currency), "invalid_currency")
        if self.evidence_id is not None:
            sha(self.evidence_id)
        require(
            bool(self.rejections) or (self.order is not None and self.evidence_id is not None),
            "unproven_allow",
        )
        for value in (self.order_notional, self.projected_position, self.projected_exposure):
            require(
                value is None or (type(value) is Decimal and value.is_finite() and value >= 0),
                "invalid_metric",
            )

    @property
    def allowed(self) -> bool:
        return not self.rejections

    def to_decision(self) -> RiskDecision | None:
        """Reuse the existing paper port contract; malformed orders cannot be submitted."""
        if self.order is None:
            return None
        limits = {
            RiskCode.ORDER_NOTIONAL,
            RiskCode.CASH,
            RiskCode.CONCENTRATION,
            RiskCode.EXPOSURE,
            RiskCode.DAILY_LOSS,
            RiskCode.POSITION,
        }
        reasons = tuple(
            dict.fromkeys(
                DenialReason.RISK_LIMIT if r.code in limits else DenialReason.ELIGIBILITY_DENIED
                for r in self.rejections
            )
        )
        return RiskDecision(
            self.order.order_id,
            allowed=self.allowed,
            reasons=reasons,
            evidence_id=self.evidence_id,
            order=self.order,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "allowed": self.allowed,
            "order_id": self.order.order_id if self.order else None,
            "evidence_id": self.evidence_id,
            "currency": _encode(self.currency),
            "rejections": [_encode(r) for r in self.rejections],
            "order_notional": _encode(self.order_notional),
            "projected_position": _encode(self.projected_position),
            "projected_exposure": _encode(self.projected_exposure),
        }


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_encode(v) for v in value]
    if isinstance(value, frozenset):
        return sorted(_encode(v) for v in value)
    return value


def _revalidate(value: Any) -> None:
    """Recheck supplied immutable owners, including nested values; no trust cache."""
    if is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            _revalidate(getattr(value, f.name))
        require(replace(value) == value, "noncanonical_value")
    elif isinstance(value, (tuple, frozenset)):
        for child in value:
            _revalidate(child)


def evaluate_risk(
    order: OrderIntent | None, context: RiskContext | None, policy: RiskPolicy | None
) -> RiskAssessment:
    """Evaluate only caller-supplied evidence at evaluated_at; no clock or I/O.

    Invalid structural inputs deny before dependent arithmetic. Independent valid
    checks accumulate in deterministic check order. Values are in account currency.
    """
    errors: list[RiskRejection] = []

    def deny(code: RiskCode, field: str) -> None:
        rejection = RiskRejection(code, field)
        if rejection not in errors:
            errors.append(rejection)

    valid_order = order
    for input_value, cls, code, field in (
        (order, OrderIntent, RiskCode.INVALID_ORDER, "order"),
        (
            policy,
            RiskPolicy,
            RiskCode.MISSING_POLICY if policy is None else RiskCode.INVALID_POLICY,
            "policy",
        ),
        (context, RiskContext, RiskCode.INVALID_CONTEXT, "context"),
    ):
        try:
            require(isinstance(input_value, cls), "required_input")
            if isinstance(input_value, RiskContext):
                require(replace(input_value) == input_value, "noncanonical_context")
            else:
                _revalidate(input_value)
        except (ValueError, TypeError, AttributeError, ArithmeticError, KeyError):
            if field == "order" and isinstance(order, OrderIntent):
                try:
                    require(isinstance(order.quantity, Quantity), "quantity_required")
                    integer(order.quantity.shares, positive=True)
                except (ValueError, TypeError, AttributeError):
                    code = RiskCode.QUANTITY
            deny(code, field)
            if field == "order":
                valid_order = None
    if errors:
        return RiskAssessment(valid_order, tuple(errors), None)
    assert order is not None and context is not None and policy is not None

    for item, code, field in (
        (context.account, RiskCode.INVALID_CONTEXT, "account"),
        (context.valuation, RiskCode.INVALID_VALUATION, "valuation"),
        (context.buy_cost_buffer, RiskCode.INVALID_CONTEXT, "buy_cost_buffer"),
        (context.pending_orders, RiskCode.INVALID_CONTEXT, "pending_orders"),
        *((q, RiskCode.INVALID_PRICE, f"quotes[{i}]") for i, q in enumerate(context.quotes)),
        *((f, RiskCode.INVALID_FX, f"fx_rates[{i}]") for i, f in enumerate(context.fx_rates)),
    ):
        try:
            _revalidate(item)
        except (ValueError, TypeError, AttributeError, ArithmeticError, KeyError):
            deny(code, field)
    if errors:
        return RiskAssessment(order, tuple(errors), None)

    now = context.evaluated_at

    def fresh(
        observed: datetime, available: datetime, age: int, stale: RiskCode, field: str
    ) -> bool:
        if observed > now or available > now:
            deny(RiskCode.FUTURE, field)
            return False
        elapsed = now - observed
        seconds = elapsed.days * 86400 + elapsed.seconds
        if seconds > age or (seconds == age and elapsed.microseconds > 0):
            deny(stale, field)
            return False
        return True

    if context.trading_enabled is not True:
        deny(RiskCode.DISABLED, "trading_enabled")
    if context.account_complete is not True:
        deny(RiskCode.INVALID_CONTEXT, "account_complete")
    if context.account.account_id != order.account_id:
        deny(RiskCode.ACCOUNT, "account")
    if context.account_currency is not policy.account_currency:
        deny(RiskCode.CURRENCY, "account_currency")
    if order.instrument.market not in policy.allowed_markets:
        deny(RiskCode.MARKET, "order.instrument")
    if order.created_at > now:
        deny(RiskCode.FUTURE, "order.created_at")
    fresh(
        context.account.observed_at,
        context.account.available_at,
        policy.max_account_age_seconds,
        RiskCode.STALE_ACCOUNT,
        "account",
    )

    currency = policy.account_currency
    quotes = {q.instrument: q for q in context.quotes}
    rates = {(f.source_currency, f.target_currency): f for f in context.fx_rates}

    def mark(instrument: Instrument, *, limit: Money | None = None) -> Decimal | None:
        label = f"{instrument.exchange.value}:{instrument.symbol}"
        quote = quotes.get(instrument)
        if quote is None:
            deny(RiskCode.MISSING_PRICE, f"quotes.{label}")
            return None
        if not fresh(
            quote.observed_at,
            quote.available_at,
            policy.max_price_age_seconds,
            RiskCode.STALE_PRICE,
            f"quotes.{label}",
        ):
            return None
        rate = Decimal(1)
        if instrument.currency is not currency:
            fx = rates.get((instrument.currency, currency))
            if fx is None:
                deny(RiskCode.MISSING_FX, f"fx.{instrument.currency.value}/{currency.value}")
                return None
            if not fresh(
                fx.observed_at,
                fx.available_at,
                policy.max_fx_age_seconds,
                RiskCode.STALE_FX,
                f"fx.{instrument.currency.value}/{currency.value}",
            ):
                return None
            rate = fx.rate
        return max(quote.price.amount, limit.amount if limit else quote.price.amount) * rate

    notional = concentration = exposure = None
    try:
        arithmetic = Context(
            prec=128,
            rounding=ROUND_HALF_EVEN,
            Emin=-999999,
            Emax=999999,
            capitals=1,
            clamp=0,
            traps=[Inexact, Rounded, InvalidOperation, DivisionByZero, Overflow],
        )
        with localcontext(arithmetic):
            valuation = context.valuation
            equity = None
            if valuation is None:
                deny(RiskCode.MISSING_VALUATION, "valuation")
            else:
                if valuation.account_id != order.account_id:
                    deny(RiskCode.ACCOUNT, "valuation")
                if valuation.equity.currency is not currency:
                    deny(RiskCode.CURRENCY, "valuation")
                elif fresh(
                    valuation.observed_at,
                    valuation.available_at,
                    policy.max_valuation_age_seconds,
                    RiskCode.STALE_VALUATION,
                    "valuation",
                ):
                    equity = valuation.equity.amount
                zone = ZoneInfo(policy.risk_timezone)
                risk_date = now.astimezone(zone).date()
                if (
                    valuation.risk_date != risk_date
                    or valuation.observed_at.astimezone(zone).date() != valuation.risk_date
                ):
                    deny(RiskCode.INVALID_VALUATION, "valuation.risk_date")
                if (
                    valuation.daily_pnl.currency is currency
                    and valuation.daily_pnl.amount <= -policy.max_daily_loss.amount
                ):
                    deny(RiskCode.DAILY_LOSS, "valuation.daily_pnl")

            price = mark(order.instrument, limit=order.limit_price)
            if price is not None:
                notional = price * order.quantity.shares
                if notional > policy.max_order_notional.amount:
                    deny(RiskCode.ORDER_NOTIONAL, "order.notional")
            balance = next(
                (b for b in context.account.balances if b.available.currency is currency), None
            )
            if balance is None:
                deny(RiskCode.MISSING_CASH, "account.balances")
            if order.side is OrderSide.BUY:
                buffer = context.buy_cost_buffer
                if buffer is None:
                    deny(RiskCode.MISSING_COST, "buy_cost_buffer")
                elif buffer.currency is not currency:
                    deny(RiskCode.CURRENCY, "buy_cost_buffer")
                elif buffer.amount < 0:
                    deny(RiskCode.INVALID_COST, "buy_cost_buffer")
                elif notional is not None and balance is not None:
                    if notional + buffer.amount > balance.available.amount:
                        deny(RiskCode.CASH, "account.available_cash")
            else:
                position = next(
                    (p for p in context.account.positions if p.instrument == order.instrument), None
                )
                if position is None or position.sellable.shares < order.quantity.shares:
                    deny(RiskCode.POSITION, "account.sellable_quantity")

            values: dict[Instrument, Decimal] = {}
            coverage = True
            for position in context.account.positions:
                if position.quantity.shares:
                    value = mark(position.instrument)
                    if value is None:
                        coverage = False
                    else:
                        values[position.instrument] = value * position.quantity.shares
            for pending in context.pending_orders:
                if pending.order_id == order.order_id or pending.account_id != order.account_id:
                    deny(RiskCode.ACCOUNT, "pending_orders")
                if pending.created_at > now:
                    deny(RiskCode.FUTURE, "pending_orders")
                if pending.side is OrderSide.BUY:
                    value = mark(pending.instrument, limit=pending.limit_price)
                    if value is None:
                        coverage = False
                    else:
                        values[pending.instrument] = (
                            values.get(pending.instrument, Decimal(0))
                            + value * pending.quantity.shares
                        )
            if price is not None and coverage:
                current = values.get(order.instrument, Decimal(0))
                if order.side is OrderSide.BUY:
                    assert notional is not None
                    projected = current + notional
                else:
                    # Reduce exposure by the reference mark, never by hoped-for sale proceeds.
                    reference = mark(order.instrument)
                    assert reference is not None
                    projected = current - reference * order.quantity.shares
                if projected >= 0:
                    values[order.instrument] = projected
                    concentration = projected
                    exposure = sum(values.values(), Decimal(0))
                    if equity is not None:
                        # Check other instruments too, including existing concentration.
                        if any(v > equity * policy.max_position_fraction for v in values.values()):
                            deny(RiskCode.CONCENTRATION, "projected_positions")
                        if exposure > equity * policy.max_exposure_fraction:
                            deny(RiskCode.EXPOSURE, "projected_exposure")
    except ArithmeticError:
        deny(RiskCode.NUMERIC, "arithmetic")
        notional = concentration = exposure = None

    evidence = digest(
        {
            "version": RiskAssessment.version,
            "policy_version": policy.version,
            "policy": _encode(policy),
            "context": _encode(context),
            "order": _encode(order),
            "rejections": _encode(tuple(errors)),
        }
    )
    return RiskAssessment(
        order, tuple(errors), evidence, notional, concentration, exposure, currency
    )
