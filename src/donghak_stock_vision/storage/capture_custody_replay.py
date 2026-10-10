"""Audited operator replay of quarantined custody submissions."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from donghak_stock_vision.storage.capture_custody_outbox import SCHEMA as OUTBOX_SCHEMA

REPLAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_custody_replay_events (
    id INTEGER PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES capture_custody_outbox(raw_id),
    operator_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    previous_attempt_count INTEGER NOT NULL,
    previous_blocker TEXT,
    recorded_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS capture_custody_replay_no_update
BEFORE UPDATE ON capture_custody_replay_events
BEGIN SELECT RAISE(ABORT, 'custody replay audit immutable'); END;
CREATE TRIGGER IF NOT EXISTS capture_custody_replay_no_delete
BEFORE DELETE ON capture_custody_replay_events
BEGIN SELECT RAISE(ABORT, 'custody replay audit immutable'); END;
"""


@dataclass(frozen=True)
class ReplayDecision:
    raw_id: int
    previous_attempt_count: int
    replay_event_id: int


def requeue_after_operator_review(
    connection: sqlite3.Connection,
    raw_id: int,
    *,
    operator_id: str,
    reason: str,
) -> ReplayDecision:
    """Explicitly retry a quarantined item; never assert acceptance or authenticity.

    Caller must independently establish operator permissions. The audit is
    local and cannot prove who performed the action.
    """
    if raw_id <= 0 or not operator_id.strip() or not reason.strip():
        raise ValueError("raw ID, operator ID and review reason are required")
    connection.execute(OUTBOX_SCHEMA)
    connection.executescript(REPLAY_SCHEMA)
    with connection:
        row = connection.execute(
            "SELECT state,attempt_count,last_blocker FROM capture_custody_outbox WHERE raw_id=?",
            (raw_id,),
        ).fetchone()
        if row is None or row[0] != "manual_review":
            raise ValueError("only quarantined custody submissions can be replayed")
        cursor = connection.execute(
            "INSERT INTO capture_custody_replay_events "
            "(raw_id,operator_id,reason,previous_attempt_count,previous_blocker,recorded_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                raw_id,
                operator_id.strip(),
                reason.strip(),
                row[1],
                row[2],
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.execute(
            "UPDATE capture_custody_outbox SET state='pending', attempt_count=0, "
            "last_blocker=NULL, last_attempt_at=NULL, acknowledgement_sha256=NULL "
            "WHERE raw_id=? AND state='manual_review'",
            (raw_id,),
        )
    if cursor.lastrowid is None:
        raise RuntimeError("custody replay event was not recorded")
    return ReplayDecision(raw_id, row[1], cursor.lastrowid)
