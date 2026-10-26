"""Original synthetic capture/journal -> original risk/broker -> atomic PAPER log."""

import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_signal import CALCULATED, make

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.paper_trade_demo_cli import action, configuration
from donghak_stock_vision.storage import forward_paper_journal as log
from donghak_stock_vision.storage.forward_paper import initial, process
from donghak_stock_vision.storage.forward_signal_journal import GENESIS as SIGNAL_GENESIS
from donghak_stock_vision.storage.forward_signal_journal import append_signal


def changed(owner: FrozenJSON, **values: Any) -> FrozenJSON:
    return FrozenJSON.freeze(owner.to_dict() | values)


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON]:
    root = tmp_path / "capture"
    captured = make(root, (100, 80, 120, 80)).to_dict()
    buy = FrozenJSON.freeze(captured | {"fast": 1, "slow": 2, "sources": captured["sources"][:3]})
    sell = FrozenJSON.freeze(
        captured
        | {"fast": 1, "slow": 2, "calculated_at": (CALCULATED + timedelta(seconds=3)).isoformat()}
    )
    journal = tmp_path / "signals.sqlite3"
    a = append_signal(journal, root, buy, buy.identifier, SIGNAL_GENESIS, CALCULATED)
    b = append_signal(
        journal,
        root,
        sell,
        sell.identifier,
        a.to_dict()["head_pin"],
        CALCULATED + timedelta(seconds=3),
    )
    pin = b.to_dict()["head_pin"]
    jobs = [
        action(
            root,
            r,
            journal,
            pin,
            e.to_dict()["entry"]["entry"]["decision"]["signal_id"],
            CALCULATED + timedelta(seconds=step),
        )
        for r, e, step in ((buy, a, 0), (sell, b, 3))
    ]
    return tmp_path / "paper.sqlite3", configuration(CALCULATED), jobs[0], jobs[1]


def test_buy_sell_exact_broker_settlement(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, sell = inputs
    first = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()
    r = first["result"]
    assert r["signal"] == "BUY" and r["risk"]["allowed"] and r["outcome"] == "filled"
    assert r["execution"]["fill"]["order"]["source_id"] == buy.to_dict()["signal_id"]
    assert r["execution"]["fill"]["price"]["amount"] == "121.2"
    assert r["execution"]["fill"]["fees"]["amount"] == "0.25"
    assert r["after"]["account"]["balances"][0]["available"]["amount"] == "9757.35"
    assert r["after"]["account"]["positions"][0]["quantity"]["shares"] == 2
    assert r["after"]["average_entry_cost"] is None and r["after"]["realized_pnl"] is None
    second = log.execute(path, config, config.identifier, first["head_pin"], sell).to_dict()
    r = second["result"]
    assert r["signal"] == "SELL" and r["outcome"] == "filled"
    assert r["execution"]["fill"]["fees"]["amount"] == "0.16"
    assert r["execution"]["fill"]["taxes"]["amount"] == "0.32"
    assert r["after"]["account"]["balances"][0]["available"]["amount"] == "9915.27"
    assert r["after"]["account"]["positions"][0]["quantity"]["shares"] == 0
    verified = log.inspect(path, config, config.identifier, second["head_pin"]).to_dict()
    assert len(verified["entries"]) == 2 and verified["semantic_replay_verified"]
    assert verified["entries"][0]["entry"]["result"] == first["result"]


@pytest.mark.parametrize(
    "closes,outcome", [((100, 100, 100, 100), "HOLD"), ((100, 90), "INSUFFICIENT_DATA")]
)
def test_no_order_no_broker_mutation(tmp_path: Path, closes: tuple[int, ...], outcome: str) -> None:
    root, journal = tmp_path / "capture", tmp_path / "signals.sqlite3"
    req = make(root, closes)
    record = append_signal(journal, root, req, req.identifier, SIGNAL_GENESIS, CALCULATED).to_dict()
    job = action(
        root,
        req,
        journal,
        record["head_pin"],
        record["entry"]["entry"]["decision"]["signal_id"],
        CALCULATED,
    )
    broker = initial(configuration(CALCULATED), configuration(CALCULATED).identifier)
    before = broker.state_id
    with patch.object(type(broker), "submit", side_effect=AssertionError("no order allowed")):
        updated, result = process(broker, job)
    assert result.to_dict()["signal"] == outcome
    assert result.to_dict()["order"] is None
    assert updated.state_id == before and updated.fills() == ()
    config = configuration(CALCULATED)
    appended = log.execute(tmp_path / "paper.sqlite3", config, config.identifier, log.GENESIS, job)
    assert appended.to_dict()["result"] == result.to_dict()
    assert log.inspect(
        tmp_path / "paper.sqlite3", config, config.identifier, appended.to_dict()["head_pin"]
    ).to_dict()["semantic_replay_verified"]


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"initial_cash": "100"}, "insufficient_available_cash"),
        ({}, "insufficient_sellable_position"),
    ],
)
def test_insufficient_resources_leave_account_unchanged(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON], updates: dict[str, Any], code: str
) -> None:
    path, config, buy, sell = inputs
    config = changed(config, **updates)
    job = buy if updates else sell
    result = log.execute(path, config, config.identifier, log.GENESIS, job).to_dict()["result"]
    assert result["outcome"] == "risk_rejected" and result["order"] is None
    assert code in [r["code"] for r in result["risk"]["rejections"]]
    assert result["before"]["broker_state_id"] == result["after"]["broker_state_id"]
    assert result["before"]["account"] == result["after"]["account"]


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("max_order_notional", "100", "maximum_order_notional"),
        ("max_position_fraction", "0.001", "maximum_position_concentration"),
        ("max_exposure_fraction", "0.001", "maximum_portfolio_exposure"),
    ],
)
def test_explicit_risk_limits(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON], field: str, value: str, code: str
) -> None:
    path, config, buy, _ = inputs
    config = changed(config, risk=config.to_dict()["risk"] | {field: value})
    result = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()["result"]
    assert code in [r["code"] for r in result["risk"]["rejections"]]
    assert result["order"] is None


def test_duplicate_conflict_and_stale_head(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    first = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()
    before = path.read_bytes()
    again = log.execute(path, config, config.identifier, first["head_pin"], buy).to_dict()
    assert again["duplicate"] and again["result"] == first["result"] and path.read_bytes() == before
    with pytest.raises(ValueError, match="conflicting_signal_replay"):
        log.execute(path, config, config.identifier, first["head_pin"], changed(buy, quantity=1))
    with pytest.raises(ValueError, match="unknown_commit_reconcile_required"):
        log.execute(path, config, config.identifier, log.GENESIS, buy)
    assert path.read_bytes() == before


def test_dry_run_source_hashes_and_repeatability(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    source_files = [
        Path(buy.to_dict()["capture_root"]) / "pilot.sqlite3",
        Path(buy.to_dict()["signal_journal"]),
    ]
    before = [p.read_bytes() for p in source_files]
    with (
        patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
        patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("HTTP forbidden")),
    ):
        a = log.execute(path, config, config.identifier, log.GENESIS, buy, dry_run=True)
        b = log.execute(path, config, config.identifier, log.GENESIS, buy, dry_run=True)
    assert a == b and not path.exists()
    committed = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()
    assert a.to_dict()["result"] == committed["result"]
    assert before == [p.read_bytes() for p in source_files]
    saved = path.read_bytes()
    log.execute(path, config, config.identifier, committed["head_pin"], buy, dry_run=True)
    assert saved == path.read_bytes()


@pytest.mark.parametrize("after_commit", [False, True])
def test_interrupted_write_unknown_outcome_and_reconciliation(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON], after_commit: bool
) -> None:
    path, config, buy, _ = inputs
    expected = log.execute(path, config, config.identifier, log.GENESIS, buy, dry_run=True)

    def interrupt(connection: sqlite3.Connection) -> None:
        if after_commit:
            connection.commit()
        raise RuntimeError("interrupted")

    with patch.object(log, "_commit", side_effect=interrupt), pytest.raises(RuntimeError):
        log.execute(path, config, config.identifier, log.GENESIS, buy)
    if after_commit:
        with pytest.raises(ValueError, match="unknown_commit"):
            log.execute(path, config, config.identifier, log.GENESIS, buy)
        reconciled = log.reconcile(path, config, config.identifier, log.GENESIS, buy).to_dict()
        assert reconciled["committed"]
        candidate_head = reconciled["head_pin"]
        # Exact intended successor is verified against the independently retained old head.
        verified = log.inspect(path, config, config.identifier, candidate_head).to_dict()
        assert verified["entries"][0]["entry"]["result"] == expected.to_dict()["result"]
        assert log.execute(path, config, config.identifier, candidate_head, buy).to_dict()[
            "duplicate"
        ]
    else:
        assert log.inspect(path, config, config.identifier, log.GENESIS).to_dict()["entries"] == []
        assert (
            log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()["result"]
            == expected.to_dict()["result"]
        )


@pytest.mark.parametrize("change", ["raw", "journal", "manifest"])
def test_tampered_original_sources(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON], change: str
) -> None:
    path, config, buy, _ = inputs
    d = buy.to_dict()
    if change == "journal":
        Path(d["signal_journal"]).write_bytes(b"corrupt signal archive")
    elif change == "manifest":
        Path(d["capture_root"], "pilot.sqlite3").unlink()
    else:
        with closing(sqlite3.connect(Path(d["capture_root"]) / "pilot.sqlite3")) as connection:
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall():
                connection.execute(f'DROP TRIGGER "{name}"')
            connection.execute("UPDATE raw_pages SET body=?", (b"tampered",))
            connection.commit()
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        log.execute(path, config, config.identifier, log.GENESIS, buy)
    assert not path.exists()


@pytest.mark.parametrize(
    "updates",
    [
        {"quantity": 0},
        {"quantity": True},
        {"quantity": 10001},
        {"mode": "LIVE"},
        {"at": "2025-07-09T00:00:00+00:00"},
        {"at": "2025-07-10T00:00:02"},
        {"signal_id": "0" * 64},
        {"version": "historical_sma_analysis_v1"},
    ],
)
def test_invalid_action_fail_closed(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON], updates: dict[str, Any]
) -> None:
    path, config, buy, _ = inputs
    with pytest.raises(ValueError):
        log.execute(path, config, config.identifier, log.GENESIS, changed(buy, **updates))
    assert not path.exists()


def test_protected_partition_before_source_io(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    d = buy.to_dict()
    d["request"]["sources"][0]["plan"]["partition"] = "final_test"
    d["request_pin"] = FrozenJSON.freeze(d["request"]).identifier
    with (
        patch(
            "donghak_stock_vision.storage.forward_paper.history",
            side_effect=AssertionError("source loaded"),
        ),
        pytest.raises(ValueError),
    ):
        log.execute(path, config, config.identifier, log.GENESIS, FrozenJSON.freeze(d))
    assert not path.exists()


def test_provider_or_synthetic_masquerade_rejected_before_io(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    d = buy.to_dict()
    d["request"]["mode"] = "authorized_provider"
    d["request_pin"] = FrozenJSON.freeze(d["request"]).identifier
    with (
        patch(
            "donghak_stock_vision.storage.forward_paper.history",
            side_effect=AssertionError("source loaded"),
        ),
        pytest.raises(ValueError),
    ):
        log.execute(path, config, config.identifier, log.GENESIS, FrozenJSON.freeze(d))


def test_rehashed_forgery_and_immutability(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    first = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()
    with closing(sqlite3.connect(path)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM paper_actions")
        connection.execute("DROP TRIGGER paper_no_update")
        document = json.loads(
            connection.execute("SELECT document FROM paper_actions").fetchone()[0]
        )
        document["result"]["after"]["account"]["balances"][0]["available"]["amount"] = "1000000"
        forged = FrozenJSON.freeze(document)
        connection.execute(
            "UPDATE paper_actions SET document=?,pin=?", (forged.payload_json, forged.identifier)
        )
        connection.execute(
            "CREATE TRIGGER paper_no_update BEFORE UPDATE ON paper_actions "
            "BEGIN SELECT RAISE(ABORT,'immutable'); END"
        )
        connection.commit()
    with pytest.raises(ValueError, match="semantic_replay"):
        log.inspect(path, config, config.identifier, forged.identifier)
    assert forged.identifier != first["head_pin"]


@pytest.mark.parametrize(
    "enabled,opened,outcome", [(False, True, "risk_rejected"), (True, False, "pending")]
)
def test_disabled_risk_and_closed_market(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
    enabled: bool,
    opened: bool,
    outcome: str,
) -> None:
    path, config, buy, _ = inputs
    result = log.execute(
        path,
        config,
        config.identifier,
        log.GENESIS,
        changed(buy, trading_enabled=enabled, session_open=opened),
    ).to_dict()["result"]
    assert result["outcome"] == outcome
    assert result["execution"] is None or result["execution"]["fill"] is None
    if not enabled:
        assert result["order"] is None
    else:
        assert result["after"]["account"]["balances"][0]["reserved"]["amount"] != "0"


def test_config_pin_and_policy_change(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    with pytest.raises(ValueError, match="configuration_pin"):
        log.execute(path, config, "0" * 64, log.GENESIS, buy)
    result = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()
    new = changed(config, initial_cash="20000")
    with pytest.raises(ValueError, match="integrity"):
        log.inspect(path, new, new.identifier, result["head_pin"])


def test_cli_json_and_read_only_history(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON], capsys: pytest.CaptureFixture[str]
) -> None:
    path, config, buy, _ = inputs
    account, job = path.parent / "account.json", path.parent / "action.json"
    account.write_text(config.payload_json)
    job.write_text(buy.payload_json)
    args = [
        "--account",
        str(account),
        "--account-pin",
        config.identifier,
        "--journal",
        str(path),
        "--journal-pin",
        log.GENESIS,
    ]
    assert main(["paper-trade", "dry-run", *args, "--action-file", str(job)]) == 0
    a = json.loads(capsys.readouterr().out)
    assert a["version"] == "forward_paper_processing_v1" and not path.exists()
    assert main(["paper-trade", "execute", *args, "--action-file", str(job)]) == 0
    b = json.loads(capsys.readouterr().out)
    assert b["result"] == a["result"]
    args[-1] = b["head_pin"]
    before = path.read_bytes()
    for command in ("history", "portfolio", "verify"):
        assert main(["paper-trade", command, *args]) == 0
        assert json.loads(capsys.readouterr().out)["head_pin"] == b["head_pin"]
    assert path.read_bytes() == before


def test_bounded_capacity_and_output_rollback(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, sell = inputs
    with patch.object(log, "MAX_DOCUMENT", 1), pytest.raises(ValueError, match="entry_byte_budget"):
        log.execute(path, config, config.identifier, log.GENESIS, buy)
    assert log.inspect(path, config, config.identifier, log.GENESIS).to_dict()["entries"] == []
    first = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()
    before = path.read_bytes()
    with patch.object(log, "MAX_ACTIONS", 1), pytest.raises(ValueError, match="action_capacity"):
        log.execute(path, config, config.identifier, first["head_pin"], sell)
    assert path.read_bytes() == before


def test_interrupted_initialization_and_wrong_database(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    path.touch()
    with pytest.raises(ValueError, match="interrupted_initialization"):
        log.execute(path, config, config.identifier, log.GENESIS, buy)
    assert path.read_bytes() == b""
    path.write_bytes(b"not SQLite")
    with pytest.raises(sqlite3.DatabaseError):
        log.execute(path, config, config.identifier, log.GENESIS, buy)
    assert path.read_bytes() == b"not SQLite"


@pytest.mark.parametrize("at", ["2025-07-10T01:00:03+00:00", "2025-07-10T00:00:02+09:00"])
def test_stale_and_noncanonical_action_clock(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON], at: str
) -> None:
    path, config, buy, _ = inputs
    from donghak_stock_vision.backtest.validation import utc

    late = changed(buy, at=at, fill_at=(utc(at) + timedelta(seconds=1)).isoformat())
    with pytest.raises(ValueError):
        log.execute(path, config, config.identifier, log.GENESIS, late)
    assert not path.exists()


def test_subsequent_signal_cannot_reverse_clock(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, sell = inputs
    first = log.execute(
        path,
        config,
        config.identifier,
        log.GENESIS,
        changed(buy, fill_at=(CALCULATED + timedelta(seconds=4)).isoformat()),
    ).to_dict()
    with pytest.raises(ValueError, match="action_clock_regression"):
        log.execute(path, config, config.identifier, first["head_pin"], sell)


def test_rehashed_capture_database_does_not_authenticate_receipts(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    import hashlib

    path, config, buy, _ = inputs
    d = buy.to_dict()
    db = Path(d["capture_root"]) / "pilot.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall():
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute("DELETE FROM raw_capture_receipts")
        connection.commit()
    d["request"]["database_pin"] = hashlib.sha256(db.read_bytes()).hexdigest()
    d["request_pin"] = FrozenJSON.freeze(d["request"]).identifier
    with pytest.raises(ValueError):
        log.execute(path, config, config.identifier, log.GENESIS, FrozenJSON.freeze(d))
    assert not path.exists()


def test_ambient_decimal_context_cannot_change_execution(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    from decimal import localcontext

    path, config, buy, _ = inputs
    a = log.execute(path, config, config.identifier, log.GENESIS, buy, dry_run=True)
    with localcontext() as context:
        context.prec = 3
        b = log.execute(path, config, config.identifier, log.GENESIS, buy, dry_run=True)
    assert a == b


def test_explicit_daily_loss_risk_and_unavailable_performance(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, _ = inputs
    result = log.execute(
        path, config, config.identifier, log.GENESIS, changed(buy, daily_pnl="-1001")
    ).to_dict()["result"]
    assert result["outcome"] == "risk_rejected"
    assert "daily_loss_limit" in [r["code"] for r in result["risk"]["rejections"]]
    assert result["after"]["realized_pnl"] is None


def test_reconciliation_requires_exact_intended_successor(
    inputs: tuple[Path, FrozenJSON, FrozenJSON, FrozenJSON],
) -> None:
    path, config, buy, sell = inputs
    first = log.execute(path, config, config.identifier, log.GENESIS, buy).to_dict()
    with pytest.raises(ValueError, match="not_exact_intended_successor"):
        log.reconcile(path, config, config.identifier, log.GENESIS, changed(buy, quantity=1))
    second = log.execute(path, config, config.identifier, first["head_pin"], sell).to_dict()
    with pytest.raises(ValueError, match="not_exact_intended_successor"):
        log.reconcile(path, config, config.identifier, log.GENESIS, sell)
    assert (
        log.reconcile(path, config, config.identifier, first["head_pin"], sell).to_dict()[
            "head_pin"
        ]
        == second["head_pin"]
    )
