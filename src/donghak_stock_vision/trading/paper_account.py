"""Explicit synthetic KRW 10M PAPER account genesis; no orders or market data."""

from datetime import datetime
from decimal import Decimal

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import text, utc
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    CashBalance,
    Currency,
    Money,
)
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_types import FixturePolicy, PaperFrame

INITIAL_KRW = Decimal("10000000")


def open_krw_10m_account(
    account_id: str, at: datetime, policy: FixturePolicy
) -> DeterministicPaperBroker:
    """Create an empty synthetic broker funded with exactly 10,000,000 KRW."""
    identity = text(account_id)
    opening_at = utc(at)
    if opening_at != at or at.utcoffset() is None:
        raise ValueError("timezone_aware_utc_opening_required")
    if not any(mechanics.currency is Currency.KRW for mechanics in policy.currencies):
        raise ValueError("krw_mechanics_required")
    genesis = FrozenJSON.freeze(
        {
            "version": "paper_krw_10m_genesis_v1",
            "mode": "PAPER_SYNTHETIC",
            "account_id": identity,
            "initial_cash": "10000000",
            "opening_at": opening_at.isoformat(),
        }
    )
    cash = Money(INITIAL_KRW, Currency.KRW)
    zero = Money(Decimal(0), Currency.KRW)
    account = AccountSnapshot(
        identity, (CashBalance(cash, zero),), (), opening_at, opening_at, genesis.identifier
    )
    frame = PaperFrame(genesis.identifier, opening_at, (), (), (), (), False)
    return DeterministicPaperBroker(account, policy, frame)
