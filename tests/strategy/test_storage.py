import sqlite3
from pathlib import Path

import pytest

from donghak_stock_vision.storage.decision import DecisionStore, ensure_separate
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy
from donghak_stock_vision.strategy.engine import decide
from tests.strategy.helpers import inputs, policy


def test_immutable_replay_and_idempotency(tmp_path: Path) -> None:
    b, p = inputs(held=5), policy()
    original, settings = DecisionInput.from_dict(b), DecisionPolicy.from_dict(p)
    result = decide(original, settings)
    store = DecisionStore(tmp_path / "decision.db")
    try:
        first = store.save(original, settings, result)
        store.save(original, settings, result)
        assert store.connection.execute("SELECT count(*) FROM decision_bundles").fetchone()[0] == 1
        assert (
            store.connection.execute("SELECT count(*) FROM decision_executions").fetchone()[0] == 2
        )
        b["account"]["quantity"] = 0
        p["thresholds"]["buy"] = "0"
        replay = store.replay(result.identifier)
        assert replay == first
        assert replay["diagnostics"]["safety_assurance"] is False
        assert replay["diagnostics"]["exit_signal_status"] == "no_applicable_exit_signal"
        assert store.query() == []
        assert store.query("research", "000001") == [first]
        assert store.query("research", as_of="2019-01-01T00:00:00Z") == []
        for sql in ("DELETE FROM decision_bundles", "UPDATE decision_bundles SET ticker='000002'"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                store.connection.execute(sql)
        store.connection.execute("DROP TRIGGER decision_immutable_update")
        store.connection.execute("UPDATE decision_bundles SET policy_json='{}'")
        with pytest.raises(ValueError, match="checksum"):
            store.replay(result.identifier)
    finally:
        store.close()


def test_atomic_rollback(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decision.db")
    try:
        store.connection.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON decision_executions "
            "BEGIN SELECT RAISE(ABORT,'audit failed'); END"
        )
        b, p = DecisionInput.from_dict(inputs()), DecisionPolicy.from_dict(policy())
        with pytest.raises(sqlite3.IntegrityError, match="audit failed"):
            store.save(b, p, decide(b, p))
        assert store.connection.execute("SELECT count(*) FROM decision_bundles").fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_database_alias_protection(tmp_path: Path, alias: str) -> None:
    source = tmp_path / "market.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE bars (ticker TEXT)")
    before = source.read_bytes()
    target = tmp_path / "decision.db"
    if alias == "same":
        target = source
    elif alias == "symlink":
        target.symlink_to(source)
    else:
        target.hardlink_to(source)
    with pytest.raises(ValueError, match="separate"):
        ensure_separate(target, [source])
    with pytest.raises(ValueError, match="not_a_decision"):
        DecisionStore(target)
    assert source.read_bytes() == before
