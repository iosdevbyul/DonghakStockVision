"""Explicit synthetic mechanics and immutable paper command/receipt values."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from decimal import (
    ROUND_CEILING,
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
from typing import Any, ClassVar, Literal

from donghak_stock_vision.backtest.validation import integer, money, require, text, utc
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.broker import RiskDecision
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    Currency,
    Exchange,
    FillReceipt,
    Money,
    OrderIntent,
    OrderStatus,
    Quantity,
)
from donghak_stock_vision.trading.risk import (
    FXRate,
    MarketQuote,
    PortfolioValuation,
    RiskAssessment,
    RiskPolicy,
)


@contextmanager
def exact() -> Iterator[None]:
    with localcontext(
        Context(
            prec=128,
            rounding=ROUND_HALF_EVEN,
            Emin=-999999,
            Emax=999999,
            capitals=1,
            clamp=0,
            traps=[Inexact, Rounded, InvalidOperation, DivisionByZero, Overflow],
        )
    ):
        yield


def rounded(value: Decimal, quantum: Decimal, direction: str = ROUND_CEILING) -> Decimal:
    # Only explicitly declared fixture price/cost quantization can round.
    with localcontext() as context:
        context.traps[Inexact] = context.traps[Rounded] = False
        return value.quantize(quantum, rounding=direction)


def wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: wire(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [wire(v) for v in value]
    if isinstance(value, frozenset):
        return sorted(wire(v) for v in value)
    # PortfolioValuation includes a calendar risk date.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def fingerprint(value: Any) -> str:
    return digest(wire(value))


def validate_owner(value: Any) -> None:
    """Revalidate supplied dataclass owners independently, without normalization in place."""
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            validate_owner(getattr(value, field.name))
        require(replace(value) == value, "noncanonical_paper_input")
    elif isinstance(value, tuple):
        for item in value:
            validate_owner(item)


@dataclass(frozen=True)
class CurrencyMechanics:
    currency: Currency
    price_quantum: Decimal
    cost_quantum: Decimal

    def __post_init__(self) -> None:
        require(isinstance(self.currency, Currency), "invalid_currency")
        for name in ("price_quantum", "cost_quantum"):
            value = getattr(self, name)
            require(type(value) is Decimal, "decimal_required")
            value = Decimal(money(value, positive=True))
            require(value.as_tuple().digits == (1,), "power_of_ten_quantum_required")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class FixturePolicy:
    policy_id: str
    risk_policies: tuple[RiskPolicy, ...]
    currencies: tuple[CurrencyMechanics, ...]
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    sell_tax_rate: Decimal
    slippage_rate: Decimal
    max_session_age_seconds: int
    max_commands: int
    version: ClassVar[str] = "synthetic_paper_mechanics_v1"

    def __post_init__(self) -> None:
        text(self.policy_id)
        require(
            type(self.risk_policies) is tuple and bool(self.risk_policies), "risk_policies_required"
        )
        require(all(isinstance(p, RiskPolicy) for p in self.risk_policies), "risk_policy_required")
        require(
            type(self.currencies) is tuple and bool(self.currencies), "currency_mechanics_required"
        )
        require(
            all(isinstance(c, CurrencyMechanics) for c in self.currencies),
            "currency_mechanics_required",
        )
        currencies = {c.currency for c in self.currencies}
        require(len(currencies) == len(self.currencies), "duplicate_currency_mechanics")
        require(
            len({p.account_currency for p in self.risk_policies}) == len(self.risk_policies),
            "duplicate_risk_currency",
        )
        require(
            {p.account_currency for p in self.risk_policies} == currencies, "risk_currency_coverage"
        )
        for name in ("buy_fee_rate", "sell_fee_rate", "sell_tax_rate", "slippage_rate"):
            value = getattr(self, name)
            require(type(value) is Decimal, "decimal_required")
            value = Decimal(money(value))
            require(value < 1, "invalid_fixture_rate")
            object.__setattr__(self, name, value)
        with exact():
            require(self.sell_fee_rate + self.sell_tax_rate < 1, "invalid_total_sell_rate")
        integer(self.max_session_age_seconds)
        integer(self.max_commands, positive=True)


@dataclass(frozen=True)
class MarketSession:
    exchange: Exchange
    is_open: bool | None
    observed_at: datetime
    available_at: datetime
    source_id: str

    def __post_init__(self) -> None:
        require(isinstance(self.exchange, Exchange), "invalid_exchange")
        require(self.is_open is None or type(self.is_open) is bool, "boolean_required")
        text(self.source_id)
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        object.__setattr__(self, "available_at", utc(self.available_at))
        require(self.observed_at <= self.available_at, "session_time_order")


@dataclass(frozen=True)
class PaperFrame:
    frame_id: str
    now: datetime
    quotes: tuple[MarketQuote, ...]
    fx_rates: tuple[FXRate, ...]
    valuations: tuple[PortfolioValuation, ...]
    sessions: tuple[MarketSession, ...]
    trading_enabled: bool | None

    def __post_init__(self) -> None:
        text(self.frame_id)
        object.__setattr__(self, "now", utc(self.now))
        for items, cls in (
            (self.quotes, MarketQuote),
            (self.fx_rates, FXRate),
            (self.valuations, PortfolioValuation),
            (self.sessions, MarketSession),
        ):
            require(
                type(items) is tuple and all(isinstance(i, cls) for i in items),
                "immutable_frame_required",
            )
        require(len({q.instrument for q in self.quotes}) == len(self.quotes), "duplicate_quote")
        require(
            len({(f.source_currency, f.target_currency) for f in self.fx_rates})
            == len(self.fx_rates),
            "duplicate_fx",
        )
        require(
            len({v.equity.currency for v in self.valuations}) == len(self.valuations),
            "duplicate_valuation",
        )
        require(len({s.exchange for s in self.sessions}) == len(self.sessions), "duplicate_session")
        require(
            self.trading_enabled is None or type(self.trading_enabled) is bool, "boolean_required"
        )


@dataclass(frozen=True)
class Submission:
    order: OrderIntent
    idempotency_key: str
    risk: RiskDecision | None


@dataclass(frozen=True)
class FillEvent:
    event_id: str
    order_id: str
    quantity: Quantity
    at: datetime
    source_id: str

    def __post_init__(self) -> None:
        for value in (self.event_id, self.order_id, self.source_id):
            text(value)
        require(
            isinstance(self.quantity, Quantity) and self.quantity.shares > 0,
            "positive_fill_quantity",
        )
        object.__setattr__(self, "at", utc(self.at))


@dataclass(frozen=True)
class Cancellation:
    order_id: str
    idempotency_key: str
    at: datetime


@dataclass(frozen=True)
class PaperOrder:
    intent: OrderIntent
    status: OrderStatus
    remaining: int
    filled: int
    closed: int
    reserved_cash: Money
    reserved_quantity: int
    unit_reserve: Money
    price_cap: Money | None
    accepted_at: datetime | None
    risk: RiskAssessment
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PaperReceipt:
    operation: str
    order_id: str
    order_status: OrderStatus
    outcome: Literal["accepted", "rejected", "filled", "pending", "cancelled", "unchanged"]
    reasons: tuple[str, ...]
    at: datetime
    risk: RiskAssessment | None = None
    fill: FillReceipt | None = None
    benchmark: Money | None = None
    slippage: Money | None = None
    frame_id: str | None = None
    policy_id: str | None = None
    command_key: str | None = None
    quote_source_id: str | None = None
    session_source_id: str | None = None
    version: ClassVar[str] = "synthetic_paper_receipt_v1"
    simulated: ClassVar[bool] = True

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = wire(self)
        return {
            "version": self.version,
            "simulated": True,
            **result,
            "risk": self.risk.to_dict() if self.risk else None,
        }


Payload = PaperFrame | Submission | FillEvent | Cancellation


@dataclass(frozen=True)
class PaperCommand:
    kind: Literal["advance", "submit", "fill", "cancel"]
    key: str
    payload: Payload


@dataclass(frozen=True)
class RecordedCommand:
    command: PaperCommand
    receipt: PaperReceipt | None


@dataclass(frozen=True)
class PaperState:
    account: AccountSnapshot
    frame: PaperFrame
    orders: tuple[PaperOrder, ...]
    fills: tuple[FillReceipt, ...]
    records: tuple[RecordedCommand, ...]
    revision: int


@dataclass(frozen=True)
class PaperCheckpoint:
    initial_account: AccountSnapshot
    policy: FixturePolicy
    initial_frame: PaperFrame
    commands: tuple[PaperCommand, ...]
    state_id: str
    version: ClassVar[str] = "synthetic_paper_checkpoint_v1"
