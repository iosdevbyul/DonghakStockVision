"""Synthetic KRW 10M PAPER opening reuses deterministic broker semantics."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from donghak_stock_vision.trading.domain import Currency
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_account import open_krw_10m_account
from tests.trading.test_paper import fixture


def test_initial_account_has_exact_cash_and_no_trades() -> None:
    original, _, frame = fixture()
    broker = open_krw_10m_account("paper-10m", frame.now, original.checkpoint().policy)
    assert isinstance(broker, DeterministicPaperBroker)
    account = broker.account("paper-10m")
    assert len(account.balances) == 1
    assert account.balances[0].available.currency is Currency.KRW
    assert account.balances[0].available.amount == Decimal("10000000")
    assert account.balances[0].reserved.amount == 0
    assert account.positions == ()
    assert broker.fills() == ()
    assert broker.revision == 0
    assert broker.checkpoint().initial_frame.trading_enabled is False


def test_account_genesis_is_deterministic_and_account_scoped() -> None:
    original, _, frame = fixture()
    policy = original.checkpoint().policy
    one = open_krw_10m_account("paper-10m", frame.now, policy)
    two = open_krw_10m_account("paper-10m", frame.now, policy)
    other = open_krw_10m_account("other-account", frame.now, policy)
    assert one.state_id == two.state_id
    assert one.state_id != other.state_id


def test_rejects_non_utc_opening() -> None:
    original, _, frame = fixture()
    policy = original.checkpoint().policy
    with pytest.raises(ValueError):
        open_krw_10m_account("paper-10m", frame.now.replace(tzinfo=None), policy)
    with pytest.raises(ValueError):
        open_krw_10m_account(
            "paper-10m", datetime(2026, 10, 31, tzinfo=timezone(timedelta(hours=9))), policy
        )
