"""Offline temporary provisioning checks; fixtures never establish real KRX permission."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_krx_recurring import AT, execute
from tests.integration.test_krx_scheduler import client
from tests.integration.test_krx_scheduler import config as config
from tests.integration.test_operational_capture import snapshot

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.storage.krx_readiness import preflight
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig, run_once, set_enabled


def prepare(config: SchedulerConfig) -> Path:
    config.path("capture_root").mkdir(mode=0o700)
    p = config.path("working_directory") / "scheduler.json"
    p.write_text(config.document.payload_json)
    p.chmod(0o600)
    return p


def change_policy(config: SchedulerConfig, **values: object) -> SchedulerConfig:
    p = config.path("policy_root") / "recurring-policy.json"
    owner = FrozenJSON.freeze(json.loads(p.read_text()) | values)
    p.write_text(owner.payload_json)
    return replace(
        config,
        document=FrozenJSON.freeze(config.document.to_dict() | {"policy_pin": owner.identifier}),
    )


def test_offline_preflight_no_mutation_or_enablement(config: SchedulerConfig) -> None:
    p = prepare(config)
    before = snapshot(config.path("working_directory"))
    with patch.object(httpx.Client, "send", side_effect=AssertionError("network forbidden")):
        a = preflight(config, AT, p).to_dict()
        b = preflight(config, AT, p).to_dict()
    assert a == b and snapshot(config.path("working_directory")) == before
    assert a["ready_for_mock_capture"] and not a["ready_for_manual_provider_capture"]
    assert not a["ready_for_scheduled_collection"] and not a["scheduler_application_enabled"]
    assert a["request"]["credential_binding_verified"] and a["network_requests"] == 0
    assert a["policy_created"] is False and a["launchagent_changed"] is False
    assert not config.path("runner_root").exists()
    assert str(config.path("policy_root")) not in json.dumps(a)
    assert config.document.to_dict()["policy_pin"] not in json.dumps(a)
    assert all(
        a[n] is False
        for n in (
            "pit_qualified",
            "forward_signal_eligible",
            "paper_execution_eligible",
            "execution_authorized",
        )
    )


@pytest.mark.parametrize(
    "damage", ["policy", "key", "wrong_key", "archive", "permissions", "db", "corrupt_db"]
)
def test_missing_or_unusable_operational_input(
    config: SchedulerConfig, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    p = prepare(config)
    if damage == "policy":
        (config.path("policy_root") / "recurring-policy.json").unlink()
    elif damage == "key":
        monkeypatch.delenv("KRX_API_KEY")
    elif damage == "wrong_key":
        monkeypatch.setenv("KRX_API_KEY", "wrong-fixture-key")
    elif damage == "archive":
        config.path("capture_root").rmdir()
    elif damage == "permissions":
        config.path("capture_root").chmod(0o755)
    else:
        db = config.path("operational_root") / "database" / "operational.sqlite3"
        if damage == "db":
            db.unlink()
        else:
            db.write_bytes(b"corrupt synthetic database")
    result = preflight(config, AT, p).to_dict()
    assert result["blockers"] and not result["ready_for_mock_capture"]
    assert not result["ready_for_manual_provider_capture"] and result["network_requests"] == 0


@pytest.mark.parametrize("damage", ["expired", "service", "scope", "originals", "pin"])
def test_policy_validation_not_summary_trust(config: SchedulerConfig, damage: str) -> None:
    p = prepare(config)
    if damage == "expired":
        config = change_policy(config, approved_through="2025-07-06")
    elif damage == "service":
        config = change_policy(config, service_id="stk_isu_base_info")
    elif damage == "scope":
        d = config.document.to_dict()
        policy = json.loads((config.path("policy_root") / "recurring-policy.json").read_text())
        config = change_policy(
            config, full_market_scope=policy["full_market_scope"] | {"market": "KOSDAQ"}
        )
        assert d != config.document.to_dict()
    elif damage == "originals":
        (config.path("policy_root") / "terms.bin").write_bytes(b"modified")
    else:
        (config.path("policy_root") / "recurring-policy.json").write_text("{}")
    result = preflight(config, AT, p).to_dict()
    assert result["blockers"] and not result["ready_for_mock_capture"]


def test_expiry_and_no_eligible_completed_date(config: SchedulerConfig) -> None:
    p = prepare(config)
    for at in [AT + timedelta(days=7), AT - timedelta(days=4)]:
        result = preflight(config, at, p).to_dict()
        assert result["blockers"] and not result["ready_for_mock_capture"]


def test_weekend_execution_uses_latest_permitted_completed_session(config: SchedulerConfig) -> None:
    p = prepare(config)
    # Saturday 2025-07-12 is not an allowed market day; Friday 2025-07-11 is.
    at = AT + timedelta(days=4)
    with patch.object(httpx.Client, "send", side_effect=AssertionError("network forbidden")):
        result = preflight(config, at, p).to_dict()
    assert result["selected_trading_date"] == "2025-07-11"
    assert result["ready_for_mock_capture"] and not result["blockers"]
    assert result["network_requests"] == 0


def test_duplicate_exhausted_budget_and_pause(config: SchedulerConfig) -> None:
    p = prepare(config)
    config = change_policy(config, daily_limit=1)
    from donghak_stock_vision.storage.krx_scheduler import _plan

    plan, _ = _plan(config, AT)
    execute(config.path("capture_root"), plan, config.reference)
    result = preflight(config, AT, p).to_dict()
    assert "duplicate_collection_date" in result["blockers"]
    assert "local_daily_budget_exhausted" in result["blockers"]
    assert result["request"]["locally_used_requests"] == 1


def test_timezone_and_disabled_are_distinct_from_manual_readiness(config: SchedulerConfig) -> None:
    p = prepare(config)
    with patch("donghak_stock_vision.storage.krx_readiness.host_seoul", return_value=False):
        result = preflight(config, AT, p).to_dict()
    assert result["ready_for_mock_capture"]
    assert result["checks"]["host_timezone"]["state"] == "blocked"
    assert not result["ready_for_scheduled_collection"]
    assert result["launchagent"]["installed"] is False


def test_first_run_mock_then_verified_storage(config: SchedulerConfig) -> None:
    p = prepare(config)
    assert preflight(config, AT, p).to_dict()["ready_for_mock_capture"]
    set_enabled(config, True, allow_network=True, operator_confirmed=True)
    calls: list[int] = []
    with client(calls) as http:
        result = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert calls == [1] and result["observation_count"] == 940
    set_enabled(config, False)
    status = preflight(config, AT, p).to_dict()
    assert status["storage"]["observation_count"] == 940
    assert status["storage"]["duplicate_records"] == 0
    assert not status["ready_for_mock_capture"]  # Original source already reserved.
    assert not status["scheduler_application_enabled"]


@pytest.mark.parametrize(
    "flag",
    ["--apply", "--execute", "--allow-network", "--confirm-operator-authorization", "--scheduled"],
)
def test_cli_preflight_cannot_activate(
    config: SchedulerConfig, caplog: pytest.LogCaptureFixture, flag: str
) -> None:
    p = prepare(config)
    assert (
        main(
            [
                "krx-scheduler",
                "preflight",
                "--config",
                str(p),
                "--config-pin",
                config.document.identifier,
                flag,
            ]
        )
        == 2
    )
    assert "preflight_is_read_only" in caplog.text
    assert not config.path("runner_root").exists()


def test_cli_versioned_json(config: SchedulerConfig, capsys: pytest.CaptureFixture[str]) -> None:
    p = prepare(config)
    with patch("donghak_stock_vision.krx_scheduler_cli.datetime") as time:
        time.now.return_value = AT
        assert (
            main(
                [
                    "krx-scheduler",
                    "preflight",
                    "--config",
                    str(p),
                    "--config-pin",
                    config.document.identifier,
                ]
            )
            == 0
        )
    result = json.loads(capsys.readouterr().out)
    assert result["version"] == "krx_production_preflight_v1" and result["network_requests"] == 0


def test_storage_limit_and_bad_launchctl_do_not_activate(config: SchedulerConfig) -> None:
    from donghak_stock_vision.storage.operational_market import OperationalMarketStore

    p = prepare(config)
    original = OperationalMarketStore.usage
    with patch.object(
        OperationalMarketStore,
        "usage",
        lambda s: original(s) | {"free_space_requirement_met": False},
    ):
        result = preflight(config, AT, p).to_dict()
    assert "operational_storage_not_ready" in result["blockers"]
    import subprocess

    with patch(
        "donghak_stock_vision.storage.krx_readiness.krx_launchd.status",
        side_effect=subprocess.TimeoutExpired("launchctl", 10),
    ):
        result = preflight(config, AT, p).to_dict()
    assert result["launchagent"] is None and not result["ready_for_scheduled_collection"]
    assert not config.path("runner_root").exists()


def test_pending_capture_blocks_without_retry(config: SchedulerConfig) -> None:
    from donghak_stock_vision.storage.krx_scheduler import _plan, _save, _state

    p = prepare(config)
    plan, _ = _plan(config, AT)
    state = _state(config)
    state["pending"] = dict(
        phase="capture_pending",
        plan=plan.document.to_dict(),
        capture_pin=None,
        started_at=AT.isoformat(),
    )
    _save(config, "state.json", state)
    before = snapshot(config.path("working_directory"))
    result = preflight(config, AT, p).to_dict()
    assert "scheduler_state_not_ready" in result["blockers"]
    assert not result["ready_for_mock_capture"]
    assert snapshot(config.path("working_directory")) == before
