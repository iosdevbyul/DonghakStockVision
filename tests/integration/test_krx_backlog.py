"""Synthetic bounded catch-up only; no real KRX requests or LaunchAgent activation."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_forward_capture import Clock
from tests.integration.test_krx_recurring import AT, execute
from tests.integration.test_krx_scheduler import config as config
from tests.integration.test_market_collection import row
from tests.integration.test_operational_capture import snapshot

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import FullMarketScope
from donghak_stock_vision.storage.krx_backlog import backlog, register_completion, run_due
from donghak_stock_vision.storage.krx_scheduler import (
    SchedulerConfig,
    _daily_plan,
    _lock,
    _policy,
    _save,
    _state,
    control,
    set_enabled,
)
from donghak_stock_vision.storage.operational_capture import ingest_capture
from donghak_stock_vision.storage.operational_market import OperationalMarketStore


@pytest.fixture
def setup(config: SchedulerConfig) -> SchedulerConfig:
    p = config.path("policy_root") / "recurring-policy.json"
    d = FrozenJSON(p.read_text()).to_dict()
    scope = FullMarketScope.from_dict(d["full_market_scope"])
    scope = replace(
        scope,
        valid_until=scope.valid_until + timedelta(days=3),
        permitted_days=tuple(
            datetime.fromisoformat("2025-07-" + n).date()
            for n in ["03", "04", "07", "08", "09", "10", "11"]
        ),
    )
    d.update(
        full_market_scope=scope.to_dict(),
        daily_limit=100,
        valid_until=scope.valid_until.isoformat(),
    )
    owner = FrozenJSON.freeze(d)
    p.write_text(owner.payload_json)
    return SchedulerConfig(
        FrozenJSON.freeze(config.document.to_dict() | {"policy_pin": owner.identifier})
    )


def transport(
    calls: list[str], *, fail: bool = False, interrupt: bool = False, callback: Any = None
) -> httpx.Client:
    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "data-dbg.krx.co.kr"
        assert set(request.url.params) == {"basDd"}
        day = datetime.strptime(request.url.params["basDd"], "%Y%m%d").date()
        calls.append(day.isoformat())
        if interrupt:
            raise KeyboardInterrupt
        if fail:
            raise httpx.ConnectError("synthetic offline")
        if callback:
            callback()
        return httpx.Response(
            200, json={"OutBlock_1": [row(t, day) for t in ["005930", "000001", "000002"]]}
        )

    return httpx.Client(transport=httpx.MockTransport(reply), trust_env=False)


def enable(config: SchedulerConfig) -> None:
    set_enabled(config, True, allow_network=True, operator_confirmed=True)


def run(config: SchedulerConfig, calls: list[str], **kwargs: Any) -> dict[str, Any]:
    clock = Clock(AT)
    with transport(calls) as client:
        return run_due(
            config,
            execute=True,
            client=client,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
            **kwargs,
        ).to_dict()


def test_oldest_first_persistent_verified_completions(setup: SchedulerConfig) -> None:
    cfg = setup
    enable(cfg)
    calls: list[str] = []
    before = (cfg.path("policy_root") / "recurring-policy.json").read_bytes()
    result = run(cfg, calls)
    assert calls == ["2025-07-03", "2025-07-04", "2025-07-07"]
    assert result["stop_reason"] == "no_due_dates" and len(result["results"]) == 3
    assert (cfg.path("policy_root") / "recurring-policy.json").read_bytes() == before
    current = SchedulerConfig(FrozenJSON(cfg.document.payload_json))
    view = backlog(current, AT).to_dict()
    assert [r["day"] for r in view["completed"]] == calls and view["stored_observations"] == 9
    assert view["missing_dates"] == [] and view["last_batch"]["stop_reason"] == "no_due_dates"
    assert not run(current, calls)["results"] and len(calls) == 3
    with closing(OperationalMarketStore(cfg.path("operational_root"))) as store:
        assert (
            store.validate().to_dict()["valid"]
            and store.status().to_dict()["duplicate_records"] == 0
        )


def test_daily_limit_one_preserved_and_resume_next_day(setup: SchedulerConfig) -> None:
    p = setup.path("policy_root") / "recurring-policy.json"
    d = FrozenJSON(p.read_text()).to_dict() | {"daily_limit": 1}
    owner = FrozenJSON.freeze(d)
    p.write_text(owner.payload_json)
    cfg = SchedulerConfig(
        FrozenJSON.freeze(setup.document.to_dict() | {"policy_pin": owner.identifier})
    )
    enable(cfg)
    calls: list[str] = []
    result = run(cfg, calls)
    assert calls == ["2025-07-03"] and result["stop_reason"] == "daily_budget_exhausted"
    clock = Clock(AT + timedelta(days=1))
    with transport(calls) as c:
        result = run_due(
            cfg,
            execute=True,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert (
        calls == ["2025-07-03", "2025-07-04"] and result["stop_reason"] == "daily_budget_exhausted"
    )


def test_outside_lag_never_sent_and_today_not_filled(setup: SchedulerConfig) -> None:
    enable(setup)
    calls: list[str] = []
    clock = Clock(AT + timedelta(days=6))
    with transport(calls) as c:
        result = run_due(
            setup,
            execute=True,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert calls == ["2025-07-07", "2025-07-08", "2025-07-09", "2025-07-10", "2025-07-11"]
    assert backlog(setup, clock.now()).to_dict()["expired_dates"] == ["2025-07-03", "2025-07-04"]
    assert result["stop_reason"] == "no_due_dates"


def test_disable_preview_lock_and_time_guards(setup: SchedulerConfig) -> None:
    before = snapshot(setup.path("working_directory"))
    with patch("httpx.Client.send", side_effect=AssertionError("network")):
        assert run_due(setup, clock=lambda: AT).to_dict()["due_dates"] == [
            "2025-07-03",
            "2025-07-04",
            "2025-07-07",
        ]
    assert snapshot(setup.path("working_directory")) == before
    assert (
        run_due(setup, execute=True, clock=lambda: AT).to_dict()["stop_reason"]
        == "scheduler_disabled"
    )
    enable(setup)
    with _lock(setup, ".run.lock"), pytest.raises(ValueError, match="already_running"):
        run_due(setup, execute=True, clock=lambda: AT)
    calls: list[str] = []
    assert (
        run(setup, calls, scheduled=True)["stop_reason"] == "scheduled_time_not_due" and not calls
    )
    with patch("donghak_stock_vision.storage.krx_scheduler.host_seoul", return_value=False):
        assert run(setup, calls, scheduled=True)["stop_reason"] == "host_timezone_not_asia_seoul"
    assert not calls


def test_scheduled_1900_and_missing_execution_day(setup: SchedulerConfig) -> None:
    enable(setup)
    calls: list[str] = []
    clock = Clock(AT + timedelta(hours=10))
    with (
        patch("donghak_stock_vision.storage.krx_scheduler.host_seoul", return_value=True),
        transport(calls) as c,
    ):
        result = run_due(
            setup,
            execute=True,
            scheduled=True,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert len(calls) == 3 and result["stop_reason"] == "no_due_dates"
    assert "2025-07-08" not in calls


def test_known_failure_not_retried_and_other_days_remain(setup: SchedulerConfig) -> None:
    enable(setup)
    calls: list[str] = []
    clock = Clock(AT)
    with transport(calls, fail=True) as c:
        result = run_due(
            setup,
            execute=True,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert calls == ["2025-07-03"] and result["outcome"] == "failed"
    result = run(setup, calls)
    assert calls == ["2025-07-03", "2025-07-04", "2025-07-07"]
    assert backlog(setup, AT).to_dict()["reserved_without_verified_completion"] == ["2025-07-03"]


def test_unknown_request_blocks_all_retries(setup: SchedulerConfig) -> None:
    enable(setup)
    calls: list[str] = []
    with transport(calls, interrupt=True) as c, pytest.raises(KeyboardInterrupt):
        run_due(setup, execute=True, client=c, clock=lambda: AT)
    assert _state(setup)["pending"]["phase"] == "capture_pending"
    result = run(setup, calls)
    assert result["stop_reason"] == "interrupted_capture_outcome_unknown" and len(calls) == 1


def test_offline_ingestion_resume_before_next_request(setup: SchedulerConfig) -> None:
    enable(setup)
    calls: list[str] = []
    with patch(
        "donghak_stock_vision.storage.krx_scheduler.ingest_capture",
        side_effect=sqlite3.OperationalError("synthetic disk"),
    ):
        result = run(setup, calls)
    assert result["outcome"] == "failed" and calls == ["2025-07-03"]
    assert _state(setup)["pending"]["phase"] == "ingest_pending"
    result = run(setup, calls)
    assert result["results"][0]["reason"] == "offline_ingestion_resumed"
    assert calls == ["2025-07-03", "2025-07-04", "2025-07-07"]


def manual(config: SchedulerConfig, day: int = 3) -> tuple[Any, str, str]:
    plan = _daily_plan(_policy(config), datetime(2025, 7, day).date())
    owner = execute(config.path("capture_root"), plan, config.reference, attempt="manual", count=3)
    result = ingest_capture(
        config.path("operational_root"),
        config.path("capture_root"),
        plan,
        plan.document.identifier,
        "manual",
        owner.identifier,
        authorization=config.reference,
        authorization_policy="operator_full_market_recurring_v1",
    )
    return plan, owner.identifier, result.to_dict()["batch_pin"]


def test_explicit_manual_registration_and_no_auto_trust(setup: SchedulerConfig) -> None:
    plan, pin, batch = manual(setup)
    view = backlog(setup, AT).to_dict()
    assert view["completed"] == [] and view["reserved_without_verified_completion"] == [
        "2025-07-03"
    ]
    before = snapshot(setup.path("capture_root"))
    result = register_completion(
        setup, plan, plan.document.identifier, "manual", pin, batch, at=AT
    ).to_dict()
    assert result["network_requests"] == 0 and result["observation_count"] == 3
    assert register_completion(
        setup, plan, plan.document.identifier, "manual", pin, batch, at=AT
    ).to_dict()["already_registered"]
    assert snapshot(setup.path("capture_root")) == before
    enable(setup)
    calls: list[str] = []
    run(setup, calls)
    assert calls == ["2025-07-04", "2025-07-07"]


@pytest.mark.parametrize(
    "damage", ["capture_pin", "plan_pin", "batch_pin", "database", "source", "count", "missing_db"]
)
def test_tampering_cannot_hide_gaps_or_trigger_network(setup: SchedulerConfig, damage: str) -> None:
    plan, pin, batch = manual(setup)
    if damage.endswith("_pin"):
        args = dict(plan_pin=plan.document.identifier, capture_pin=pin, batch_pin=batch)
        args[damage] = "0" * 64
        with pytest.raises(ValueError):
            register_completion(setup, plan, attempt="manual", at=AT, **args)
        return
    register_completion(setup, plan, plan.document.identifier, "manual", pin, batch, at=AT)
    if damage == "source":
        (setup.path("capture_root") / "pilot.sqlite3").write_bytes(b"corrupt")
    elif damage == "missing_db":
        (setup.path("operational_root") / "database/operational.sqlite3").unlink()
    elif damage == "count":
        path = setup.path("runner_root") / "verified-completions.json"
        envelope = FrozenJSON(path.read_text()).to_dict()
        doc = envelope["document"]
        doc["records"][0]["observation_count"] = 900
        changed = FrozenJSON.freeze(doc)
        path.write_text(FrozenJSON.freeze(dict(document=doc, pin=changed.identifier)).payload_json)
    else:
        with sqlite3.connect(setup.path("operational_root") / "database/operational.sqlite3") as c:
            c.execute("DROP TRIGGER market_observations_immutable_delete")
            c.execute("DELETE FROM market_observations")
    enable(setup)
    calls: list[str] = []
    result = run(setup, calls)
    assert not calls and result["stop_reason"] == "completion_or_storage_verification_failed"


@pytest.mark.parametrize("limit", [0, 8, True])
def test_run_count_bound(setup: SchedulerConfig, limit: Any) -> None:
    with pytest.raises(ValueError, match="bounded_backlog"):
        run_due(setup, max_runs=limit, clock=lambda: AT)


def test_bounded_batch_and_pause_between_days(setup: SchedulerConfig) -> None:
    enable(setup)
    calls: list[str] = []
    assert len(run(setup, calls, max_runs=1)["results"]) == 1 and calls == ["2025-07-03"]
    from donghak_stock_vision.storage.krx_scheduler import _run_locked as original

    def paused(*a: Any, **kw: Any) -> Any:
        r = original(*a, **kw)
        set_enabled(setup, False)
        return r

    with patch("donghak_stock_vision.storage.krx_backlog._run_locked", side_effect=paused):
        result = run(setup, calls)
    assert result["stop_reason"] == "scheduler_disabled" and calls == ["2025-07-03", "2025-07-04"]
    assert len(backlog(setup, AT).to_dict()["completed"]) == 2


def test_new_policy_preserves_original_sources_and_archive_quota(setup: SchedulerConfig) -> None:
    import shutil

    plan, pin, batch = manual(setup)
    old_ref = setup.reference
    root = setup.path("working_directory") / "renewed-policy"
    shutil.copytree(setup.path("policy_root"), root)
    renewed = FrozenJSON((root / "recurring-policy.json").read_text()).to_dict()
    renewed["daily_limit"] = 90
    renewed["full_market_scope"]["permitted_days"].remove("2025-07-03")
    policy = FrozenJSON.freeze(renewed)
    (root / "recurring-policy.json").write_text(policy.payload_json)
    cfg = SchedulerConfig(
        FrozenJSON.freeze(
            setup.document.to_dict()
            | {
                "policy_root": str(root),
                "policy_pin": policy.identifier,
                "runner_root": str(setup.path("working_directory") / "new-runner"),
            }
        )
    )
    register_completion(
        cfg, plan, plan.document.identifier, "manual", pin, batch, authorization=old_ref, at=AT
    )
    assert backlog(cfg, AT).to_dict()["local_request_budget"]["used"] == 1
    enable(cfg)
    calls: list[str] = []
    run(cfg, calls)
    assert calls == ["2025-07-04", "2025-07-07"]
    assert len(backlog(cfg, AT).to_dict()["completed"]) == 3


def test_registration_atomicity_and_independent_batch_pin(setup: SchedulerConfig) -> None:
    plan, pin, batch = manual(setup)
    register_completion(setup, plan, plan.document.identifier, "manual", pin, batch, at=AT)
    path = setup.path("runner_root") / "verified-completions.json"
    old = path.read_bytes()
    other, other_pin, other_batch = manual(setup, 4)
    with patch("os.replace", side_effect=OSError("synthetic interruption")):
        with pytest.raises(OSError):
            register_completion(
                setup, other, other.document.identifier, "manual", other_pin, other_batch, at=AT
            )
    assert path.read_bytes() == old
    assert [r["day"] for r in backlog(setup, AT).to_dict()["completed"]] == ["2025-07-03"]
    history = [
        {"day": (datetime(2000, 1, 1) + timedelta(days=i)).date().isoformat()} for i in range(366)
    ]
    with patch("donghak_stock_vision.storage.krx_backlog._records", return_value=history):
        with pytest.raises(ValueError, match="completion_limit"):
            register_completion(
                setup, other, other.document.identifier, "manual", other_pin, other_batch, at=AT
            )
    assert path.read_bytes() == old


def test_unlisted_execution_day_can_recover_only_permitted_observations(
    setup: SchedulerConfig,
) -> None:
    enable(setup)
    calls: list[str] = []
    clock = Clock(AT + timedelta(days=4, hours=10))
    with (
        patch("donghak_stock_vision.storage.krx_scheduler.host_seoul", return_value=True),
        transport(calls) as c,
    ):
        result = run_due(
            setup,
            execute=True,
            scheduled=True,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert calls == ["2025-07-07", "2025-07-08", "2025-07-09", "2025-07-10", "2025-07-11"]
    assert result["stop_reason"] == "no_due_dates" and "2025-07-12" not in calls


def test_cooperative_time_budget(setup: SchedulerConfig) -> None:
    enable(setup)
    calls: list[str] = []
    clock = Clock(AT)
    with transport(calls, callback=lambda: clock.sleep(2)) as c:
        result = run_due(
            setup,
            execute=True,
            max_seconds=1,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert calls == ["2025-07-03"] and result["stop_reason"] == "cooperative_batch_time_limit"
    from donghak_stock_vision.storage.krx_backlog import backlog as original

    clock = Clock(AT)

    def slow_view(*args: Any, **kwargs: Any) -> Any:
        clock.sleep(2)
        return original(*args, **kwargs)

    with (
        patch("donghak_stock_vision.storage.krx_backlog.backlog", side_effect=slow_view),
        transport(calls) as c,
    ):
        result = run_due(
            setup,
            execute=True,
            max_seconds=1,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert calls == ["2025-07-03"] and result["stop_reason"] == "cooperative_batch_time_limit"


@pytest.mark.parametrize("limit", [0, 301, False])
def test_time_budget_validation(setup: SchedulerConfig, limit: Any) -> None:
    with pytest.raises(ValueError, match="bounded_backlog"):
        run_due(setup, max_seconds=limit, clock=lambda: AT)


def test_cli_preview_and_offline_registration(
    setup: SchedulerConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, pin, batch = manual(setup)
    cfg = setup.path("working_directory") / "config.json"
    cfg.write_text(setup.document.payload_json)
    cfg.chmod(0o600)
    common = ["--config", str(cfg), "--config-pin", setup.document.identifier]
    source = setup.path("working_directory") / "source-plan.json"
    source.write_text(plan.document.payload_json)
    with patch("donghak_stock_vision.krx_scheduler_cli.datetime") as t:
        t.now.return_value = AT
        assert main(["krx-scheduler", "backlog", *common]) == 0
    assert json.loads(capsys.readouterr().out)["reserved_without_verified_completion"] == [
        "2025-07-03"
    ]
    assert (
        main(
            [
                "krx-scheduler",
                "register-completion",
                *common,
                "--source-plan",
                str(source),
                "--source-plan-pin",
                plan.document.identifier,
                "--source-attempt",
                "manual",
                "--capture-pin",
                pin,
                "--batch-pin",
                batch,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["network_requests"] == 0
    assert not control(setup)["enabled"]
    assert main(["krx-scheduler", "backlog", *common, "--execute"]) == 2
    assert main(["krx-scheduler", "run-due", *common, "--allow-network"]) == 2


def test_lost_capture_history_does_not_repeat_existing_database_date(
    setup: SchedulerConfig,
) -> None:
    manual(setup)
    archive = setup.path("capture_root") / "pilot.sqlite3"
    original_archive = archive.read_bytes()
    archive.unlink()
    enable(setup)
    calls: list[str] = []
    result = run(setup, calls)
    assert not calls
    assert result["stop_reason"] == "stored_data_requires_original_completion_registration"
    assert backlog(setup, AT).to_dict()["stored_without_verified_history"] == ["2025-07-03"]
    for damage in ("DELETE FROM request_budget", "DROP TABLE request_budget"):
        archive.write_bytes(original_archive)
        archive.chmod(0o600)
        with sqlite3.connect(archive) as connection:
            connection.execute(damage)
        result = run(setup, calls)
        assert not calls and result["stop_reason"] == "completion_or_storage_verification_failed"


def test_explicit_registration_can_resolve_matching_uncertain_pending(
    setup: SchedulerConfig,
) -> None:
    from donghak_stock_vision.storage.krx_scheduler import ATTEMPT

    plan = _daily_plan(_policy(setup), datetime(2025, 7, 3).date())
    owner = execute(setup.path("capture_root"), plan, setup.reference, attempt=ATTEMPT, count=3)
    result = ingest_capture(
        setup.path("operational_root"),
        setup.path("capture_root"),
        plan,
        plan.document.identifier,
        ATTEMPT,
        owner.identifier,
        authorization=setup.reference,
        authorization_policy="operator_full_market_recurring_v1",
    )
    state = _state(setup)
    state["pending"] = dict(
        phase="capture_pending",
        plan=plan.document.to_dict(),
        capture_pin=None,
        started_at=AT.isoformat(),
    )
    _save(setup, "state.json", state)
    register_completion(
        setup,
        plan,
        plan.document.identifier,
        ATTEMPT,
        owner.identifier,
        result.to_dict()["batch_pin"],
        at=AT,
    )
    assert _state(setup)["pending"] is None
    assert backlog(setup, AT).to_dict()["completed"][0]["day"] == "2025-07-03"


@pytest.mark.parametrize(
    "damage", ["missing_key", "wrong_key", "expired", "wrong_service", "invalid_store"]
)
def test_preflight_failure_never_calls_provider(
    setup: SchedulerConfig, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    cfg = setup
    at = AT
    if damage == "missing_key":
        monkeypatch.delenv("KRX_API_KEY")
    if damage == "wrong_key":
        monkeypatch.setenv("KRX_API_KEY", "incorrect-synthetic-key")
    if damage == "expired":
        at = AT + timedelta(days=9)
    if damage == "wrong_service":
        p = cfg.path("policy_root") / "recurring-policy.json"
        owner = FrozenJSON.freeze(
            FrozenJSON(p.read_text()).to_dict() | {"service_id": "stk_isu_base_info"}
        )
        p.write_text(owner.payload_json)
        cfg = SchedulerConfig(
            FrozenJSON.freeze(cfg.document.to_dict() | {"policy_pin": owner.identifier})
        )
    if damage == "invalid_store":
        (cfg.path("operational_root") / "database/operational.sqlite3").write_bytes(b"corrupt")
    enable(cfg)
    calls: list[str] = []
    clock = Clock(at)
    with transport(calls) as c:
        result = run_due(
            cfg,
            execute=True,
            client=c,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
        ).to_dict()
    assert not calls and result["stop_reason"] != "no_due_dates"


def test_old_completion_remains_verified_after_current_lag_expires(setup: SchedulerConfig) -> None:
    plan, pin, batch = manual(setup)
    register_completion(setup, plan, plan.document.identifier, "manual", pin, batch, at=AT)
    view = backlog(setup, AT + timedelta(days=20)).to_dict()
    assert [r["day"] for r in view["completed"]] == ["2025-07-03"]
    assert "2025-07-03" not in view["expired_dates"] and view["due_dates"] == []
