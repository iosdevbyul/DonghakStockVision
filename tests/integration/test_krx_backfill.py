"""Temporary synthetic originals and the real validators/recorder; no live KRX calls."""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Self
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_krx_recurring import AT
from tests.integration.test_krx_scheduler import config as config
from tests.integration.test_market_collection import row
from tests.integration.test_operational_capture import snapshot

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.official_evidence import KIND_URL, normalize_month
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.krx_backfill import BackfillPlan, BackfillPolicy
from donghak_stock_vision.providers.http import RequestGate
from donghak_stock_vision.storage.krx_backfill import (
    DB,
    inspect_backfill,
    load_plan,
    load_policy,
    prepare_plan,
    run_backfill,
)
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig, _lock
from donghak_stock_vision.storage.operational_market import OperationalMarketStore
from donghak_stock_vision.storage.sqlite import SQLiteStore


def private(path: Path, value: str) -> None:
    path.write_text(value)
    path.chmod(0o600)


@pytest.fixture
def policy(tmp_path: Path, config: SchedulerConfig) -> BackfillPolicy:
    config_path = tmp_path / "scheduler.json"
    private(config_path, config.document.payload_json)
    research_root = tmp_path / "research"
    research_root.mkdir(mode=0o700)
    research = research_root / "market.sqlite3"
    with closing(SQLiteStore(research)):
        pass
    raw = '<table id="calBig">'
    for day in range(1, 31):
        weekday = date(2025, 6, day).weekday()
        css = "sat" if weekday == 5 else "sun" if weekday == 6 else ""
        label = " \ud604\ucda9\uc77c" if day == 6 else ""
        raw += f'<tr><td class="{css}">{day}{label}</td></tr>'
    raw += "</table>"
    raw_path, source_path = tmp_path / "calendar.html", tmp_path / "calendar.json"
    raw_path.write_bytes(raw.encode())
    source = dict(
        url=KIND_URL,
        status=200,
        raw_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        params=dict(method="searchTraidingCalendar", ldMktTpCd="1", selYear="2025", selMonth="6"),
    )
    private(source_path, json.dumps(source))
    pins = tmp_path / "source-pins"
    pins.mkdir(mode=0o700)
    return BackfillPolicy(
        FrozenJSON.freeze(
            dict(
                version="operator_krx_historical_backfill_v1",
                acknowledgement="private_historical_acquisition_not_forward_authority",
                reviewed_at=AT.isoformat(),
                valid_from=AT.isoformat(),
                valid_until=(AT + timedelta(days=5)).isoformat(),
                start="2025-06-02",
                end="2025-06-06",
                scheduler_config=str(config_path),
                scheduler_config_pin=config.document.identifier,
                research_database=str(research),
                research_database_sha256=hashlib.sha256(research.read_bytes()).hexdigest(),
                archive_root=str(tmp_path / "historical"),
                source_pins_root=str(pins),
                calendars=[
                    dict(
                        raw=str(raw_path),
                        source=str(source_path),
                        normalized_pin=normalize_month(raw.encode(), source).identifier,
                    )
                ],
                max_requests=4,
                daily_limit=10,
                reserved_daily_requests=2,
                known_request_ledgers=[],
                min_rows=2,
                required_tickers=["005930"],
            )
        )
    )


def changed(policy: BackfillPolicy, **values: Any) -> BackfillPolicy:
    return BackfillPolicy(FrozenJSON.freeze(policy.values | values))


def response_client(
    calls: list[str],
    *,
    status: int = 200,
    empty: bool = False,
    invalid: bool = False,
    timeout: bool = False,
    zipped: bool = False,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        value = request.url.params["basDd"]
        calls.append(value)
        assert request.url.path.endswith("/stk_bydd_trd")
        if timeout:
            raise httpx.ReadTimeout("synthetic timeout")
        day = date(int(value[:4]), int(value[4:6]), int(value[6:]))
        rows = [] if empty else [row("005930", day), row("000660", day)]
        if invalid:
            rows[0]["BAS_DD"] = "20250601"
        body = json.dumps({"OutBlock_1": rows}).encode()
        if zipped:
            import gzip

            return httpx.Response(
                status, content=gzip.compress(body), headers={"content-encoding": "gzip"}
            )
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)


def execute(
    policy: BackfillPolicy, plan: BackfillPlan, client: httpx.Client | None = None, **kwargs: Any
) -> FrozenJSON:
    return run_backfill(
        policy,
        plan,
        execute=True,
        allow_synthetic_fixture=True,
        client=client,
        clock=lambda: AT,
        sleep=lambda _: None,
        **kwargs,
    )


def test_historical_full_market_readback_and_replay(
    policy: BackfillPolicy, config: SchedulerConfig
) -> None:
    plan = prepare_plan(policy, AT)
    assert plan.document.to_dict()["dates"] == [
        "2025-06-05",
        "2025-06-04",
        "2025-06-03",
        "2025-06-02",
    ]
    before = snapshot(config.path("policy_root"))
    calls: list[str] = []
    with response_client(calls) as client:
        result = execute(
            policy, plan, client, allow_network=True, operator_confirmed=True
        ).to_dict()
        assert result["requests_reserved"] == 2 and result["new_observations"] == 4
        assert result["successful_dates"] == 2 and calls == ["20250605", "20250604"]
        second = execute(
            policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=4
        ).to_dict()
        assert second["successful_dates"] == 2 and len(calls) == 4
        replay = execute(
            policy, plan, client, allow_network=True, operator_confirmed=True
        ).to_dict()
        assert (
            replay["requests_reserved"] == 0 and replay["new_observations"] == 0 and len(calls) == 4
        )
    assert snapshot(config.path("policy_root")) == before
    with patch("httpx.Client.send", side_effect=AssertionError("network forbidden")):
        report = inspect_backfill(policy, plan, AT).to_dict()
    assert len(report["results"]) == 4
    assert report["account_wide_remaining_quota"] == "UNKNOWN"
    assert (
        not report["independent_publication_time_verified"]
        and report["performance_metrics"] is None
    )
    with closing(OperationalMarketStore(config.path("operational_root"))) as store:
        assert store.validate().to_dict()["valid"]
        assert store.status().to_dict()["observation_count"] == 8
        assert store.status().to_dict()["duplicate_records"] == 0
        ref = json.loads(
            store.connection.execute("SELECT document FROM ingestion_batches LIMIT 1").fetchone()[0]
        )["request"]["source_capture"]
        assert ref["version"] == "operational_backfill_reference_v1"
        assert ref["origin"] == "authorized_provider_mock_fixture"


@pytest.mark.parametrize(
    "status,empty,invalid,timeout,reason",
    [
        (403, False, False, False, "http_or_transport_failure"),
        (429, False, False, False, "http_or_transport_failure"),
        (200, False, False, True, "http_or_transport_failure"),
        (200, True, False, False, "empty_market_response"),
        (200, False, True, False, "invalid_market_response"),
    ],
)
def test_stop_without_retry(
    policy: BackfillPolicy, status: int, empty: bool, invalid: bool, timeout: bool, reason: str
) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with response_client(
        calls, status=status, empty=empty, invalid=invalid, timeout=timeout
    ) as client:
        result = execute(
            policy, plan, client, allow_network=True, operator_confirmed=True
        ).to_dict()
        assert result["stop_reason"] == reason and result["new_observations"] == 0
        again = execute(policy, plan, client, allow_network=True, operator_confirmed=True).to_dict()
        assert again["stop_reason"] == "backfill_previous_failure_requires_review_no_retry"
    assert calls == ["20250605"]
    assert result["results"][0]["outcome"] == ("no_data" if empty else "failed")


def test_ingest_interruption_resumes_offline(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with (
        response_client(calls) as client,
        patch.object(OperationalMarketStore, "ingest", side_effect=OSError("interrupted")),
    ):
        with pytest.raises(OSError):
            execute(policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1)
    with patch("httpx.Client.send", side_effect=AssertionError("offline recovery")):
        result = execute(policy, plan, max_dates=1).to_dict()
    assert result["successful_dates"] == 1 and result["new_observations"] == 2
    assert calls == ["20250605"]


def test_commit_before_completion_replays_idempotently(policy: BackfillPolicy) -> None:
    import donghak_stock_vision.storage.krx_backfill as module

    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with (
        response_client(calls) as client,
        patch.object(module, "_stored", side_effect=OSError("crashed after commit")),
    ):
        with pytest.raises(OSError):
            execute(policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1)
    result = execute(policy, plan, max_dates=1).to_dict()
    assert result["successful_dates"] == 1 and result["new_observations"] == 0
    assert len(calls) == 1


def test_unknown_request_outcome_never_retries(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with (
        response_client(calls) as client,
        patch(
            "donghak_stock_vision.storage.krx_backfill.archive_raw_page",
            side_effect=KeyboardInterrupt,
        ),
    ):
        with pytest.raises(KeyboardInterrupt):
            execute(policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1)
    with (
        response_client(calls) as client,
        pytest.raises(ValueError, match="outcome_unknown_no_retry"),
    ):
        execute(policy, plan, client, allow_network=True, operator_confirmed=True)
    assert len(calls) == 1


def test_independent_source_pin_required(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with response_client(calls) as client:
        execute(policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1)
    pin = Path(policy.values["source_pins_root"]) / "source-2025-06-05.pin"
    pin.unlink()
    with pytest.raises(ValueError, match="private_backfill_file_required"):
        inspect_backfill(policy, plan, AT)


@pytest.mark.parametrize("target", ["raw", "receipt", "metadata", "completion"])
def test_tampered_original_or_rehashed_report_rejected(policy: BackfillPolicy, target: str) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with response_client(calls) as client:
        execute(policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1)
    dbpath = Path(policy.values["archive_root"]) / DB
    with closing(sqlite3.connect(dbpath)) as db, db:
        if target == "raw":
            db.execute("DROP TRIGGER backfill_raw_no_UPDATE")
            db.execute("UPDATE raw_pages SET body=x'7b7d'")
        elif target == "receipt":
            db.execute("DROP TRIGGER raw_capture_receipts_no_update")
            db.execute("UPDATE raw_capture_receipts SET raw_sha256=?", ("0" * 64,))
        elif target == "metadata":
            db.execute("DROP TRIGGER backfill_responses_no_UPDATE")
            db.execute(
                "UPDATE backfill_responses SET metadata=json_set(metadata,'$.http_status',201)"
            )
        else:
            db.execute("DROP TRIGGER backfill_completions_no_UPDATE")
            db.execute(
                "UPDATE backfill_completions SET "
                "document=json_set(document,'$.observation_count',999)"
            )
    with pytest.raises(ValueError):
        inspect_backfill(policy, plan, AT)


def test_skip_existing_originals_and_bars_without_network(
    policy: BackfillPolicy, config: SchedulerConfig
) -> None:
    research = Path(policy.values["research_database"])
    from donghak_stock_vision.providers.base import RawPage

    with closing(SQLiteStore(research)) as store:
        store.archive(
            "existing-empty",
            "krx",
            "@krx_market:KOSPI",
            RawPage(b'{"OutBlock_1":[]}', AT, date(2025, 6, 5)),
        )
    policy = changed(
        policy, research_database_sha256=hashlib.sha256(research.read_bytes()).hexdigest()
    )
    plan = prepare_plan(policy, AT)
    assert "2025-06-05" not in plan.document.to_dict()["dates"]
    assert any(
        s["reason"] == "original_already_retained" for s in plan.document.to_dict()["skipped"]
    )
    before = snapshot(Path(policy.values["archive_root"]).parent / "research")
    report = run_backfill(policy, plan, clock=lambda: AT)
    assert report.to_dict()["network_requests"] == 0 and before == snapshot(research.parent)
    assert not Path(policy.values["archive_root"]).exists()


def test_known_budget_and_daily_reservation(policy: BackfillPolicy, tmp_path: Path) -> None:
    ledger = tmp_path / "other-account-usage.sqlite3"
    gate = RequestGate(ledger, clock=lambda: AT.timestamp(), sleep=lambda _: None)
    gate.acquire()
    policy = changed(policy, daily_limit=1, known_request_ledgers=[str(ledger)])
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with (
        response_client(calls) as client,
        pytest.raises(ValueError, match="known_account_budget_exhausted"),
    ):
        execute(policy, plan, client, allow_network=True, operator_confirmed=True)
    assert not calls
    with pytest.raises(ValueError, match="daily_scheduler_budget_not_reserved"):
        prepare_plan(changed(policy, reserved_daily_requests=1), AT)


def test_existing_scheduler_lock_and_window_protected(
    policy: BackfillPolicy, config: SchedulerConfig
) -> None:
    plan = prepare_plan(policy, AT)
    with _lock(config, ".run.lock"), pytest.raises(ValueError, match="scheduler_already_running"):
        execute(policy, plan)
    with pytest.raises(ValueError, match="daily_scheduler_window"):
        run_backfill(
            policy,
            plan,
            execute=True,
            allow_synthetic_fixture=True,
            clock=lambda: AT.replace(hour=10),
        )  # 19:00 Seoul


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "v99"),
        ("daily_limit", 10000),
        ("reserved_daily_requests", 0),
        ("max_requests", 0),
    ],
)
def test_contract_bounds(policy: BackfillPolicy, field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        changed(policy, **{field: value})


def test_wrong_owners_and_calendar_fail_closed(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    forged = BackfillPlan(FrozenJSON.freeze(plan.document.to_dict() | {"policy_pin": "0" * 64}))
    with pytest.raises(ValueError, match="plan_policy_mismatch"):
        inspect_backfill(policy, forged, AT)
    cal = policy.values["calendars"]
    cal[0]["normalized_pin"] = "0" * 64
    with pytest.raises(ValueError, match="calendar_pin_mismatch"):
        prepare_plan(changed(policy, calendars=cal), AT)
    with pytest.raises(ValueError, match="backfill_service_approval_expired"):
        prepare_plan(
            changed(policy, valid_until=(AT + timedelta(days=30)).isoformat()),
            AT + timedelta(days=25),
        )


def test_private_pins_and_cli_preview(
    policy: BackfillPolicy, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = tmp_path / "package"
    package.mkdir(mode=0o700)
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o700)
    policy_path = package / "policy.json"
    private(policy_path, policy.document.payload_json)
    policy_pin = trusted / "policy.pin"
    private(policy_pin, policy.document.identifier)
    plan = prepare_plan(policy, AT)
    plan_path = package / "plan.json"
    private(plan_path, plan.document.payload_json)
    plan_pin = trusted / "plan.pin"
    private(plan_pin, plan.document.identifier)
    assert load_policy(policy_path, policy.document.identifier) == policy
    assert load_plan(plan_path, plan.document.identifier) == plan
    with patch("httpx.Client.send", side_effect=AssertionError("network forbidden")):
        assert (
            main(
                [
                    "krx-backfill",
                    "inspect",
                    "--policy",
                    str(policy_path),
                    "--policy-pin-file",
                    str(policy_pin),
                    "--plan",
                    str(plan_path),
                    "--plan-pin-file",
                    str(plan_pin),
                ]
            )
            == 0
        )
    output = capsys.readouterr().out
    assert policy.document.identifier not in output and plan.document.identifier not in output
    assert json.loads(output)["network_requests"] == 0
    with pytest.raises(ValueError, match="policy_pin_mismatch"):
        load_policy(policy_path, "0" * 64)


def test_coverage_bound_stops_without_retry(policy: BackfillPolicy) -> None:
    policy = changed(policy, min_rows=3)
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with response_client(calls) as client:
        result = execute(
            policy, plan, client, allow_network=True, operator_confirmed=True
        ).to_dict()
    assert len(calls) == 1 and result["http_requests"] == 1
    assert result["stop_reason"] == "market_coverage_below_reviewed_bound"
    assert result["new_observations"] == 0


def test_compressed_response_and_size_limit(policy: BackfillPolicy) -> None:
    calls: list[str] = []
    plan = prepare_plan(policy, AT)
    with response_client(calls, zipped=True) as client:
        report = execute(
            policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1
        ).to_dict()
    assert report["successful_dates"] == 1
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"x" * (2 * 1024**2 + 1))
        ),
        trust_env=False,
    ) as client:
        report = execute(
            policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1
        ).to_dict()
    assert report["results"][0]["outcome"] == "failed" and report["http_requests"] == 1
    assert report["new_observations"] == 0


def test_changed_research_source_does_not_repin(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    original = policy.values["research_database_sha256"]
    dbpath = Path(policy.values["research_database"])
    with closing(sqlite3.connect(dbpath)) as db, db:
        db.execute("CREATE TABLE unauthorized_change (value TEXT)")
    with pytest.raises(ValueError, match="research_inventory_pin_mismatch"):
        execute(policy, plan)
    assert policy.values["research_database_sha256"] == original


def test_live_policy_rejects_mock_and_backdated_clock(
    policy: BackfillPolicy, config: SchedulerConfig
) -> None:
    original_root = config.path("policy_root")
    value = json.loads((original_root / "recurring-policy.json").read_text())
    value["transport"] = "provider_https"
    value["document_origin"] = "operator_reviewed_source_copies"
    source = FrozenJSON.freeze(value)
    private(original_root / "recurring-policy.json", source.payload_json)
    cfg = FrozenJSON.freeze(config.document.to_dict() | {"policy_pin": source.identifier})
    private(Path(policy.values["scheduler_config"]), cfg.payload_json)
    policy = changed(policy, scheduler_config_pin=cfg.identifier)
    plan = prepare_plan(policy, AT)
    with pytest.raises(ValueError, match="current_local_utc_clock"):
        run_backfill(
            policy,
            plan,
            execute=True,
            allow_network=True,
            operator_confirmed=True,
            clock=lambda: AT,
        )
    assert not Path(policy.values["archive_root"]).exists()


def test_no_network_permission_and_immutable_intents(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    report = execute(policy, plan).to_dict()
    assert report["requests_reserved"] == 0 and report["http_requests"] == 0
    assert report["stop_reason"] == "explicit_backfill_network_authorization_required"
    calls: list[str] = []
    with response_client(calls) as client:
        execute(policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1)
    with closing(sqlite3.connect(Path(policy.values["archive_root"]) / DB)) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable backfill evidence"):
            db.execute("DELETE FROM backfill_intents")
        with pytest.raises(sqlite3.IntegrityError, match="immutable original"):
            db.execute("UPDATE raw_pages SET body=x'7b7d'")


def test_cli_policy_review_and_plan_pins_are_exclusive(
    policy: BackfillPolicy, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import datetime

    class Now(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Self:
            return cls.fromtimestamp(AT.timestamp(), tz)

    package = tmp_path / "review-package"
    package.mkdir(mode=0o700)
    pins = tmp_path / "review-trusted"
    pins.mkdir(mode=0o700)
    policy_path = package / "policy.json"
    private(policy_path, policy.document.payload_json)
    pin_path = pins / "policy.pin"
    args = [
        "krx-backfill",
        "pin-policy",
        "--policy",
        str(policy_path),
        "--policy-pin-file",
        str(pin_path),
    ]
    with (
        patch("donghak_stock_vision.krx_backfill_cli.datetime", Now),
        patch("httpx.Client.send", side_effect=AssertionError("network forbidden")),
    ):
        assert main(args) == 2
        assert not pin_path.exists()
        assert main(args + ["--confirm-operator-authorization"]) == 0
        assert pin_path.read_text().strip() == policy.document.identifier
        before = pin_path.read_bytes()
        assert main(args + ["--confirm-operator-authorization"]) == 2
        assert pin_path.read_bytes() == before
        plan_path, plan_pin = package / "plan.json", pins / "plan.pin"
        assert (
            main(
                [
                    "krx-backfill",
                    "prepare-plan",
                    "--policy",
                    str(policy_path),
                    "--policy-pin-file",
                    str(pin_path),
                    "--plan",
                    str(plan_path),
                    "--plan-pin-file",
                    str(plan_pin),
                ]
            )
            == 0
        )
        assert load_plan(plan_path, plan_pin.read_text().strip()) == prepare_plan(policy, AT)
    assert policy.document.identifier not in capsys.readouterr().out


def test_cross_response_substitution_and_holiday_plan_rejected(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    with pytest.raises(ValueError, match="both_selected_and_skipped"):
        BackfillPlan(FrozenJSON.freeze(plan.document.to_dict() | {"dates": ["2025-06-06"]}))
    bad = BackfillPlan(
        FrozenJSON.freeze(plan.document.to_dict() | {"dates": ["2025-06-06"], "skipped": []})
    )
    with pytest.raises(ValueError, match="calendar_or_date_mismatch"):
        inspect_backfill(policy, bad, AT)
    calls: list[str] = []
    with response_client(calls) as client:
        execute(policy, plan, client, allow_network=True, operator_confirmed=True)
    with closing(sqlite3.connect(Path(policy.values["archive_root"]) / DB)) as db, db:
        db.execute("DROP TRIGGER backfill_responses_no_UPDATE")
        db.execute(
            "UPDATE backfill_responses SET raw_id=(SELECT raw_id FROM backfill_responses "
            "WHERE day='2025-06-04') WHERE day='2025-06-05'"
        )
    with pytest.raises(ValueError, match="raw_source_ownership_mismatch"):
        inspect_backfill(policy, plan, AT)


def test_offline_verification_survives_policy_expiry(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with response_client(calls) as client:
        execute(policy, plan, client, allow_network=True, operator_confirmed=True, max_dates=1)
    assert len(inspect_backfill(policy, plan, AT + timedelta(days=90)).to_dict()["results"]) == 1
    with pytest.raises(ValueError, match="expired_or_not_yet_valid"):
        run_backfill(policy, plan, execute=True, clock=lambda: AT + timedelta(days=90))


def test_archive_missing_evidence_table_is_not_recreated(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    execute(policy, plan)
    with closing(sqlite3.connect(Path(policy.values["archive_root"]) / DB)) as db, db:
        db.execute("DROP TABLE backfill_responses")
    with pytest.raises(ValueError, match="incomplete_backfill_archive_rejected"):
        execute(policy, plan)


def test_explicit_date_failure_continuation_never_retries(policy: BackfillPolicy) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        day = request.url.params["basDd"]
        calls.append(day)
        value = date(int(day[:4]), int(day[4:6]), int(day[6:]))
        return httpx.Response(
            200,
            json={
                "OutBlock_1": []
                if day == "20250605"
                else [row("005930", value), row("000660", value)]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        result = execute(
            policy,
            plan,
            client,
            allow_network=True,
            operator_confirmed=True,
            continue_date_failures=True,
            max_dates=4,
        ).to_dict()
        assert result["http_requests"] == 4 and result["successful_dates"] == 3
        assert result["new_observations"] == 6 and result["results"][0]["outcome"] == "no_data"
        replay = execute(
            policy,
            plan,
            client,
            allow_network=True,
            operator_confirmed=True,
            continue_date_failures=True,
            max_dates=4,
        ).to_dict()
    assert replay["http_requests"] == 0 and len(calls) == 4


@pytest.mark.parametrize("status", [403, 429])
def test_date_continuation_cannot_ignore_account_errors(
    policy: BackfillPolicy, status: int
) -> None:
    plan = prepare_plan(policy, AT)
    calls: list[str] = []
    with response_client(calls, status=status) as client:
        result = execute(
            policy,
            plan,
            client,
            allow_network=True,
            operator_confirmed=True,
            continue_date_failures=True,
            max_dates=4,
        ).to_dict()
    assert result["http_requests"] == 1 and len(calls) == 1
    assert result["stop_reason"] == "http_or_transport_failure"
