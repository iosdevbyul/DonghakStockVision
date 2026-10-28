"""Full-market recurrence: synthetic documents/MockTransport only, never KRX calls."""

import gzip
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_forward_capture import Clock
from tests.integration.test_krx_forward_authorization import KEY
from tests.integration.test_krx_operator_policy import package
from tests.integration.test_market_collection import row
from tests.integration.test_operational_capture import snapshot

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan, FullMarketScope
from donghak_stock_vision.storage.forward_capture import (
    DB,
    capture_status,
    run_capture,
    verify_capture,
    verify_operator_capture_authorization,
)
from donghak_stock_vision.storage.forward_capture import recurring_control as control
from donghak_stock_vision.storage.forward_capture import recurring_preview as preview
from donghak_stock_vision.storage.krx_authorization import verify_operator_authorization
from donghak_stock_vision.storage.krx_recurring import (
    POLICY,
    verify_recurring_authorization,
)
from donghak_stock_vision.storage.operational_capture import ingest_capture
from donghak_stock_vision.storage.operational_market import OperationalMarketStore, StorageLimits

START = datetime(2025, 7, 5, tzinfo=UTC)
AT = datetime(2025, 7, 8, tzinfo=UTC)


def fixture(tmp: Path, *, limit: int = 2) -> tuple[ForwardCapturePlan, FrozenJSON]:
    old, ref, owner = package(tmp)
    d = owner.to_dict()
    scope = FullMarketScope(
        START, START + timedelta(days=9), tuple(date(2025, 7, i) for i in (7, 8, 9, 10, 11))
    )
    d.pop("captures")
    d.update(
        version=POLICY,
        scope_acknowledgement="explicit_full_market_recurring_read_only",
        valid_from=scope.valid_from.isoformat(),
        valid_until=scope.valid_until.isoformat(),
        reviewed_at=START.isoformat(),
        approved_through="2025-07-31",
        full_market_scope=scope.to_dict(),
        daily_limit=limit,
        verified_service_daily_limit=10000,
        max_response_bytes=2 * 1024**2,
        max_seconds=30,
        interval_seconds=1,
    )
    owner = FrozenJSON.freeze(d)
    (Path(ref.to_dict()["root"]) / "recurring-policy.json").write_text(owner.payload_json)
    ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": owner.identifier})
    plan = replace(
        old,
        ticker="",
        day=date(2025, 7, 7),
        partition="forward_market_observation",
        split=None,
        split_pin=None,
        observation_scope=None,
        full_market_scope=scope,
        authorization_reference=owner.identifier,
        max_response_bytes=2 * 1024**2,
        daily_limit=limit,
    )
    return plan, ref


def execute(
    root: Path,
    plan: ForwardCapturePlan,
    ref: FrozenJSON,
    *,
    attempt: str = "a",
    at: datetime = AT,
    count: int = 940,
    status: int = 200,
    pin: str | None = None,
    counter: list[int] | None = None,
    zipped: bool = False,
) -> FrozenJSON:
    def respond(_: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(1)
        body = json.dumps(
            {"OutBlock_1": [row(f"{i:06d}", plan.day) for i in range(count)]}
        ).encode()
        if zipped:
            return httpx.Response(
                status,
                headers={"Content-Encoding": "gzip"},
                stream=httpx.ByteStream(gzip.compress(body, mtime=0)),
            )
        return httpx.Response(status, content=body)

    clock = Clock(at)
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        return run_capture(
            root,
            plan,
            attempt,
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_policy=POLICY,
            operator_confirmed=True,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
            trusted_resume_pin=pin,
        )


def test_v3_roundtrip_and_durable_policy(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    doc = plan.document.to_dict()
    assert doc["version"] == "forward_capture_plan_v3"
    assert "ticker" not in doc and "split" not in doc and "partition" not in doc
    assert ForwardCapturePlan.from_dict(doc) == plan
    assert verify_recurring_authorization(plan, "a", ref, None, AT)
    next_plan = replace(plan, day=date(2025, 7, 8))
    assert verify_recurring_authorization(
        next_plan, "another-day", ref, None, AT + timedelta(days=1)
    )
    assert next_plan.authorization_reference == plan.authorization_reference
    with pytest.raises(ValueError):
        verify_operator_authorization(plan, "a", ref, None, AT)

    # A new capture may read an explicitly approved recent completed session at
    # policy start; the policy must precede capture, not the bar's trading date.
    assert plan.full_market_scope is not None
    scope = replace(
        plan.full_market_scope,
        permitted_days=(date(2025, 7, 4), *plan.full_market_scope.permitted_days),
    )
    path = Path(ref.to_dict()["root"]) / "recurring-policy.json"
    d = FrozenJSON(path.read_text()).to_dict()
    d["full_market_scope"] = scope.to_dict()
    policy = FrozenJSON.freeze(d)
    path.write_text(policy.payload_json)
    recent = replace(
        plan,
        day=date(2025, 7, 4),
        full_market_scope=scope,
        authorization_reference=policy.identifier,
    )
    recent_ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": policy.identifier})
    assert verify_recurring_authorization(recent, "first", recent_ref, None, START)
    with pytest.raises(ValueError, match="not_yet_valid"):
        verify_recurring_authorization(
            recent, "first", recent_ref, None, START - timedelta(seconds=1)
        )


@pytest.mark.parametrize("zipped", [False, True])
def test_full_market_source_to_storage(tmp_path: Path, zipped: bool) -> None:
    plan, ref = fixture(tmp_path)
    root = tmp_path / "capture"
    counter: list[int] = []
    with patch("socket.socket.connect", side_effect=AssertionError("no external requests")):
        owner = execute(root, plan, ref, counter=counter, zipped=zipped)
        before = snapshot(root)
        assert verify_capture(root, plan, "a", owner.identifier) == owner
        assert (
            verify_operator_capture_authorization(root, plan, "a", owner.identifier, ref, AT)
            == "authorized_provider_mock_fixture"
        )
        assert capture_status(root).to_dict()["version"] == "forward_capture_status_v2"
        operational = tmp_path / "operational"
        OperationalMarketStore.initialize(operational, StorageLimits(min_free_bytes=0))

        def import_source() -> FrozenJSON:
            return ingest_capture(
                operational,
                root,
                plan,
                plan.document.identifier,
                "a",
                owner.identifier,
                authorization=ref,
                authorization_policy=POLICY,
            )

        result = import_source()
        assert result.to_dict()["observation_count"] == 940
        assert not any(result.to_dict()["eligibility"].values())
        assert import_source() == result
        assert snapshot(root) == before
        with closing(OperationalMarketStore(operational)) as store:
            assert store.validate().to_dict()["valid"]
            assert store.status().to_dict()["observation_count"] == 940
        with closing(sqlite3.connect(root / DB)) as c:
            assert c.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0
    assert counter == [1]
    assert owner.to_dict()["ingestion"] == []
    assert owner.to_dict()["version"] == "forward_capture_manifest_v2"
    assert owner.to_dict()["normalization"] == "operational_store_only"
    assert len(owner.to_dict()["responses"][0]["parsed_bars"]) == 940


@pytest.mark.parametrize(
    "field,value",
    [
        ("service_id", "stk_isu_base_info"),
        ("endpoint", "https://example.invalid/"),
        ("method", "POST"),
        ("daily_limit", 10001),
        ("verified_service_daily_limit", 10001),
        ("scope_acknowledgement", "old-pilot"),
        ("approved_through", "2025-07-07"),
        ("uses", dict(private_capture=True, forward_signal=True, paper=False)),
    ],
)
def test_semantically_invalid_policy_rejected(tmp_path: Path, field: str, value: Any) -> None:
    plan, ref = fixture(tmp_path)
    path = Path(ref.to_dict()["root"]) / "recurring-policy.json"
    d = FrozenJSON(path.read_text()).to_dict()
    d[field] = value
    changed = FrozenJSON.freeze(d)
    path.write_text(changed.payload_json)
    newref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": changed.identifier})
    with pytest.raises(ValueError):
        verify_recurring_authorization(
            replace(plan, authorization_reference=changed.identifier), "a", newref, None, AT
        )


@pytest.mark.parametrize("at", [START, START + timedelta(days=10), AT + timedelta(days=7)])
def test_incomplete_expired_or_stale_date_not_ready(tmp_path: Path, at: datetime) -> None:
    plan, ref = fixture(tmp_path)
    doc = preview(
        plan, "a", ref, None, at, True, credential=KEY, capture_root=tmp_path / "capture"
    ).to_dict()
    assert doc["blockers"] and not doc["ready_for_explicit_read_only_capture"]
    assert not (tmp_path / "capture").exists()


def test_preview_read_only_quota_duplicate_and_resume(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    root = tmp_path / "capture"
    calls: list[int] = []

    def inspect() -> dict[str, Any]:
        return preview(plan, "a", ref, None, AT, True, credential=KEY, capture_root=root).to_dict()

    assert inspect()["remaining_local_allowance"] == 2 and not root.exists()
    first = execute(root, plan, ref, counter=calls)
    before = snapshot(root)
    doc = inspect()
    assert snapshot(root) == before
    assert doc["duplicate"] and doc["remaining_local_allowance"] == 1
    assert "duplicate_collection_date" in doc["blockers"]
    with pytest.raises(ValueError, match="duplicate_collection_date"):
        execute(root, plan, ref, attempt="different", counter=calls)
    assert execute(root, plan, ref, pin=first.identifier, counter=calls) == first
    assert calls == [1]
    control(root, plan, ref, AT, True)
    assert execute(root, plan, ref, pin=first.identifier, counter=calls) == first
    assert calls == [1]
    assert inspect()["paused"]
    assert "recurring_collection_paused" in inspect()["blockers"]
    control(root, plan, ref, AT, False)
    assert not inspect()["paused"]
    assert verify_capture(root, plan, "a", first.identifier) == first


def test_daily_limit_no_second_date_request(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path, limit=1)
    root = tmp_path / "capture"
    # Both allowed dates are completed and within seven days at this one local day.
    at = AT + timedelta(days=1)
    calls: list[int] = []
    original = execute(root, plan, ref, at=at, counter=calls)
    assert execute(root, plan, ref, at=at, counter=calls, pin=original.identifier) == original
    second = replace(plan, day=date(2025, 7, 8))
    d = preview(second, "b", ref, None, at, True, credential=KEY, capture_root=root).to_dict()
    assert d["remaining_local_allowance"] == 0
    with pytest.raises(ValueError, match="local_daily_budget_exhausted"):
        execute(root, second, ref, attempt="b", at=at, counter=calls)
    assert calls == [1]


@pytest.mark.parametrize("damage", ["pin", "terms", "credential", "old_policy"])
def test_authorization_damage_no_call(tmp_path: Path, damage: str) -> None:
    plan, ref = fixture(tmp_path)
    root = tmp_path / "capture"
    calls: list[int] = []
    if damage == "pin":
        ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": "0" * 64})
    elif damage == "terms":
        (Path(ref.to_dict()["root"]) / "terms.bin").write_bytes(b"altered")
    elif damage == "credential":
        d = FrozenJSON(
            (Path(ref.to_dict()["root"]) / "recurring-policy.json").read_text()
        ).to_dict()
        d["credential_sha256"] = hashlib.sha256(b"different").hexdigest()
        p = FrozenJSON.freeze(d)
        (Path(ref.to_dict()["root"]) / "recurring-policy.json").write_text(p.payload_json)
        ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": p.identifier})
        plan = replace(plan, authorization_reference=p.identifier)
    else:
        ref = FrozenJSON.freeze(
            ref.to_dict()
            | {
                "grant_pin": FrozenJSON(
                    (Path(ref.to_dict()["root"]) / "operator-policy.json").read_text()
                ).identifier
            }
        )
    with pytest.raises(ValueError):
        execute(root, plan, ref, counter=calls)
    assert not calls and not root.exists()


@pytest.mark.parametrize("change", ["market", "ticker", "partition", "date", "budget", "scope"])
def test_invalid_plan_and_legacy_escalation(tmp_path: Path, change: str) -> None:
    plan, _ = fixture(tmp_path)
    values: dict[str, dict[str, Any]] = dict(
        market=dict(market="KOSDAQ"),
        ticker=dict(ticker="005930"),
        partition=dict(partition="final_test"),
        date=dict(day=date(2025, 7, 4)),
        budget=dict(max_requests=2),
        scope=dict(observation_scope=object()),
    )
    with pytest.raises(ValueError):
        replace(plan, **values[change])


def test_http_error_consumes_attempt_without_automatic_retry(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    root = tmp_path / "capture"
    calls: list[int] = []
    owner = execute(root, plan, ref, status=429, counter=calls)
    assert owner.to_dict()["stop_reason"] == "HTTP_429"
    with pytest.raises(ValueError, match="duplicate_collection_date"):
        execute(root, plan, ref, attempt="retry", counter=calls)
    assert calls == [1]


def test_no_network_opt_in_and_pause_block_provider(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    root = tmp_path / "capture"
    control(root, plan, ref, AT, True)
    with pytest.raises(ValueError, match="paused"):
        execute(root, plan, ref)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("request"))) as client:
        with pytest.raises(ValueError, match="explicit_network_permission_required"):
            run_capture(
                root,
                plan,
                "a",
                client,
                api_key=KEY,
                authorization=ref,
                authorization_policy=POLICY,
                operator_confirmed=True,
                clock=lambda: AT,
            )


def test_cli_preview_and_pause_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, ref = fixture(tmp_path)
    path = tmp_path / "plan.json"
    path.write_text(plan.document.payload_json)
    monkeypatch.setenv("KRX_API_KEY", KEY)
    args = [
        "forward-capture",
        "dry-run",
        "--root",
        str(tmp_path / "capture"),
        "--plan",
        str(path),
        "--plan-pin",
        plan.document.identifier,
        "--authorization-root",
        ref.to_dict()["root"],
        "--authorization-pin",
        ref.to_dict()["grant_pin"],
        "--authorization-policy",
        POLICY,
    ]

    class Fixed(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Fixed":
            return cls.fromtimestamp(AT.timestamp(), tz)

    with (
        patch("donghak_stock_vision.forward_capture_cli.datetime", Fixed),
        patch("httpx.Client.send", side_effect=AssertionError("network")),
    ):
        assert main(args) == 0
        d = json.loads(capsys.readouterr().out)
        assert d["ready_for_explicit_read_only_capture"] and d["network_requests"] == 0
        assert "credential_sha256" not in json.dumps(d) and "grant_pin" not in json.dumps(d)
        assert not (tmp_path / "capture").exists()
        args[1] = "recurring-pause"
        assert main(args) == 0
        assert json.loads(capsys.readouterr().out)["paused"]
        args[1] = "recurring-resume"
        assert main(args) == 0
        assert not json.loads(capsys.readouterr().out)["paused"]


def test_more_than_32_daily_captures_one_policy(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path, limit=1)
    days = tuple(
        START.date() + timedelta(days=i)
        for i in range(2, 52)
        if (START.date() + timedelta(days=i)).weekday() < 5
    )[:33]
    scope = FullMarketScope(START, START + timedelta(days=60), days)
    path = Path(ref.to_dict()["root"]) / "recurring-policy.json"
    d = FrozenJSON(path.read_text()).to_dict()
    d.update(
        full_market_scope=scope.to_dict(),
        valid_until=scope.valid_until.isoformat(),
        approved_through="2025-10-01",
    )
    policy = FrozenJSON.freeze(d)
    path.write_text(policy.payload_json)
    ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": policy.identifier})
    plan = replace(plan, full_market_scope=scope, authorization_reference=policy.identifier)
    for day in days:
        execute(
            tmp_path / "capture",
            replace(plan, day=day),
            ref,
            count=1,
            at=datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1),
        )
    status = capture_status(tmp_path / "capture").to_dict()
    assert status["version"] == "forward_capture_status_v2" and len(status["captures"]) == 33


def test_concurrent_duplicate_scheduling_only_one_mock_request(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    calls: list[int] = []

    def run(attempt: str) -> bool:
        try:
            execute(tmp_path / "capture", plan, ref, attempt=attempt, count=1, counter=calls)
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(run, ("one", "two"))) == 1
    assert calls == [1]


def test_tampered_future_request_slot_blocks_without_sleep(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    execute(tmp_path / "capture", plan, ref, count=1)
    with closing(sqlite3.connect(tmp_path / "capture" / DB)) as c:
        c.execute("UPDATE request_budget SET last_slot=?", ((AT + timedelta(days=30)).timestamp(),))
        c.commit()
    doc = preview(
        plan, "a", ref, None, AT, True, credential=KEY, capture_root=tmp_path / "capture"
    ).to_dict()
    assert "invalid_or_unbounded_request_slot" in doc["blockers"]


def test_legacy_policy_cannot_request_v3(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("request"))) as client:
        with pytest.raises(ValueError, match="distinct_recurring_policy"):
            run_capture(
                tmp_path / "capture",
                plan,
                "a",
                client,
                api_key=KEY,
                allow_network=True,
                authorization=ref,
                authorization_policy="operator_read_only_pilot_v1",
                operator_confirmed=True,
                clock=lambda: AT,
            )
    assert not (tmp_path / "capture").exists()

    from tests.integration.test_krx_operator_policy import record

    old_policy = FrozenJSON((Path(ref.to_dict()["root"]) / "operator-policy.json").read_text())
    original = old_policy.to_dict()
    old_plan = replace(
        ForwardCapturePlan.from_dict(original["captures"][0]["plan"]),
        authorization_reference=old_policy.identifier,
        terms_reference=original["terms"]["sha256"],
    )
    old_ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": old_policy.identifier})
    record(tmp_path / "capture", old_plan, old_ref)
    before = snapshot(tmp_path / "capture")
    doc = preview(
        plan, "a", ref, None, AT, True, credential=KEY, capture_root=tmp_path / "capture"
    ).to_dict()
    assert "recurring_archive_cannot_mix_legacy_pilot" in doc["blockers"]
    with pytest.raises(ValueError, match="cannot_mix_legacy"):
        control(tmp_path / "capture", plan, ref, AT, True)
    assert snapshot(tmp_path / "capture") == before


def test_wrong_credential_and_private_archive_preview(tmp_path: Path) -> None:
    plan, ref = fixture(tmp_path)
    root = tmp_path / "capture"
    doc = preview(plan, "a", ref, None, AT, True, credential="wrong", capture_root=root).to_dict()
    assert (
        not doc["ready_for_explicit_read_only_capture"] and not doc["credential_binding_verified"]
    )
    root.mkdir(mode=0o755)
    doc = preview(plan, "a", ref, None, AT, True, credential=KEY, capture_root=root).to_dict()
    assert "private_recurring_capture_root_required" in doc["blockers"]
