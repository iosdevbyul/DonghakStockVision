"""Local signal journal using SQLite transactions and existing immutable-trigger pattern.

A separate explicit output file keeps original capture archives untouched. Hash chains
need an independently retained head pin to detect truncation; they are not custody.
"""

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.forward_signal import generate_signal

APPLICATION_ID = 1146312266
MAX_ENTRIES = 256
MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRY = 128 * 1024
GENESIS = FrozenJSON.freeze({"version": "forward_signal_journal_v1", "entries": []}).identifier
SCHEMA = (
    "CREATE TABLE signal_entries (sequence INTEGER PRIMARY KEY, signal_id TEXT NOT NULL UNIQUE, "
    "document TEXT NOT NULL, entry_pin TEXT NOT NULL UNIQUE)",
    "CREATE TRIGGER signal_entries_no_update BEFORE UPDATE ON signal_entries "
    "BEGIN SELECT RAISE(ABORT, 'signal journal immutable'); END",
    "CREATE TRIGGER signal_entries_no_delete BEFORE DELETE ON signal_entries "
    "BEGIN SELECT RAISE(ABORT, 'signal journal immutable'); END",
)


def _open(path: Path, *, create: bool = False) -> sqlite3.Connection:
    require(not path.is_symlink() and path.parent.is_dir(), "private_journal_path_required")
    created = False
    if create:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            created = True
    require(path.is_file() and path.stat().st_size <= MAX_BYTES, "journal_missing_or_size_limit")
    connection = sqlite3.connect(
        path.resolve().as_uri() + ("?mode=rw" if create else "?mode=ro"), uri=True, timeout=5
    )
    try:
        if created:
            # Schema and marker commit together; an interrupted empty file fails closed.
            connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.commit()
        require(
            connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID,
            "not_a_signal_journal_or_interrupted_initialization",
        )
        require(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND name IN ('signal_entries_no_update','signal_entries_no_delete')"
            ).fetchone()[0]
            == 2,
            "journal_immutability_guards_missing",
        )
        if create:
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={MAX_BYTES // page_size}")
            connection.execute("PRAGMA synchronous=FULL")
        else:
            connection.execute("PRAGMA query_only=ON")
        return connection
    except BaseException:
        connection.close()
        raise


def _entries(connection: sqlite3.Connection, expected_head: str) -> list[dict[str, Any]]:
    sha(expected_head)
    size = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(length(document)),0),"
        "COALESCE(MAX(length(document)),0) FROM signal_entries"
    ).fetchone()
    require(
        size[0] <= MAX_ENTRIES and size[1] <= MAX_BYTES and size[2] <= MAX_ENTRY,
        "journal_record_budget",
    )
    previous = GENESIS
    entries: list[dict[str, Any]] = []
    for seq, signal_id, document, entry_pin in connection.execute(
        "SELECT sequence,signal_id,document,entry_pin FROM signal_entries ORDER BY sequence"
    ):
        owner = FrozenJSON(document)
        d = owner.to_dict()
        require(
            set(d)
            == {"version", "sequence", "previous_pin", "inserted_at", "decision", "decision_pin"},
            "journal_entry_fields",
        )
        decision = FrozenJSON.freeze(d["decision"])
        require(
            d["version"] == "forward_signal_entry_v1"
            and seq == len(entries) + 1 == d["sequence"]
            and signal_id == d["decision"]["signal_id"]
            and d["previous_pin"] == previous
            and decision.identifier == d["decision_pin"]
            and owner.identifier == entry_pin,
            "journal_integrity_mismatch",
        )
        inserted = timestamp(d["inserted_at"])
        require(
            inserted.isoformat() == d["inserted_at"]
            and inserted >= timestamp(d["decision"]["calculated_at"]),
            "journal_timestamp_mismatch",
        )
        if entries:
            require(
                inserted >= timestamp(entries[-1]["entry"]["inserted_at"]),
                "journal_insertion_clock_regression",
            )
        entries.append({"entry": d, "entry_pin": entry_pin})
        previous = entry_pin
    require(previous == expected_head, "journal_head_pin_mismatch")
    return entries


def history(path: Path, expected_head: str, *, limit: int = 50) -> FrozenJSON:
    require(type(limit) is int and 1 <= limit <= MAX_ENTRIES, "journal_history_limit")
    with closing(_open(path)) as connection:
        connection.execute("BEGIN")
        entries = _entries(connection, expected_head)
    return FrozenJSON.freeze(
        {
            "version": "forward_signal_history_v1",
            "total": len(entries),
            "entries": entries[-limit:],
            "head_pin": expected_head,
            "assurance": "local_chain_integrity_only_not_semantic_replay_or_custody",
        }
    )


def _commit(connection: sqlite3.Connection) -> None:
    connection.commit()


def append_signal(
    path: Path,
    root: Path,
    request: FrozenJSON,
    request_pin: str,
    expected_head: str,
    inserted_at: datetime,
) -> FrozenJSON:
    decision = generate_signal(root, request, request_pin)  # Never append arbitrary summary JSON.
    require(len(decision.payload_json.encode()) <= MAX_ENTRY - 2048, "decision_output_budget")
    inserted_at = timestamp(inserted_at)
    require(
        inserted_at >= timestamp(decision.to_dict()["calculated_at"]),
        "insertion_before_calculation",
    )
    with closing(_open(path, create=True)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            entries = _entries(connection, expected_head)
            signal_id = decision.to_dict()["signal_id"]
            for entry in entries:
                if entry["entry"]["decision"]["signal_id"] == signal_id:
                    require(
                        entry["entry"]["decision_pin"] == decision.identifier,
                        "same_signal_id_different_content",
                    )
                    return FrozenJSON.freeze(
                        {
                            "version": "forward_signal_append_v1",
                            "duplicate": True,
                            "entry": entry,
                            "head_pin": expected_head,
                        }
                    )
            require(len(entries) < MAX_ENTRIES, "journal_capacity_exhausted")
            require(
                not entries or inserted_at >= timestamp(entries[-1]["entry"]["inserted_at"]),
                "journal_insertion_clock_regression",
            )
            owner = FrozenJSON.freeze(
                {
                    "version": "forward_signal_entry_v1",
                    "sequence": len(entries) + 1,
                    "previous_pin": expected_head,
                    "inserted_at": inserted_at.isoformat(),
                    "decision": decision.to_dict(),
                    "decision_pin": decision.identifier,
                }
            )
            require(len(owner.payload_json.encode()) <= MAX_ENTRY, "entry_output_budget")
            connection.execute(
                "INSERT INTO signal_entries VALUES(?,?,?,?)",
                (len(entries) + 1, signal_id, owner.payload_json, owner.identifier),
            )
            _commit(connection)
        except BaseException:
            connection.rollback()
            raise
    return FrozenJSON.freeze(
        {
            "version": "forward_signal_append_v1",
            "duplicate": False,
            "entry": {"entry": owner.to_dict(), "entry_pin": owner.identifier},
            "head_pin": owner.identifier,
        }
    )
