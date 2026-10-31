"""Final Track B regression: synthetic account remains isolated, inert and replayable."""

import sqlite3
from decimal import Decimal
from typing import Any

import pytest
from tests.trading.test_paper import fixture

from donghak_stock_vision.trading.broker import ExecutionMode
from donghak_stock_vision.trading.domain import Currency
from donghak_stock_vision.trading.paper_account import open_krw_10m_account


def test_genesis_is_repeatable_and_isolated_across_accounts() -> None:
    original, _, frame = fixture()
    policy = original.checkpoint().policy
    first = open_krw_10m_account("paper-final-a", frame.now, policy)
    repeated = open_krw_10m_account("paper-final-a", frame.now, policy)
    second = open_krw_10m_account("paper-final-b", frame.now, policy)

    assert first.mode is ExecutionMode.PAPER
    assert first.simulated is True
    assert first.state_id == repeated.state_id
    assert first.state_id != second.state_id
    assert first.checkpoint() == repeated.checkpoint()
    assert first.revision == repeated.revision == second.revision == 0

    for broker, account_id in ((first, "paper-final-a"), (second, "paper-final-b")):
        account = broker.account(account_id)
        assert len(account.balances) == 1
        assert account.balances[0].available.currency is Currency.KRW
        assert account.balances[0].available.amount == Decimal("10000000")
        assert account.balances[0].reserved.amount == 0
        assert account.positions == ()
        assert broker.fills() == ()
        assert broker.checkpoint().initial_frame.trading_enabled is False


def test_genesis_and_readback_do_not_open_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, _, frame = fixture()
    policy = original.checkpoint().policy

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("synthetic PAPER account unexpectedly opened SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    broker = open_krw_10m_account("paper-offline", frame.now, policy)
    before = broker.state_id
    assert broker.account("paper-offline").positions == ()
    assert broker.fills() == ()
    assert broker.checkpoint().initial_frame.trading_enabled is False
    assert broker.state_id == before
    assert broker.revision == 0
