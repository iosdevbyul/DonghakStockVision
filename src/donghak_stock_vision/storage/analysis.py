"""Content-addressed immutable snapshots/models/results, separate from market data."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from donghak_stock_vision.data.learning import AnalysisError, canonical, digest


class AnalysisStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS analysis_artifacts (
                kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(kind,id)
            );
            CREATE TRIGGER IF NOT EXISTS immutable_artifact_update
            BEFORE UPDATE ON analysis_artifacts BEGIN
                SELECT RAISE(ABORT, 'immutable artifact'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_artifact_delete
            BEFORE DELETE ON analysis_artifacts BEGIN
                SELECT RAISE(ABORT, 'immutable artifact'); END;
            CREATE TABLE IF NOT EXISTS analysis_executions (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, artifact_id TEXT NOT NULL,
                executed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operational_registry (
                model_version TEXT NOT NULL, direction TEXT NOT NULL,
                registered_at TEXT NOT NULL, evidence TEXT NOT NULL,
                PRIMARY KEY(model_version,direction)
            );
        """)

    def close(self) -> None:
        self.connection.close()

    def put(self, kind: str, payload: dict[str, Any]) -> str:
        return self.put_many([(kind, payload)])[0]

    def put_many(self, records: list[tuple[str, dict[str, Any]]]) -> list[str]:
        ids = []
        with self.connection:
            for kind, payload in records:
                artifact_id = digest(payload)
                self.connection.execute(
                    "INSERT OR IGNORE INTO analysis_artifacts VALUES(?,?,?,?)",
                    (kind, artifact_id, canonical(payload), datetime.now(UTC).isoformat()),
                )
                self.connection.execute(
                    "INSERT INTO analysis_executions(kind,artifact_id,executed_at) VALUES(?,?,?)",
                    (kind, artifact_id, datetime.now(UTC).isoformat()),
                )
                ids.append(artifact_id)
        return ids

    def get(self, kind: str, artifact_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload FROM analysis_artifacts WHERE kind=? AND id=?",
            (kind, artifact_id),
        ).fetchone()
        if row is None:
            raise AnalysisError("missing_model" if kind == "model" else f"missing_{kind}")
        try:
            payload = json.loads(row[0])
            if not isinstance(payload, dict) or digest(payload) != artifact_id:
                raise ValueError("checksum")
        except (ValueError, TypeError) as error:
            raise AnalysisError(f"invalid_{kind}") from error
        return cast(dict[str, Any], payload)

    def created_at(self, kind: str, artifact_id: str) -> datetime:
        self.get(kind, artifact_id)
        row = self.connection.execute(
            "SELECT created_at FROM analysis_artifacts WHERE kind=? AND id=?",
            (kind, artifact_id),
        ).fetchone()
        return datetime.fromisoformat(row[0])

    def all(self, kind: str) -> list[tuple[str, dict[str, Any]]]:
        ids = self.connection.execute(
            "SELECT id FROM analysis_artifacts WHERE kind=? ORDER BY id",
            (kind,),
        ).fetchall()
        return [(row[0], self.get(kind, row[0])) for row in ids]
