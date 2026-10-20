"""Offline domain contracts; no broker, inference or economic calculation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

import pytest
from tests.backtest.helpers import payload

from donghak_stock_vision.backtest.contracts import VirtualOrder
from donghak_stock_vision.backtest.research_contract import OrderSide as LegacyOrderSide
from donghak_stock_vision.trading.broker import (
    BrokerReader,
    DenialReason,
    ExecutionMode,
    PaperBroker,
    RiskDecision,
)
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    CashBalance,
    Currency,
    Exchange,
    FillReceipt,
    Instrument,
    Market,
    Money,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quantity,
    validate_fills,
    validate_order_batch,
    validate_transition,
)

T = datetime(2026, 1, 5, tzinfo=UTC)
KR = Instrument("00088K", Exchange.KOSPI, Currency.KRW)
US = Instrument("BRK.B", Exchange.NYSE, Currency.USD)


def cash(amount: str = "10", currency: Currency = Currency.KRW) -> Money:
    return Money(Decimal(amount), currency)


def intent() -> OrderIntent:
    return OrderIntent(
        "o1", "a1", KR, OrderSide.BUY, OrderType.MARKET, Quantity(10), T, "decision1"
    )


def receipt() -> FillReceipt:
    return FillReceipt("f1", intent(), Quantity(4), cash(), cash("0"), cash("0"), T, T, "source1")


@pytest.mark.parametrize(
    "exchange,market,currency,symbol",
    [
        (Exchange.KOSPI, Market.KR, Currency.KRW, "00088K"),
        (Exchange.KOSDAQ, Market.KR, Currency.KRW, "123456"),
        (Exchange.NYSE, Market.US, Currency.USD, "BRK.B"),
        (Exchange.NASDAQ, Market.US, Currency.USD, "AAPL"),
    ],
)
def test_instrument(exchange: Exchange, market: Market, currency: Currency, symbol: str) -> None:
    value = Instrument(symbol, exchange, currency)
    assert value.market is market
    assert value.symbol == symbol


@pytest.mark.parametrize("symbol", ["aapl", "", "../AAPL", "A A", "A/US"])
def test_invalid_symbol(symbol: str) -> None:
    with pytest.raises(ValueError):
        Instrument(symbol, Exchange.NASDAQ, Currency.USD)


def test_currency_and_enum_boundaries() -> None:
    with pytest.raises(ValueError, match="instrument_currency_mismatch"):
        Instrument("005930", Exchange.KOSPI, Currency.USD)
    with pytest.raises(ValueError, match="invalid_exchange"):
        Instrument("005930", "KOSPI", Currency.KRW)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Instrument("12345", Exchange.KOSDAQ, Currency.KRW)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("1e30"), 1.1, "1"])
def test_invalid_money(value: Decimal) -> None:
    with pytest.raises(ValueError):
        Money(value, Currency.USD)


def test_decimal_context_and_immutability() -> None:
    with localcontext() as context:
        context.prec = 1
        value = cash("123456.7800")
    assert str(value.amount) == "123456.78"
    assert value == cash("123456.78")
    assert cash("-1").amount == Decimal("-1")  # Signed values, e.g. supplied P&L.


@pytest.mark.parametrize("value", [-1, True, 0.5, Decimal("1"), 2**63])
def test_invalid_quantity(value: int) -> None:
    with pytest.raises(ValueError):
        Quantity(value)


def test_order_invariants() -> None:
    for changes in (
        {"order_id": ""},
        {"account_id": " "},
        {"source_id": ""},
        {"quantity": Quantity(0)},
        {"created_at": T.replace(tzinfo=None)},
        {"side": "BUY"},
        {"order_type": OrderType.LIMIT},
        {"limit_price": cash()},
        {"order_type": OrderType.LIMIT, "limit_price": cash("1", Currency.USD)},
        {"order_type": OrderType.LIMIT, "limit_price": cash("0")},
    ):
        with pytest.raises(ValueError):
            replace(intent(), **changes)
    assert replace(intent(), order_type=OrderType.LIMIT, limit_price=cash()).limit_price == cash()


def test_order_identities() -> None:
    validate_order_batch((intent(), replace(intent(), order_id="o2")))
    for duplicate in (intent(), replace(intent(), side=OrderSide.SELL)):
        with pytest.raises(ValueError, match="duplicate_order_id"):
            validate_order_batch((intent(), duplicate))


@pytest.mark.parametrize(
    "before,after",
    [
        (OrderStatus.SUBMITTED, OrderStatus.OPEN),
        (OrderStatus.SUBMITTED, OrderStatus.REJECTED),
        (OrderStatus.OPEN, OrderStatus.PARTIAL),
        (OrderStatus.PARTIAL, OrderStatus.PARTIAL),
        (OrderStatus.PARTIAL, OrderStatus.FILLED),
        (OrderStatus.OPEN, OrderStatus.EXPIRED),
        (OrderStatus.CANCEL_PENDING, OrderStatus.FILLED),
        (OrderStatus.CANCEL_PENDING, OrderStatus.CANCELLED),
    ],
)
def test_observed_transitions(before: OrderStatus, after: OrderStatus) -> None:
    validate_transition(before, after)


@pytest.mark.parametrize(
    "before",
    [
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.UNKNOWN,
    ],
)
def test_terminal_and_unknown_cannot_reopen(before: OrderStatus) -> None:
    with pytest.raises(ValueError, match="invalid_order_transition"):
        validate_transition(before, OrderStatus.OPEN)


def test_no_inferred_acceptance() -> None:
    with pytest.raises(ValueError):
        validate_transition(OrderStatus.SUBMITTED, OrderStatus.FILLED)


def test_fills_bind_full_intent_and_preserve_source() -> None:
    first = receipt()
    second = replace(first, fill_id="f2", quantity=Quantity(6))
    validate_fills(intent(), (first, second))
    assert first.source_id == "source1" and first.available_at == T
    for fills in ((first, first), (second, replace(second, fill_id="f3"))):
        with pytest.raises(ValueError):
            validate_fills(intent(), fills)
    with pytest.raises(ValueError, match="fill_order_mismatch"):
        validate_fills(replace(intent(), side=OrderSide.SELL), (first,))


def test_invalid_fills() -> None:
    for changes in (
        {"fill_id": ""},
        {"source_id": ""},
        {"quantity": Quantity(0)},
        {"quantity": Quantity(11)},
        {"price": cash("0")},
        {"fees": cash("-1")},
        {"taxes": cash("-1")},
        {"price": cash("1", Currency.USD)},
        {"executed_at": T - timedelta(seconds=1)},
        {"available_at": T - timedelta(seconds=1)},
        {"executed_at": T.replace(tzinfo=None)},
    ):
        with pytest.raises(ValueError):
            replace(receipt(), **changes)


def test_account_invariants() -> None:
    balance = CashBalance(cash(), cash("0"))
    position = Position(KR, Quantity(10), Quantity(6), Quantity(4))
    snapshot = AccountSnapshot("a", (balance,), (position,), T, T, "account-source")
    assert snapshot.positions[0].instrument == KR
    assert replace(snapshot, positions=(position, replace(position, instrument=US)))
    for changes in (
        {"balances": (balance, balance)},
        {"positions": (position, position)},
        {"available_at": T - timedelta(seconds=1)},
        {"source_id": ""},
        {"positions": [position]},
    ):
        with pytest.raises(ValueError):
            replace(snapshot, **changes)
    with pytest.raises(ValueError, match="negative_cash"):
        CashBalance(cash("-1"), cash())
    with pytest.raises(ValueError, match="balance_currency_mismatch"):
        CashBalance(cash(), cash(currency=Currency.USD))
    with pytest.raises(ValueError, match="position_reservation"):
        replace(position, sellable=Quantity(7))


def test_risk_defaults_and_denials() -> None:
    default = RiskDecision("o1")
    assert not default.allowed and default.reasons == (DenialReason.ELIGIBILITY_UNKNOWN,)
    with pytest.raises(ValueError, match="risk_denied"):
        default.validate_for(intent())
    for reason in DenialReason:
        assert not RiskDecision("o1", reasons=(reason,)).allowed
    with pytest.raises(ValueError, match="risk_evidence_required"):
        RiskDecision("o1", allowed=True, reasons=())
    with pytest.raises(ValueError, match="risk_reasons_mismatch"):
        RiskDecision("o1", allowed=True)
    with pytest.raises(ValueError, match="live_execution_disabled"):
        RiskDecision("o1", ExecutionMode.LIVE, True, (), "evidence")
    with pytest.raises(ValueError, match="live_denial_required"):
        RiskDecision("o1", ExecutionMode.LIVE)
    assert not RiskDecision("o1", ExecutionMode.LIVE, reasons=(DenialReason.LIVE_DISABLED,)).allowed


def test_paper_risk_order_binding() -> None:
    risk = RiskDecision(
        "o1", allowed=True, reasons=(), evidence_id="paper-policy-result", order=intent()
    )
    risk.validate_for(intent())
    with pytest.raises(ValueError, match="risk_order_mismatch"):
        risk.validate_for(replace(intent(), order_id="o2"))
    with pytest.raises(ValueError, match="risk_order_mismatch"):
        risk.validate_for(replace(intent(), side=OrderSide.SELL))
    with pytest.raises(ValueError, match="risk_order_binding_required"):
        RiskDecision("o1", allowed=True, reasons=(), evidence_id="paper-policy-result")


class FakePaper:
    @property
    def mode(self) -> Literal[ExecutionMode.PAPER]:
        return ExecutionMode.PAPER

    def account(self, account_id: str) -> AccountSnapshot:
        return AccountSnapshot(account_id, (), (), T, T, "synthetic")

    def order_status(self, account_id: str, order_id: str) -> OrderStatus:
        return OrderStatus.UNKNOWN

    def submit_paper(self, order: OrderIntent, risk: RiskDecision) -> OrderStatus:
        risk.validate_for(order)
        return OrderStatus.SUBMITTED  # Not a fill and no account mutation.


def test_broker_port_and_paper_live_separation() -> None:
    broker: PaperBroker = FakePaper()
    reader: BrokerReader = broker
    assert isinstance(reader, BrokerReader) and isinstance(broker, PaperBroker)
    assert reader.account("a").source_id == "synthetic"
    assert reader.order_status("a", "o") is OrderStatus.UNKNOWN
    assert not hasattr(BrokerReader, "submit_paper")
    assert not hasattr(PaperBroker, "submit_live")
    with pytest.raises(ValueError, match="risk_denied"):
        broker.submit_paper(intent(), RiskDecision("o1"))
    risk = RiskDecision(
        "o1", allowed=True, reasons=(), evidence_id="paper-policy-result", order=intent()
    )
    assert broker.submit_paper(intent(), risk) is OrderStatus.SUBMITTED


def test_legacy_research_contract_remains_authoritative() -> None:
    assert OrderSide is LegacyOrderSide
    original = VirtualOrder.from_dict(payload(VirtualOrder))
    assert VirtualOrder.from_json(original.to_json()).identifier == original.identifier
    assert original.to_dict()["executable"] is False
    assert original.to_dict()["broker_route"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"allowed": 1},
        {"mode": "paper"},
        {"reasons": []},
        {"reasons": ("unknown",)},
        {"reasons": (DenialReason.RISK_LIMIT, DenialReason.RISK_LIMIT)},
        {"evidence_id": ""},
        {"reasons": ()},
    ],
)
def test_malformed_risk_fails_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RiskDecision("o1", **changes)  # type: ignore[arg-type]


def test_domain_import_has_no_infrastructure_side_effects() -> None:
    import subprocess
    import sys

    program = """
import sys
import donghak_stock_vision.trading.domain
import donghak_stock_vision.trading.broker
for prefix in ('fastapi', 'httpx', 'sqlite3', 'sklearn',
               'donghak_stock_vision.storage.sqlite', 'donghak_stock_vision.storage.analysis',
               'donghak_stock_vision.storage.decision', 'donghak_stock_vision.models'):
    assert not any(m == prefix or m.startswith(prefix + '.') for m in sys.modules), prefix
"""
    subprocess.run([sys.executable, "-c", program], check=True, timeout=10)
