"""Pinned, read-only projection of one autonomous study for the local research UI."""

import hashlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore

_HEX = re.compile(r"^[a-f0-9]{64}$")


class StudyReadError(Exception):
    """An unavailable or inconsistent approved research snapshot."""


def _pin(value: str) -> str:
    if not _HEX.fullmatch(value):
        raise StudyReadError("invalid_study_id")
    return value


def _file_sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


class PinnedStudyReader:
    """Read a quiescent, operator-selected SQLite copy; never expose raw artifacts.

    This is not a discovery endpoint over private roots. The deployment operator
    supplies exactly one copied DB, its independently approved SHA, and plan ID.
    """

    def __init__(self, snapshot: Path, expected_sha256: str, study_id: str) -> None:
        self.study_id = _pin(study_id)
        self.expected_sha256 = _pin(expected_sha256)
        path = snapshot.resolve(strict=True)
        if (
            not snapshot.is_absolute()
            or snapshot.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 512 * 1024 * 1024
            or path.stat().st_size == 0
            or path.suffix != ".sqlite3"
            or path.stat().st_mode & 0o077
            or _file_sha256(path) != self.expected_sha256
        ):
            raise StudyReadError("unapproved_research_snapshot")
        self.path = path
        # Validate eagerly; startup must fail closed, not leak an invalid archive.
        self._read()

    def _read(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        try:
            if (_file_sha256(self.path) != self.expected_sha256
                    or self.path.is_symlink()
                    or self.path.stat().st_mode & 0o077):
                raise StudyReadError("study_snapshot_changed")
            with closing(ReadOnlyAnalysisStore(self.path)) as store:
                store.connection.execute("PRAGMA query_only=ON")
                plan = store.get("autonomous_plan", self.study_id)
                if digest(plan) != self.study_id or plan.get("version") not in {
                    "autonomous_plan_v5", "autonomous_plan_v6"
                }:
                    raise StudyReadError("unsupported_study")
                if any(plan.get(k) is not False for k in (
                    "pit_qualified", "performance_approved", "execution_authorized"
                )):
                    raise StudyReadError("unsafe_study_approval")
                configurations = plan.get("configurations")
                if not isinstance(configurations, list) or len(configurations) > 1000:
                    raise StudyReadError("invalid_study_catalog")
                identities = {}
                for item in configurations:
                    key = _pin(item["experiment_id"])
                    if key in identities:
                        raise StudyReadError("duplicate_experiment")
                    identities[key] = item["config"]
                terminals = {}
                for _, item in store.all("autonomous_terminal"):
                    key = _pin(item["experiment_id"])
                    if (
                        key not in identities
                        or key in terminals
                        or item.get("config") != identities[key]
                        or item.get("state") not in ("completed", "failed")
                    ):
                        raise StudyReadError("invalid_terminal_link")
                    terminals[key] = item
                attempts = {}
                for _, item in store.all("autonomous_attempt"):
                    key = _pin(item["experiment_id"])
                    if key not in identities or item.get("plan_id") != self.study_id:
                        raise StudyReadError("invalid_attempt_link")
                    # Only verified parent keys are exposed; conflicts are unavailable.
                    parent = item.get("parent_experiment")
                    if parent is not None and parent not in identities:
                        raise StudyReadError("invalid_parent_link")
                    if key in attempts and attempts[key] != parent:
                        attempts[key] = None
                    else:
                        attempts[key] = parent
                return plan, terminals, attempts
        except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as exc:
            raise StudyReadError("research_snapshot_invalid") from exc

    def collection(self) -> dict[str, Any]:
        plan, terminals, _ = self._read()
        return {
            "schemaVersion": "research_study_read_v1",
            "items": [{
                "studyId": self.study_id,
                "planVersion": plan["version"],
                "selectionPolicy": plan.get("selection_policy"),
                "experimentBudget": plan.get("experiment_budget"),
                "completed": sum(t["state"] == "completed" for t in terminals.values()),
                "failed": sum(t["state"] == "failed" for t in terminals.values()),
                "recordedExperiments": len(terminals),
                "reportStatus": "UNAVAILABLE",
                "policyComparisonStatus": "UNAVAILABLE",
                "independentValidationStatus": "NOT_ESTABLISHED",
                "pitQualified": False,
                "performanceApproved": False,
                "executionAuthorized": False,
            }],
        }

    def experiments(self, study_id: str, offset: int, limit: int) -> dict[str, Any]:
        if _pin(study_id) != self.study_id:
            raise StudyReadError("study_not_found")
        plan, terminals, attempts = self._read()
        rows = []
        for item in plan["configurations"]:
            key = item["experiment_id"]
            terminal = terminals.get(key)
            config = item["config"]
            rows.append({
                "studyId": self.study_id,
                "experimentId": key,
                "status": terminal["state"].upper() if terminal else "NOT_RUN",
                "horizon": config["goal"]["horizon"],
                "stockPolicy": config.get("stock_policy"),
                "modelFamily": config["candidate"]["family"],
                "featureMask": config.get("mask"),
                "parentExperimentId": attempts.get(key),
                "meanAP": terminal.get("mean_ap") if terminal and terminal["state"] == "completed" else None,
                "stabilityAP": terminal.get("stability_ap") if terminal and terminal["state"] == "completed" else None,
                "largestTrainGap": terminal.get("largest_train_gap") if terminal and terminal["state"] == "completed" else None,
                "selectionPolicy": plan.get("selection_policy"),
                "reportId": None,
                "independentValidationStatus": "NOT_ESTABLISHED",
            })
        return {
            "schemaVersion": "research_experiments_read_v1",
            "studyId": self.study_id,
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "items": rows[offset:offset + limit],
        }

    def experiment(self, study_id: str, experiment_id: str) -> dict[str, Any] | None:
        _pin(experiment_id)
        data = self.experiments(study_id, 0, 1000)
        return next((row for row in data["items"] if row["experimentId"] == experiment_id), None)
