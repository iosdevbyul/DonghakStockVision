"""No real LaunchAgent install/enable or provider requests: private temp fixtures only."""

import hashlib
import json
import plistlib
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_krx_forward_authorization import KEY
from tests.integration.test_krx_recurring import AT, fixture
from tests.integration.test_market_collection import row
from tests.integration.test_operational_capture import snapshot

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage import krx_launchd
from donghak_stock_vision.storage.krx_scheduler import (
    ATTEMPT,
    SchedulerConfig,
    _plan,
    _save,
    _state,
    control,
    inspect,
    load_config,
    run_once,
    set_enabled,
)
from donghak_stock_vision.storage.krx_pilot import PilotEvidence, audit_stored_005930
from donghak_stock_vision.storage.operational_market import OperationalMarketStore, StorageLimits
from donghak_stock_vision.storage.forward_capture import readback, verify_capture


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SchedulerConfig:
    _, ref = fixture(tmp_path)
    root = tmp_path / "operational"
    OperationalMarketStore.initialize(root, StorageLimits(min_free_bytes=0))
    monkeypatch.setenv("KRX_API_KEY", KEY)
    monkeypatch.setattr(
        krx_launchd, "_agent_path", lambda: tmp_path / "agents" / (krx_launchd.LABEL + ".plist")
    )
    return SchedulerConfig(
        FrozenJSON.freeze(
            dict(
                version="krx_scheduler_config_v1",
                policy_root=ref.to_dict()["root"],
                policy_pin=ref.to_dict()["grant_pin"],
                capture_root=str(tmp_path / "capture"),
                operational_root=str(root),
                runner_root=str(tmp_path / "runner"),
                credentials_file=None,
                python_executable=sys.executable,
                working_directory=str(tmp_path),
                hour=19,
                minute=0,
                timezone="Asia/Seoul",
            )
        )
    )


def client(
    calls: list[int],
    *,
    status: int = 200,
    offline: bool = False,
    invalid: bool = False,
    callback: Any = None,
) -> httpx.Client:
    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        if offline:
            raise httpx.ConnectError("fixture offline")
        if callback:
            callback()
        return httpx.Response(
            status,
            json={
                "OutBlock_1": [
                    row(
                        f"{i:06d}",
                        day=AT.date() - timedelta(days=1),
                        **({"TDD_CLSPRC": "bad"} if invalid else {}),
                    )
                    for i in range(940)
                ]
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)


def enabled(config: SchedulerConfig) -> None:
    set_enabled(config, True, allow_network=True, operator_confirmed=True)


def test_disabled_default_and_read_only_preview(config: SchedulerConfig) -> None:
    root = config.path("runner_root")
    result = run_once(config, clock=lambda: AT).to_dict()
    assert not result["enabled"] and not root.exists()
    with patch("httpx.Client.send", side_effect=AssertionError("network")):
        assert (
            run_once(config, execute=True, clock=lambda: AT).to_dict()["reason"]
            == "scheduler_disabled"
        )
    assert not config.path("capture_root").exists()
    assert not control(config)["enabled"]


def test_mock_full_market_and_idempotent_restart(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        result = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
        assert result["outcome"] == "succeeded" and result["observation_count"] == 940
        assert result["origin"] == "authorized_provider_mock_fixture"
        assert not any(
            result[n]
            for n in (
                "pit_qualified",
                "forward_signal_eligible",
                "paper_execution_eligible",
                "execution_authorized",
            )
        )
        again = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert again["reason"] == "already_collected" and calls == [1]
    status = inspect(config, AT).to_dict()
    assert (
        status["latest_persisted_trading_date"] == "2025-07-07"
        and status["stored_observations"] == 940
    )
    assert status["last_successful_collection"]["day"] == "2025-07-07"
    assert status["local_request_budget"]["locally_used_requests"] == 1
    assert status["missing_runner_completion_dates"] == []
    with closing(OperationalMarketStore(config.path("operational_root"))) as store:
        assert store.validate().to_dict()["valid"]
    assert KEY not in json.dumps(status)
    assert KEY not in (config.path("runner_root") / "state.json").read_text()


@pytest.mark.parametrize(
    "at,expected",
    [
        (AT + timedelta(days=4), "today_not_permitted_collection_day"),
        (AT - timedelta(days=4), "no_eligible_completed_trading_date"),
        (AT + timedelta(days=3, hours=15), "today_not_permitted_collection_day"),
    ],
)
def test_weekends_and_missing_dates_no_calls(
    config: SchedulerConfig, at: Any, expected: str
) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        result = run_once(config, execute=True, client=http, clock=lambda: at).to_dict()
    assert result["reason"] == expected and not calls


@pytest.mark.parametrize("damage", ["key", "permission", "expired", "storage"])
def test_preflight_failures_no_network(
    config: SchedulerConfig, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    enabled(config)
    calls: list[int] = []
    at = AT
    if damage == "key":
        monkeypatch.delenv("KRX_API_KEY")
    if damage == "permission":
        (config.path("policy_root") / "recurring-policy.json").unlink()
    if damage == "expired":
        at = AT + timedelta(days=6)
    if damage == "storage":
        (config.path("operational_root") / "database" / "operational.sqlite3").unlink()
    with client(calls) as http:
        result = run_once(config, execute=True, client=http, clock=lambda: at).to_dict()
    assert (
        result["outcome"]
        in (("failed", "blocked", "skipped") if damage == "expired" else ("failed", "blocked"))
        and not calls
    )
    assert _state(config)["pending"] is None


@pytest.mark.parametrize(
    "status,offline,invalid",
    [(403, False, False), (429, False, False), (200, True, False), (200, False, True)],
)
def test_failed_capture_not_retried(
    config: SchedulerConfig, status: int, offline: bool, invalid: bool
) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls, status=status, offline=offline, invalid=invalid) as http:
        first = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
        second = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert first["outcome"] == "failed" and second["outcome"] == "blocked" and calls == [1]
    assert len(_state(config)["captures"]) == 1
    assert inspect(config, AT).to_dict()["stored_observations"] == 0


def test_sql_failure_offline_recovery_no_duplicate_http(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        with patch(
            "donghak_stock_vision.storage.krx_scheduler.ingest_capture",
            side_effect=sqlite3.OperationalError("private details not logged"),
        ):
            first = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
        assert first["reason"] == "ingestion_pending"
        assert _state(config)["pending"]["phase"] == "ingest_pending"
        second = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert second["reason"] == "offline_ingestion_resumed" and second["observation_count"] == 940
    assert calls == [1] and "private details" not in json.dumps(first)


def test_interrupted_unknown_request_never_retried(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        with patch(
            "donghak_stock_vision.storage.krx_scheduler.run_capture", side_effect=KeyboardInterrupt
        ):
            with pytest.raises(KeyboardInterrupt):
                run_once(config, execute=True, client=http, clock=lambda: AT)
        second = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert second["reason"] == "interrupted_capture_outcome_unknown" and not calls


def test_committed_ingest_interrupted_summary_replays_offline(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []
    original_save = _save

    def fail_completion(cfg: SchedulerConfig, name: str, doc: dict[str, Any]) -> None:
        if name == "state.json" and doc.get("last_run") is not None:
            raise KeyboardInterrupt
        original_save(cfg, name, doc)

    with client(calls) as http:
        with patch("donghak_stock_vision.storage.krx_scheduler._save", side_effect=fail_completion):
            with pytest.raises(KeyboardInterrupt):
                run_once(config, execute=True, client=http, clock=lambda: AT)
        result = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert result["reason"] == "offline_ingestion_resumed" and calls == [1]
    assert len(_state(config)["successes"]) == 1


def test_pause_and_resume_no_record_deletion(config: SchedulerConfig) -> None:
    with pytest.raises(ValueError):
        set_enabled(config, True)
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        run_once(config, execute=True, client=http, clock=lambda: AT)
        before = snapshot(config.path("capture_root"))
        set_enabled(config, False)
        assert (
            run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()["reason"]
            == "scheduler_disabled"
        )
        enabled(config)
        assert (
            run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()["reason"]
            == "already_collected"
        )
    assert snapshot(config.path("capture_root")) == before and calls == [1]


def test_concurrent_runner_lock(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []

    def overlap() -> None:
        with pytest.raises(ValueError, match="already_running"):
            run_once(config, execute=True, clock=lambda: AT)

    with client(calls, callback=overlap) as http:
        assert (
            run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()["outcome"]
            == "succeeded"
        )
    assert calls == [1]


def test_schedule_hour_and_host_timezone(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        with patch("donghak_stock_vision.storage.krx_scheduler.host_seoul", return_value=False):
            assert (
                run_once(
                    config, execute=True, scheduled=True, client=http, clock=lambda: AT
                ).to_dict()["reason"]
                == "host_timezone_not_asia_seoul"
            )
        with patch("donghak_stock_vision.storage.krx_scheduler.host_seoul", return_value=True):
            assert (
                run_once(
                    config, execute=True, scheduled=True, client=http, clock=lambda: AT
                ).to_dict()["reason"]
                == "scheduled_time_not_due"
            )
            assert (
                run_once(
                    config,
                    execute=True,
                    scheduled=True,
                    client=http,
                    clock=lambda: AT + timedelta(hours=10),
                ).to_dict()["outcome"]
                == "succeeded"
            )
    assert calls == [1]


def test_launchagent_generation_dry_run_no_system_changes(
    config: SchedulerConfig, tmp_path: Path
) -> None:
    path = tmp_path / "settings.json"
    with patch("subprocess.run", side_effect=AssertionError("no launchctl")):
        for action in ("install", "pause", "resume", "uninstall"):
            plan = krx_launchd.manage(config, path, action).to_dict()
            assert not plan["applied"] and plan["network_requests"] == 0
    body = plistlib.loads(krx_launchd.plist_bytes(config, path))
    assert body["Disabled"] and not body["RunAtLoad"] and not body["KeepAlive"]
    assert body["WorkingDirectory"] == str(tmp_path)
    assert body["ProgramArguments"][0] == sys.executable
    assert "run-due" in body["ProgramArguments"]
    assert body["StartCalendarInterval"] == [
        dict(Weekday=i, Hour=19, Minute=0) for i in range(1, 6)
    ]
    assert body["EnvironmentVariables"]["TZ"] == "Asia/Seoul"
    assert KEY not in str(body) and "AUTH_KEY" not in str(body)
    assert not config.path("runner_root").exists()

    # Only a fixture plist under tmp_path; never a user LaunchAgent or actual launchctl.
    fake = krx_launchd._agent_path()
    fake.parent.mkdir()
    fake.write_bytes(krx_launchd.plist_bytes(config, path))
    fake.chmod(0o600)
    enabled(config)

    def command(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        output = (
            ('"' + krx_launchd.LABEL + '" => true').encode() if args[1] == "print-disabled" else b""
        )
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr=b"")

    with (
        patch("donghak_stock_vision.storage.krx_launchd.sys.platform", "darwin"),
        patch("subprocess.run", side_effect=command),
    ):
        actual = krx_launchd.status(config, path)
    assert actual["application_enabled"] and not actual["launchd_enabled"] and not actual["enabled"]


@pytest.mark.parametrize(
    "field,value", [("timezone", "UTC"), ("hour", 24), ("minute", 60), ("capture_root", "relative")]
)
def test_bad_config_rejected(config: SchedulerConfig, field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        SchedulerConfig(FrozenJSON.freeze(config.document.to_dict() | {field: value}))


def test_missing_policy_status_and_cli_no_secrets(
    config: SchedulerConfig, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(config.document.payload_json)
    path.chmod(0o600)
    assert load_config(path, config.document.identifier) == config
    with pytest.raises(ValueError, match="pin_mismatch"):
        load_config(path, "0" * 64)
    (config.path("policy_root") / "recurring-policy.json").unlink()
    with patch("httpx.Client.send", side_effect=AssertionError("network")):
        assert (
            main(
                [
                    "krx-scheduler",
                    "status",
                    "--config",
                    str(path),
                    "--config-pin",
                    config.document.identifier,
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert "policy_or_credentials_unavailable" in result["blockers"]
        assert not result["launchagent"]["installed"]
    assert KEY not in json.dumps(result)
    assert not config.path("runner_root").exists()


def test_private_credentials_file(
    config: SchedulerConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "private.env"
    secret.write_text("KRX_API_KEY=" + KEY + "\n")
    secret.chmod(0o600)
    cfg = SchedulerConfig(
        FrozenJSON.freeze(config.document.to_dict() | {"credentials_file": str(secret)})
    )
    monkeypatch.delenv("KRX_API_KEY")
    enabled(cfg)
    calls: list[int] = []
    with client(calls) as http:
        result = run_once(cfg, execute=True, client=http, clock=lambda: AT).to_dict()
    assert result["outcome"] == "succeeded" and calls == [1] and KEY not in json.dumps(result)


def test_atomic_control_failure_keeps_old_state(config: SchedulerConfig) -> None:
    enabled(config)
    before = (config.path("runner_root") / "control.json").read_bytes()
    with patch("os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            set_enabled(config, False)
    assert (config.path("runner_root") / "control.json").read_bytes() == before
    assert control(config)["enabled"]


def test_malformed_state_rejected_before_capture(config: SchedulerConfig) -> None:
    enabled(config)
    path = config.path("runner_root") / "state.json"
    path.write_text("not JSON")
    path.chmod(0o600)
    with patch("httpx.Client.send", side_effect=AssertionError("network")):
        with pytest.raises(ValueError):
            run_once(config, execute=True, clock=lambda: AT)


def test_config_rejects_source_and_runner_overlap(config: SchedulerConfig) -> None:
    with pytest.raises(ValueError, match="separate_scheduler_storage"):
        SchedulerConfig(
            FrozenJSON.freeze(
                config.document.to_dict()
                | {"runner_root": str(config.path("capture_root") / "nested")}
            )
        )


def test_existing_unrelated_control_not_overwritten(config: SchedulerConfig) -> None:
    root = config.path("runner_root")
    root.mkdir(mode=0o700)
    path = root / "control.json"
    path.write_text("existing unrelated record")
    path.chmod(0o600)
    with pytest.raises(ValueError):
        enabled(config)
    assert path.read_text() == "existing unrelated record"


def test_mock_policy_cannot_use_real_client_factory(config: SchedulerConfig) -> None:
    enabled(config)
    with patch(
        "donghak_stock_vision.storage.krx_scheduler.capture_client",
        side_effect=AssertionError("client"),
    ):
        result = run_once(config, execute=True, clock=lambda: AT).to_dict()
    assert result["reason"] == "mock_policy_test_only" and not config.path("capture_root").exists()


def test_user_timezone_change_blocks_enable_but_allows_pause_plan(
    config: SchedulerConfig, tmp_path: Path
) -> None:
    with (
        patch("donghak_stock_vision.storage.krx_launchd.sys.platform", "darwin"),
        patch("donghak_stock_vision.storage.krx_launchd.host_seoul", return_value=False),
        patch("subprocess.run", side_effect=AssertionError("launchctl")),
    ):
        with pytest.raises(ValueError, match="timezone"):
            krx_launchd.manage(config, tmp_path / "config.json", "install", apply=True)
        assert (
            krx_launchd.manage(config, tmp_path / "config.json", "pause").to_dict()["applied"]
            is False
        )


def test_local_quota_exhaustion_stops_before_http(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        assert (
            run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()["outcome"]
            == "succeeded"
        )
        later = AT + timedelta(days=1)
        with closing(sqlite3.connect(config.path("capture_root") / "pilot.sqlite3")) as c:
            c.execute(
                "INSERT INTO request_budget VALUES(?,?,?)",
                (
                    later.astimezone(SEOUL).date().isoformat(),
                    2,
                    later.timestamp(),
                ),
            )
            c.commit()
        result = run_once(config, execute=True, client=http, clock=lambda: later).to_dict()
    assert result["outcome"] == "blocked" and "local_daily_budget_exhausted" in result["blockers"]
    assert calls == [1]


def test_control_during_capture_cancels_without_trading(config: SchedulerConfig) -> None:
    enabled(config)
    calls: list[int] = []
    with client(calls, callback=lambda: set_enabled(config, False)) as http:
        result = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert result["outcome"] == "failed" and calls == [1]
    assert not control(config)["enabled"]
    assert inspect(config, AT).to_dict()["stored_observations"] == 0


def test_production_scheduler_005930_original_capture_to_verified_database_readback(
    config: SchedulerConfig,
) -> None:
    """One bounded mock HTTP request traverses real production capture, storage and audit."""
    enabled(config)
    plan, _ = _plan(config, AT)
    raw = json.dumps(
        {
            "OutBlock_1": [
                row("005930", day=plan.day),
                row("000660", day=plan.day),
            ]
        },
        separators=(",", ":"),
    ).encode()
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "data-dbg.krx.co.kr"
        assert request.url.path == "/svc/apis/sto/stk_bydd_trd"
        assert request.url.params == {"basDd": plan.day.strftime("%Y%m%d")}
        assert request.headers["AUTH_KEY"] == KEY
        return httpx.Response(200, content=raw)

    with (
        patch("socket.create_connection", side_effect=AssertionError("live network forbidden")),
        httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as http,
    ):
        result = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
        assert result["outcome"] == "succeeded"
        assert result["observation_count"] == 2
        assert result["origin"] == "authorized_provider_mock_fixture"
        source_pin = _state(config)["successes"][0]
        capture_pin = source_pin["capture_pin"]
        original_root = config.path("capture_root")
        owner = verify_capture(original_root, plan, ATTEMPT, capture_pin)
        assert readback(original_root, plan, ATTEMPT, capture_pin, 0) == raw
        receipt = owner.to_dict()["responses"][0]["receipt"]
        assert receipt["rawSha256"] == hashlib.sha256(raw).hexdigest()
        before = snapshot(original_root)
        evidence = PilotEvidence(
            original_root,
            plan,
            plan.document.identifier,
            ATTEMPT,
            capture_pin,
            source_pin["batch_pin"],
            authorization=config.reference,
            authorization_policy="operator_full_market_recurring_v1",
        )
        report = audit_stored_005930(config.path("operational_root"), evidence).to_dict()
        assert report["ticker"] == "005930"
        assert report["source_origin"] == "authorized_provider_mock_fixture"
        assert report["verified_batch_pin"] == source_pin["batch_pin"]
        assert report["locally_verified"]
        assert report["independently_authenticated_market_origin"] is False
        assert report["network_requests"] == 0
        with closing(OperationalMarketStore(config.path("operational_root"))) as store:
            assert store.validate().to_dict()["valid"]
            names = store.connection.execute(
                "SELECT ticker FROM market_observations ORDER BY ticker"
            ).fetchall()
            assert names == [("000660",), ("005930",)]
        again = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
        assert again["reason"] == "already_collected"
        assert audit_stored_005930(config.path("operational_root"), evidence) == (
            audit_stored_005930(config.path("operational_root"), evidence)
        )
        assert snapshot(original_root) == before
    assert len(requests) == 1


def test_scheduler_persisted_batch_without_005930_cannot_pass_pilot_audit(
    config: SchedulerConfig,
) -> None:
    """Successful full-market ingestion is not evidence that Samsung was present."""
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        outcome = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert outcome["outcome"] == "succeeded" and calls == [1]
    plan, _ = _plan(config, AT)
    source = _state(config)["successes"][0]
    evidence = PilotEvidence(
        config.path("capture_root"),
        plan,
        plan.document.identifier,
        ATTEMPT,
        source["capture_pin"],
        source["batch_pin"],
        authorization=config.reference,
        authorization_policy="operator_full_market_recurring_v1",
    )
    with pytest.raises(ValueError, match="pilot_005930_missing_from_verified_batch"):
        audit_stored_005930(config.path("operational_root"), evidence)
    with closing(OperationalMarketStore(config.path("operational_root"))) as store:
        assert store.validate().to_dict()["valid"]
        assert store.status().to_dict()["observation_count"] == 940


def test_005930_interrupted_storage_recovery_does_not_issue_second_request(
    config: SchedulerConfig,
) -> None:
    """Pending ingestion may resume offline; capture-pending never silently retries."""
    enabled(config)
    plan, _ = _plan(config, AT)
    body = {"OutBlock_1": [row("005930", day=plan.day), row("000660", day=plan.day)]}
    requests: list[int] = []

    def transport(_: httpx.Request) -> httpx.Response:
        requests.append(1)
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as http:
        with patch(
            "donghak_stock_vision.storage.krx_scheduler.ingest_capture",
            side_effect=sqlite3.OperationalError("interrupted fixture ingestion"),
        ):
            failed = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
        assert failed["reason"] == "ingestion_pending"
        assert _state(config)["pending"]["phase"] == "ingest_pending"
        resumed = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert resumed["reason"] == "offline_ingestion_resumed"
    assert resumed["observation_count"] == 2
    assert requests == [1]
    source = _state(config)["successes"][0]
    evidence = PilotEvidence(
        config.path("capture_root"),
        plan,
        plan.document.identifier,
        ATTEMPT,
        source["capture_pin"],
        source["batch_pin"],
        authorization=config.reference,
        authorization_policy="operator_full_market_recurring_v1",
    )
    assert audit_stored_005930(config.path("operational_root"), evidence).to_dict()[
        "locally_verified"
    ]
