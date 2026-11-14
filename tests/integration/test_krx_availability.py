"""Original validators and retained HTTP fixtures; no live request or model promotion."""

import hashlib
import json
import socket
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.integration.test_krx_recurring import AT
from tests.integration.test_krx_scheduler import client, enabled
from tests.integration.test_krx_scheduler import config as config

from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.storage.krx_availability import _timing, assess_availability
from donghak_stock_vision.storage.krx_backlog import _records
from donghak_stock_vision.storage.krx_scheduler import (
    ATTEMPT,
    SchedulerConfig,
    _daily_plan,
    _policy,
    _state,
    run_once,
)
from donghak_stock_vision.storage.operational_market import DB
from donghak_stock_vision.storage.prospective_operational_readiness import CaptureSource


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


@pytest.fixture
def stored(config: SchedulerConfig) -> tuple[CaptureSource, str]:
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        r = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert calls == [1] and r["outcome"] == "succeeded"
    r = _records(config)[0]
    return CaptureSource(
        config.path("capture_root"),
        ForwardCapturePlan.from_dict(r["plan"]),
        r["plan_pin"],
        r["attempt"],
        r["capture_pin"],
    ), r["batch_pin"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_original_times_and_read_only_repeat(
    config: SchedulerConfig, stored: tuple[CaptureSource, str]
) -> None:
    source, batch = stored
    paths = [
        config.path("operational_root") / DB,
        source.root / "pilot.sqlite3",
        config.path("runner_root") / "state.json",
    ]
    before = [sha(p) for p in paths]
    r = assess_availability(config, source, AT + timedelta(seconds=1), batch_pin=batch).to_dict()
    repeat = assess_availability(config, source, AT + timedelta(seconds=1), batch_pin=batch)
    assert repeat.to_dict() == r and before == [sha(p) for p in paths]
    assert r["availability"] == "available_and_stored" and r["observation_count"] == 940
    assert r["request_started_at"] == r["response_received_at"] == AT.isoformat()
    assert r["sqlite_storage_completion_recorded_at"] == AT.isoformat()
    assert r["sqlite_commit_at"] is None and r["official_publication_time"] == "UNKNOWN"
    assert r["clock_source"] == "local_system_utc_unverified"
    assert not r["independent_storage_time_verified"]
    assert not r["provider_data_observed"] and r["origin"] == "authorized_provider_mock_fixture"
    assert r["successful_response_observed_at"] is not None
    assert r["source_pin"] and r["sqlite_batch_pin"] == batch
    assert not any(
        r[k]
        for k in (
            "pit_qualified",
            "performance_approved",
            "prediction_created",
            "execution_authorized",
        )
    )


def test_capture_before_storage_is_distinct(
    config: SchedulerConfig, stored: tuple[CaptureSource, str]
) -> None:
    source, _ = stored
    r = assess_availability(config, source, AT).to_dict()
    assert r["availability"] == "available_not_stored" and r["sqlite_storage_verified_at"] is None
    assert r["sqlite_batch_pin"] is None and r["execution_window_open"] is None


@pytest.mark.parametrize("kind", ["empty", "wrong_date", "403", "429", "timeout"])
def test_failed_or_empty_original_never_proves_publication(
    config: SchedulerConfig, kind: str
) -> None:
    enabled(config)
    calls: list[int] = []

    def reply(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        if kind == "timeout":
            raise httpx.ReadTimeout("fixture")
        if kind == "wrong_date":
            return httpx.Response(
                200, json={"OutBlock_1": [{"ISU_CD": "005930", "BAS_DD": "20250706"}]}
            )
        return httpx.Response(int(kind) if kind.isdigit() else 200, json={"OutBlock_1": []})

    with httpx.Client(transport=httpx.MockTransport(reply), trust_env=False) as http:
        result = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert result["outcome"] == "failed" and calls == [1]
    p = _daily_plan(_policy(config), (AT - timedelta(days=1)).date())
    pin = _state(config)["captures"][0]["capture_pin"]
    source = CaptureSource(config.path("capture_root"), p, p.document.identifier, ATTEMPT, pin)
    before = sha(source.root / "pilot.sqlite3")
    r = assess_availability(config, source, AT).to_dict()
    assert r["successful_response_observed_at"] is None and not r["provider_data_observed"]
    assert r["official_publication_at"] is None and r["sqlite_storage_verified_at"] is None
    assert sha(source.root / "pilot.sqlite3") == before and calls == [1]
    if kind == "empty":
        assert r["availability"] == "empty_response" and r["observation_count"] == 0
        assert r["schema_valid"] and r["http_status"] == 200
        with pytest.raises(ValueError, match="nonempty_source_required"):
            assess_availability(config, source, AT, batch_pin="0" * 64)
    if kind == "wrong_date":
        assert r["availability"] == "invalid_response"
    if kind == "timeout":
        assert r["response_received_at"] is None and r["capture_finished_at"] == AT.isoformat()
    with closing(sqlite3.connect(config.path("operational_root") / DB)) as db:
        assert db.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change", ["source_pin", "capture_pin", "batch_pin", "owner", "future_clock"]
)
def test_substitution_and_time_regression_rejected(
    config: SchedulerConfig, stored: tuple[CaptureSource, str], change: str
) -> None:
    source, batch = stored
    at = AT
    if change == "source_pin":
        source = replace(source, plan_pin="0" * 64)
    if change == "capture_pin":
        source = replace(source, capture_pin="0" * 64)
    if change == "batch_pin":
        batch = "0" * 64
    if change == "owner":
        source = replace(source, root=source.root.parent / "other")
    if change == "future_clock":
        at -= timedelta(seconds=1)
    with pytest.raises(ValueError):
        assess_availability(config, source, at, batch_pin=batch)


def test_modified_raw_bytes_rejected(
    config: SchedulerConfig, stored: tuple[CaptureSource, str]
) -> None:
    source, batch = stored
    with closing(sqlite3.connect(source.root / "pilot.sqlite3")) as db:
        db.execute("UPDATE raw_pages SET body=x'00'")
        db.commit()
    with pytest.raises(ValueError):
        assess_availability(config, source, AT, batch_pin=batch)


@pytest.mark.parametrize("offset,expected", [(-1, True), (0, False), (1, False)])
def test_midnight_deadline_is_not_relaxed(offset: int, expected: bool) -> None:
    deadline = AT
    at = deadline + timedelta(seconds=offset)
    r = _timing(at, at, at, deadline)
    assert all(value is expected for value in r.values())


def test_missing_commit_time_is_not_invented() -> None:
    r = _timing(AT, None, AT, AT + timedelta(days=1))
    assert r["response_before_deadline"] and r["storage_completion_before_deadline"] is None
    with pytest.raises(ValueError, match="clock_order"):
        _timing(AT, AT - timedelta(seconds=1), AT, None)


def test_cli_offline_and_flags(
    config: SchedulerConfig,
    stored: tuple[CaptureSource, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, batch = stored
    cfg, plan = tmp_path / "config.json", tmp_path / "plan.json"
    cfg.write_text(config.document.payload_json)
    cfg.chmod(0o600)
    plan.write_text(source.plan.document.payload_json)

    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("credential loader forbidden")

    monkeypatch.setattr("donghak_stock_vision.cli.load_dotenv", deny)
    args = [
        "krx-scheduler",
        "availability",
        "--config",
        str(cfg),
        "--config-pin",
        config.document.identifier,
        "--source-plan",
        str(plan),
        "--source-plan-pin",
        source.plan_pin,
        "--source-attempt",
        source.attempt,
        "--capture-pin",
        source.capture_pin,
        "--batch-pin",
        batch,
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["availability"] == "available_and_stored"
    for flag in (
        "--execute",
        "--apply",
        "--allow-network",
        "--scheduled",
        "--confirm-operator-authorization",
    ):
        assert main(args + [flag]) != 0
    assert main(args + ["--availability-request", str(tmp_path / "missing")]) != 0


def test_unsupported_research_request_fails_before_source_read(
    config: SchedulerConfig, stored: tuple[CaptureSource, str]
) -> None:
    with pytest.raises(ValueError):
        assess_availability(config, stored[0], AT, research_request={"version": "unsupported"})


def test_market_database_cannot_substitute_for_model(
    config: SchedulerConfig, stored: tuple[CaptureSource, str], tmp_path: Path
) -> None:
    database = config.path("operational_root") / DB
    before = sha(database)
    request = dict(
        version="research_shadow_request_v1",
        mode="research_forward_candidate",
        source_kind="local_provider_capture",
        target_trading_date="2025-07-08",
        tickers=["005930"],
        calendars=[],
        model=dict(
            database=str(database),
            pin=before,
            model_version="wrong-owner",
            source_pins_root=str(tmp_path / "absent"),
        ),
        operational=dict(root=str(config.path("operational_root")), pin=before, evidence=[]),
    )
    with pytest.raises((ValueError, sqlite3.Error)):
        assess_availability(config, stored[0], AT, research_request=request)
    assert sha(database) == before
