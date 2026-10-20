"""Immutable broker-neutral descriptions; no accounting, execution or I/O."""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from donghak_stock_vision.backtest.research_contract import OrderSide as OrderSide
from donghak_stock_vision.backtest.validation import integer, money, require, text, utc
from donghak_stock_vision.data.schema import validate_ticker


class Market(Enum):
    KR = "KR"
    US = "US"


class Currency(Enum):
    KRW = "KRW"
    USD = "USD"


class Exchange(Enum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"

    @property
    def market(self) -> Market:
        return Market.KR if self in {Exchange.KOSPI, Exchange.KOSDAQ} else Market.US

    @property
    def currency(self) -> Currency:
        return Currency.KRW if self.market is Market.KR else Currency.USD


@dataclass(frozen=True)
class Instrument:
    """Exchange-qualified symbol, not proof of listing, identity history or eligibility."""

    symbol: str
    exchange: Exchange
    currency: Currency

    def __post_init__(self) -> None:
        require(isinstance(self.exchange, Exchange), "invalid_exchange")
        require(isinstance(self.currency, Currency), "invalid_currency")
        require(self.currency is self.exchange.currency, "instrument_currency_mismatch")
        if self.market is Market.KR:
            validate_ticker(self.symbol)
        else:
            require(
                isinstance(self.symbol, str)
                and re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*", self.symbol) is not None
                and len(self.symbol) <= 32,
                "invalid_us_symbol",
            )

    @property
    def market(self) -> Market:
        return self.exchange.market


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        require(isinstance(self.currency, Currency), "invalid_currency")
        require(type(self.amount) is Decimal, "decimal_required")
        object.__setattr__(self, "amount", Decimal(money(self.amount, signed=True)))


@dataclass(frozen=True)
class Quantity:
    """Whole shares only in v1; fractional shares and shorts require a later contract."""

    shares: int

    def __post_init__(self) -> None:
        integer(self.shares)


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIAL = "partial"
    CANCEL_PENDING = "cancel_pending"
    UNKNOWN = "unknown"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


def validate_transition(previous: OrderStatus, following: OrderStatus) -> None:
    """Validate an explicit observed transition; never infer a fill or cash change.

    UNKNOWN is deliberately quarantined. Reconciliation needs independent evidence
    and a future adapter contract, rather than a permissive status transition.
    """
    require(
        isinstance(previous, OrderStatus) and isinstance(following, OrderStatus), "invalid_status"
    )
    transitions = {
        OrderStatus.SUBMITTED: {OrderStatus.OPEN, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
        OrderStatus.OPEN: {
            OrderStatus.PARTIAL,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
            OrderStatus.UNKNOWN,
        },
        OrderStatus.PARTIAL: {
            OrderStatus.PARTIAL,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.UNKNOWN,
        },
        OrderStatus.CANCEL_PENDING: {
            OrderStatus.PARTIAL,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.UNKNOWN,
        },
    }
    require(following in transitions.get(previous, set()), "invalid_order_transition")


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    account_id: str
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: Quantity
    created_at: datetime
    source_id: str
    limit_price: Money | None = None

    def __post_init__(self) -> None:
        for value in (self.order_id, self.account_id, self.source_id):
            text(value)
        require(isinstance(self.instrument, Instrument), "instrument_required")
        require(
            isinstance(self.side, OrderSide) and isinstance(self.order_type, OrderType),
            "invalid_order",
        )
        require(
            isinstance(self.quantity, Quantity) and self.quantity.shares > 0, "positive_quantity"
        )
        object.__setattr__(self, "created_at", utc(self.created_at))
        require(
            (self.limit_price is not None) == (self.order_type is OrderType.LIMIT), "limit_price"
        )
        if self.limit_price is not None:
            require(isinstance(self.limit_price, Money), "money_required")
            require(
                self.limit_price.currency is self.instrument.currency, "price_currency_mismatch"
            )
            require(self.limit_price.amount > 0, "positive_price")


def validate_order_batch(orders: tuple[OrderIntent, ...]) -> None:
    """Reject duplicate caller identities within this explicit batch, including identical intents.

    Durable cross-batch idempotency remains the adapter/owner's responsibility.
    """
    require(type(orders) is tuple, "immutable_orders_required")
    require(all(isinstance(order, OrderIntent) for order in orders), "order_intent_required")
    require(len({order.order_id for order in orders}) == len(orders), "duplicate_order_id")


@dataclass(frozen=True)
class FillReceipt:
    """Supplied execution facts, not an instruction or an independently computed ledger."""

    fill_id: str
    order: OrderIntent
    quantity: Quantity
    price: Money
    fees: Money
    taxes: Money
    executed_at: datetime
    available_at: datetime
    source_id: str

    def __post_init__(self) -> None:
        text(self.fill_id)
        text(self.source_id)
        require(isinstance(self.order, OrderIntent), "order_intent_required")
        require(isinstance(self.quantity, Quantity), "quantity_required")
        require(0 < self.quantity.shares <= self.order.quantity.shares, "invalid_fill_quantity")
        for value in (self.price, self.fees, self.taxes):
            require(isinstance(value, Money), "money_required")
            require(value.currency is self.order.instrument.currency, "fill_currency_mismatch")
            require(value.amount >= 0, "negative_fill_amount")
        require(self.price.amount > 0, "positive_price")
        object.__setattr__(self, "executed_at", utc(self.executed_at))
        object.__setattr__(self, "available_at", utc(self.available_at))
        require(self.order.created_at <= self.executed_at <= self.available_at, "fill_time_order")


def validate_fills(order: OrderIntent, fills: tuple[FillReceipt, ...]) -> None:
    require(isinstance(order, OrderIntent), "order_intent_required")
    require(type(fills) is tuple, "immutable_fills_required")
    require(all(isinstance(fill, FillReceipt) for fill in fills), "fill_receipt_required")
    require(len({fill.fill_id for fill in fills}) == len(fills), "duplicate_fill_id")
    require(all(fill.order == order for fill in fills), "fill_order_mismatch")
    require(sum(fill.quantity.shares for fill in fills) <= order.quantity.shares, "overfill")


@dataclass(frozen=True)
class CashBalance:
    available: Money
    reserved: Money

    def __post_init__(self) -> None:
        require(
            isinstance(self.available, Money) and isinstance(self.reserved, Money), "money_required"
        )
        require(self.available.currency is self.reserved.currency, "balance_currency_mismatch")
        require(self.available.amount >= 0 and self.reserved.amount >= 0, "negative_cash")


@dataclass(frozen=True)
class Position:
    instrument: Instrument
    quantity: Quantity
    sellable: Quantity
    reserved: Quantity

    def __post_init__(self) -> None:
        require(isinstance(self.instrument, Instrument), "instrument_required")
        require(
            all(isinstance(q, Quantity) for q in (self.quantity, self.sellable, self.reserved)),
            "quantity_required",
        )
        require(
            self.sellable.shares + self.reserved.shares <= self.quantity.shares,
            "position_reservation",
        )


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    balances: tuple[CashBalance, ...]
    positions: tuple[Position, ...]
    observed_at: datetime
    available_at: datetime
    source_id: str

    def __post_init__(self) -> None:
        text(self.account_id)
        text(self.source_id)
        require(type(self.balances) is tuple and type(self.positions) is tuple, "immutable_account")
        require(all(isinstance(b, CashBalance) for b in self.balances), "balance_required")
        require(all(isinstance(p, Position) for p in self.positions), "position_required")
        require(
            len({b.available.currency for b in self.balances}) == len(self.balances),
            "duplicate_currency",
        )
        require(
            len({p.instrument for p in self.positions}) == len(self.positions), "duplicate_position"
        )
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        object.__setattr__(self, "available_at", utc(self.available_at))
        require(self.observed_at <= self.available_at, "account_time_order")
