"""Synthetic tests for the offline operational API contract smoke command."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from tests.api.test_operational_market_read import make_snapshot


def load_smoke() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "smoke-operational-api.py"
    spec = importlib.util.spec_from_file_location("operational_api_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_operational_api_smoke(tmp_path: Path) -> None:
    module = load_smoke()
    snapshot = tmp_path / "safe.snapshot.sqlite3"
    fingerprint = make_snapshot(snapshot)
    result = module.verify(snapshot, fingerprint)
    assert result["status"] == "passed"
    assert result["evidenceStatus"] == "unreviewed"
    assert result["tickerCount"] == 1
    assert result["observationCount"] == 1
    assert result["networkRequests"] == 0
    assert result["paperExecutionEligible"] is False


def test_smoke_rejects_wrong_fingerprint(tmp_path: Path) -> None:
    module = load_smoke()
    snapshot = tmp_path / "safe.snapshot.sqlite3"
    make_snapshot(snapshot)
    with pytest.raises(ValueError, match="fingerprint"):
        module.verify(snapshot, "0" * 64)


def test_smoke_rejects_unpinned_operational_database(tmp_path: Path) -> None:
    module = load_smoke()
    database = tmp_path / "operational.sqlite3"
    fingerprint = make_snapshot(database)
    with pytest.raises(ValueError, match="offline"):
        module.verify(database, fingerprint)
