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


def test_duplicate_preserves_bundle_and_first_creation(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decision.db")
    try:
        b, p = DecisionInput.from_dict(inputs()), DecisionPolicy.from_dict(policy())
        result = decide(b, p)
        first = store.save(b, p, result)
        before = store.connection.execute("SELECT * FROM decision_bundles").fetchall()
        store.save(b, p, result)
        assert store.connection.execute("SELECT * FROM decision_bundles").fetchall() == before
        assert store.get(result.identifier) == first
        assert store.replay(result.identifier) == first
        assert (
            store.connection.execute("SELECT count(*) FROM decision_executions").fetchone()[0] == 2
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "column,replacement",
    [
        ("input_json", "{}"),
        ("policy_json", "{}"),
        ("result_json", "{}"),
        ("scope", "operational"),
        ("ticker", "000002"),
        ("as_of", "2020-06-01T16:00:00+09:00"),
    ],
)
def test_duplicate_conflict_rejects_without_audit(
    tmp_path: Path, column: str, replacement: str
) -> None:
    store = DecisionStore(tmp_path / "decision.db")
    try:
        b, p = DecisionInput.from_dict(inputs()), DecisionPolicy.from_dict(policy())
        result = decide(b, p)
        store.save(b, p, result)
        # Inject corruption as a file administrator bypassing immutable triggers.
        # Even an equivalent instant is not an exact match for the stored bundle.
        store.connection.execute("DROP TRIGGER decision_immutable_update")
        with store.connection:
            store.connection.execute(f"UPDATE decision_bundles SET {column}=?", (replacement,))
        before = store.connection.execute("SELECT * FROM decision_bundles").fetchall()
        with pytest.raises(ValueError, match="decision_bundle_conflict"):
            store.save(b, p, result)
        assert not store.connection.in_transaction
        assert store.connection.execute("SELECT * FROM decision_bundles").fetchall() == before
        assert (
            store.connection.execute("SELECT count(*) FROM decision_executions").fetchone()[0] == 1
        )
    finally:
        store.close()


def test_duplicate_audit_failure_rolls_back(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decision.db")
    try:
        b, p = DecisionInput.from_dict(inputs()), DecisionPolicy.from_dict(policy())
        result = decide(b, p)
        original = store.save(b, p, result)
        store.connection.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON decision_executions "
            "BEGIN SELECT RAISE(ABORT,'audit failed'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="audit failed"):
            store.save(b, p, result)
        assert store.replay(result.identifier) == original
        assert (
            store.connection.execute("SELECT count(*) FROM decision_executions").fetchone()[0] == 1
        )
    finally:
        store.close()


def seed_offset_bundles(store: DecisionStore) -> dict[str, str]:
    """Legacy representation fixtures; leave content hashes and indexed metadata consistent."""
    from donghak_stock_vision.strategy.contracts import DecisionResult

    b, p = DecisionInput.from_dict(inputs()), DecisionPolicy.from_dict(policy())
    template = decide(b, p).to_dict()
    times = {
        "before": "2020-06-01T15:59:59.999999+09:00",
        "same_seoul": "2020-06-01T16:00:00+09:00",
        "same_z": "2020-06-01T07:00:00Z",
        "same_negative": "2020-06-01T02:00:00-05:00",
        "after": "2020-06-01T02:00:00.000001-05:00",
        "next_day": "2020-06-01T23:30:00-05:00",
    }
    keys = {}
    with store.connection:
        for name, instant in times.items():
            result = DecisionResult.from_dict({**template, "decision_as_of": instant})
            keys[name] = result.identifier
            store.connection.execute(
                "INSERT INTO decision_bundles VALUES(?,?,?,?,?,?,?,?)",
                (
                    result.identifier,
                    "research",
                    "000001",
                    instant,
                    b.payload_json,
                    p.payload_json,
                    result.payload_json,
                    "2020-06-02T00:00:00Z",
                ),
            )
    return keys


@pytest.mark.parametrize(
    "cutoff",
    ["2020-06-01T16:00:00+09:00", "2020-06-01T07:00:00Z", "2020-06-01T02:00:00-05:00"],
)
def test_offset_query_boundary_and_stable_order(tmp_path: Path, cutoff: str) -> None:
    store = DecisionStore(tmp_path / "decision.db")
    try:
        keys = seed_offset_bundles(store)
        before = store.connection.execute("SELECT * FROM decision_bundles ORDER BY id").fetchall()
        ties = sorted(keys[name] for name in ("same_seoul", "same_z", "same_negative"))
        expected = [*ties, keys["before"]]
        rows = store.query("research", "000001", cutoff)
        assert [r["decision_id"] for r in rows] == expected
        assert [r["decision_id"] for r in store.query("research", as_of=cutoff, limit=2)] == ties[
            :2
        ]
        assert [r["decision_id"] for r in store.query("research")] == [
            keys["next_day"],
            keys["after"],
            *expected,
        ]
        assert store.query("operational", as_of=cutoff) == []
        assert store.query("research", ticker="000002", as_of=cutoff) == []
        assert store.query("research", as_of="2020-06-01T06:59:59.999998Z") == []
        assert len(store.query("research", as_of="2020-06-01T07:00:00.000001Z")) == 5
        assert (
            store.connection.execute("SELECT * FROM decision_bundles ORDER BY id").fetchall()
            == before
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "instant", ["2020-06-01T16:00:00+09:00", "2020-06-01T07:00:00Z", "2020-06-01T02:00:00-05:00"]
)
def test_existing_saved_identifiers_and_replay_unchanged(tmp_path: Path, instant: str) -> None:
    path = tmp_path / "decision.db"
    store = DecisionStore(path)
    b = inputs()
    b["request"]["decision_as_of"] = instant
    original, p = DecisionInput.from_dict(b), DecisionPolicy.from_dict(policy())
    result = decide(original, p)
    saved = store.save(original, p, result)
    assert saved["decision_as_of"] == "2020-06-01T07:00:00+00:00"
    store.close()
    reopened = DecisionStore(path)
    try:
        assert reopened.query("research", as_of=instant) == [saved]
        assert reopened.replay(result.identifier) == saved
        assert reopened.get(result.identifier)["input_bundle_id"] == original.identifier
    finally:
        reopened.close()
