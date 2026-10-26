"""Task-specific append-only PAPER action log; transactional state is verified replay.

Uses the existing signal-journal transaction/trigger pattern, separate application ID.
No durable broker side effects occur before commit: the broker is disposable in memory.
"""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.storage.forward_paper import MAX_ACTIONS, initial, portfolio, process
from donghak_stock_vision.trading.paper import DeterministicPaperBroker

GENESIS = FrozenJSON.freeze({"version": "forward_paper_journal_v1", "entries": []}).identifier
MAX_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT = 384 * 1024
APPLICATION_ID = 1146312272


def _open(path: Path, create: bool = False) -> sqlite3.Connection:
    require(not path.is_symlink() and path.parent.is_dir(), "private_paper_journal_path_required")
    created = False
    if create:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            created = True
    require(path.is_file() and path.stat().st_size <= MAX_BYTES, "paper_journal_missing_or_budget")
    connection = sqlite3.connect(
        path.resolve().as_uri() + ("?mode=rw" if create else "?mode=ro"), uri=True, timeout=5
    )
    try:
        if created:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE paper_actions(sequence INTEGER PRIMARY KEY, "
                "signal_id TEXT UNIQUE NOT NULL, document TEXT NOT NULL, pin TEXT NOT NULL)"
            )
            for operation in ("UPDATE", "DELETE"):
                connection.execute(
                    f"CREATE TRIGGER paper_no_{operation.lower()} BEFORE {operation} "
                    "ON paper_actions "
                    "BEGIN SELECT RAISE(ABORT,'paper journal immutable'); END"
                )
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.commit()
        require(
            connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID,
            "not_paper_journal_or_interrupted_initialization",
        )
        require(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND name IN ('paper_no_update','paper_no_delete')"
            ).fetchone()[0]
            == 2,
            "paper_immutability_guards_missing",
        )
        if create:
            size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={MAX_BYTES // size}")
            connection.execute("PRAGMA synchronous=FULL")
        else:
            connection.execute("PRAGMA query_only=ON")
        return connection
    except BaseException:
        connection.close()
        raise


def _chronology(
    entries: list[dict[str, Any]], job: dict[str, Any], decision: dict[str, Any]
) -> None:
    if entries:
        previous = entries[-1]["entry"]
        require(utc(job["at"]) >= utc(previous["job"]["fill_at"]), "action_clock_regression")
        last = previous["result"]["latest_trading_date"]
        require(
            last is None
            or (
                decision["latest_trading_date"] is not None
                and decision["latest_trading_date"] >= last
            ),
            "source_context_regression",
        )
        require(
            utc(decision["calculated_at"]) >= utc(previous["result"]["calculated_at"]),
            "signal_calculation_regression",
        )


def _replay(
    connection: sqlite3.Connection, config: FrozenJSON, config_pin: str, head: str
) -> tuple[DeterministicPaperBroker, list[dict[str, Any]]]:
    sha(head)
    broker = initial(config, config_pin)
    size = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(length(document)),0),"
        "COALESCE(MAX(length(document)),0) FROM paper_actions"
    ).fetchone()
    require(
        size[0] <= MAX_ACTIONS and size[1] <= MAX_BYTES and size[2] <= MAX_DOCUMENT,
        "paper_history_budget",
    )
    previous = GENESIS
    entries: list[dict[str, Any]] = []
    for seq, signal_id, document, pin in connection.execute(
        "SELECT sequence,signal_id,document,pin FROM paper_actions ORDER BY sequence"
    ):
        owner = FrozenJSON(document)
        d = owner.to_dict()
        require(
            set(d) == {"version", "sequence", "previous_pin", "config_pin", "job", "result"},
            "paper_entry_fields",
        )
        require(
            owner.identifier == pin
            and d["version"] == "forward_paper_entry_v1"
            and seq == d["sequence"] == len(entries) + 1
            and d["previous_pin"] == previous
            and d["config_pin"] == config_pin
            and d["job"]["signal_id"] == signal_id,
            "paper_journal_integrity_mismatch",
        )
        _chronology(entries, d["job"], d["result"])
        broker, expected = process(broker, FrozenJSON.freeze(d["job"]))
        require(expected.to_dict() == d["result"], "paper_semantic_replay_mismatch")
        entries.append({"entry": d, "entry_pin": pin})
        previous = pin
    require(previous == head, "paper_head_pin_mismatch_unknown_commit_reconcile_required")
    return broker, entries


def inspect(path: Path, config: FrozenJSON, config_pin: str, head: str) -> FrozenJSON:
    with closing(_open(path)) as connection:
        connection.execute("BEGIN")
        broker, entries = _replay(connection, config, config_pin, head)
    return FrozenJSON.freeze(
        {
            "version": "forward_paper_history_v1",
            "mode": "PAPER",
            "entries": entries,
            "head_pin": head,
            "portfolio": entries[-1]["entry"]["result"]["after"] if entries else portfolio(broker),
            "semantic_replay_verified": True,
            "independent_custody_verified": False,
        }
    )


def _commit(connection: sqlite3.Connection) -> None:
    connection.commit()


def execute(
    path: Path,
    config: FrozenJSON,
    config_pin: str,
    head: str,
    job: FrozenJSON,
    *,
    dry_run: bool = False,
) -> FrozenJSON:
    # Validate configuration and protected source scope before creating any output file.
    from donghak_stock_vision.storage.forward_paper import source

    initial(config, config_pin)
    decision = source(job)
    if not path.exists():
        require(head == GENESIS, "paper_head_pin_mismatch_unknown_commit_reconcile_required")
    if dry_run and not path.exists():
        broker = initial(config, config_pin)
        _, result = process(broker, job)
        return FrozenJSON.freeze(
            {
                "version": "forward_paper_processing_v1",
                "dry_run": True,
                "duplicate": False,
                "result": result.to_dict(),
                "head_pin": head,
            }
        )
    with closing(_open(path, create=not dry_run)) as connection:
        try:
            connection.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
            broker, entries = _replay(connection, config, config_pin, head)
            for entry in entries:
                if entry["entry"]["job"]["signal_id"] == decision["signal_id"]:
                    require(entry["entry"]["job"] == job.to_dict(), "conflicting_signal_replay")
                    return FrozenJSON.freeze(
                        {
                            "version": "forward_paper_processing_v1",
                            "dry_run": dry_run,
                            "duplicate": True,
                            "result": entry["entry"]["result"],
                            "head_pin": head,
                        }
                    )
            require(len(entries) < MAX_ACTIONS, "paper_action_capacity")
            _chronology(entries, job.to_dict(), decision)
            _, result = process(broker, job)
            owner = FrozenJSON.freeze(
                {
                    "version": "forward_paper_entry_v1",
                    "sequence": len(entries) + 1,
                    "previous_pin": head,
                    "config_pin": config_pin,
                    "job": job.to_dict(),
                    "result": result.to_dict(),
                }
            )
            require(len(owner.payload_json.encode()) <= MAX_DOCUMENT, "paper_entry_byte_budget")
            if not dry_run:
                connection.execute(
                    "INSERT INTO paper_actions VALUES(?,?,?,?)",
                    (len(entries) + 1, decision["signal_id"], owner.payload_json, owner.identifier),
                )
                _commit(connection)
        except BaseException:
            connection.rollback()
            raise
    return FrozenJSON.freeze(
        {
            "version": "forward_paper_processing_v1",
            "dry_run": dry_run,
            "duplicate": False,
            "result": result.to_dict(),
            "head_pin": head if dry_run else owner.identifier,
        }
    )


def reconcile(
    path: Path, config: FrozenJSON, config_pin: str, previous_head: str, job: FrozenJSON
) -> FrozenJSON:
    """Only reconcile the exact intended successor of an independently retained head."""
    sha(previous_head)
    with closing(_open(path)) as connection:
        connection.execute("BEGIN")
        tail = connection.execute(
            "SELECT pin FROM paper_actions ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        candidate = tail[0] if tail else GENESIS
        _, entries = _replay(connection, config, config_pin, candidate)
        if candidate != previous_head:
            require(
                bool(entries)
                and entries[-1]["entry"]["previous_pin"] == previous_head
                and entries[-1]["entry"]["job"] == job.to_dict(),
                "reconciliation_not_exact_intended_successor",
            )
    return FrozenJSON.freeze(
        {
            "version": "forward_paper_reconciliation_v1",
            "previous_head_pin": previous_head,
            "head_pin": candidate,
            "committed": candidate != previous_head,
            "semantic_replay_verified": True,
            "journal_written": False,
        }
    )
