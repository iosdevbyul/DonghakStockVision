"""Separate SQLite: atomic immutable bundles and independent execution audit."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy, DecisionResult
from donghak_stock_vision.strategy.engine import decide


def ensure_separate(path: Path, protected: list[Path]) -> None:
    for other in protected:
        if path.resolve() == other.resolve() or (
            path.exists() and other.exists() and path.samefile(other)
        ):
            raise ValueError("decision_database_must_be_separate")


class DecisionStore:
    def __init__(self, path: Path, protected: list[Path] | None = None) -> None:
        ensure_separate(path, protected or [])
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        tables = {
            r[0]
            for r in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables and tables != {"decision_bundles", "decision_executions"}:
            self.connection.close()
            raise ValueError("not_a_decision_database")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS decision_bundles (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, ticker TEXT NOT NULL,
                as_of TEXT NOT NULL, input_json TEXT NOT NULL, policy_json TEXT NOT NULL,
                result_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_executions (
                id INTEGER PRIMARY KEY, decision_id TEXT NOT NULL, computed_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS decision_immutable_update
            BEFORE UPDATE ON decision_bundles BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
            CREATE TRIGGER IF NOT EXISTS decision_immutable_delete
            BEFORE DELETE ON decision_bundles BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
        """)

    def close(self) -> None:
        self.connection.close()

    def save(
        self, inputs: DecisionInput, policy: DecisionPolicy, result: DecisionResult
    ) -> dict[str, Any]:
        if decide(inputs, policy) != result:
            raise ValueError("decision_result_mismatch")
        value = result.to_dict()
        computed_at = datetime.now(UTC).isoformat()
        with self.connection:
            self.connection.execute(
                "INSERT INTO decision_bundles VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (
                    result.identifier,
                    value["decision_scope"],
                    value["ticker"],
                    value["decision_as_of"],
                    inputs.payload_json,
                    policy.payload_json,
                    result.payload_json,
                    computed_at,
                ),
            )
            # The INSERT holds SQLite's write lock through this check and audit write.
            # A duplicate ID must never silently accept different immutable contents.
            existing = self.connection.execute(
                "SELECT input_json,policy_json,result_json,scope,ticker,as_of "
                "FROM decision_bundles WHERE id=?",
                (result.identifier,),
            ).fetchone()
            expected = (
                inputs.payload_json,
                policy.payload_json,
                result.payload_json,
                value["decision_scope"],
                value["ticker"],
                value["decision_as_of"],
            )
            if existing != expected:
                raise ValueError("decision_bundle_conflict")
            self.connection.execute(
                "INSERT INTO decision_executions(decision_id,computed_at) VALUES(?,?)",
                (result.identifier, computed_at),
            )
        return {**value, "decision_id": result.identifier, "computed_at": computed_at}

    def _read(self, key: str) -> tuple[DecisionInput, DecisionPolicy, DecisionResult, str]:
        row = self.connection.execute(
            "SELECT input_json,policy_json,result_json,created_at,scope,ticker,as_of "
            "FROM decision_bundles WHERE id=?",
            (key,),
        ).fetchone()
        if row is None:
            raise ValueError("missing_decision")
        inputs, policy, result = (
            DecisionInput(row[0]),
            DecisionPolicy(row[1]),
            DecisionResult(row[2]),
        )
        value = result.to_dict()
        if (
            result.identifier != key
            or inputs.identifier != value["input_bundle_id"]
            or policy.identifier != value["policy_id"]
            or (value["decision_scope"], value["ticker"], value["decision_as_of"]) != row[4:]
        ):
            raise ValueError("decision_checksum_mismatch")
        return inputs, policy, result, row[3]

    def get(self, key: str) -> dict[str, Any]:
        _, _, result, created = self._read(key)
        return {**result.to_dict(), "decision_id": key, "computed_at": created}

    def replay(self, key: str) -> dict[str, Any]:
        inputs, policy, result, _ = self._read(key)
        if decide(inputs, policy) != result:
            raise ValueError("decision_reproduction_mismatch")
        return self.get(key)

    def query(
        self,
        scope: str = "operational",
        ticker: str | None = None,
        as_of: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if scope not in {"research", "operational"} or type(limit) is not int or limit < 1:
            raise ValueError("invalid_decision_query")
        cutoff = timestamp(as_of) if as_of else None
        rows = self.connection.execute(
            "SELECT id,as_of FROM decision_bundles WHERE scope=? AND (? IS NULL OR ticker=?)",
            (scope, ticker, ticker),
        ).fetchall()
        # Compare UTC instants without rewriting legacy JSON, indexed text, or IDs.
        # Apply LIMIT after chronological filtering/sorting, preserving id ASC ties.
        instants = [(key, timestamp(instant)) for key, instant in rows]
        eligible = [
            (key, instant) for key, instant in instants if cutoff is None or instant <= cutoff
        ]
        eligible.sort(key=lambda row: row[0])
        eligible.sort(key=lambda row: row[1], reverse=True)
        return [self.get(key) for key, _ in eligible[:limit]]
