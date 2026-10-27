"""Offline end-to-end forward custody pilot. No real provider or authentic time claims."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import HistoricalReadScope
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.research_split import ResearchDateRange
from donghak_stock_vision.forward_capture_cli import fixture_transport, offline_plan
from donghak_stock_vision.storage.capture_evidence_sink import (
    export_capture_receipt,
    verify_exported_capture_receipt,
)
from donghak_stock_vision.storage.forward_capture import (
    DB,
    _project,
    assess_capture_pit,
    capture_status,
    identity,
    readback,
    run_capture,
    verify_capture,
)
from donghak_stock_vision.storage.historical_pit import (
    availability_manifest,
    historical_pit_execution_gate,
)
from donghak_stock_vision.trading.domain import Currency, Exchange, Instrument

NOW = datetime(2025, 7, 2, tzinfo=UTC)


class Clock:
    def __init__(self, at: datetime = NOW) -> None:
        self.at, self.elapsed = at, 0.0

    def now(self) -> datetime:
        return self.at + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds


def execute(
    root: Path,
    plan: ForwardCapturePlan | None = None,
    attempt: str = "a",
    *,
    transport: httpx.MockTransport | None = None,
    clock: Clock | None = None,
    **kwargs: Any,
) -> FrozenJSON:
    plan = plan or offline_plan()
    clock = clock or Clock()
    with httpx.Client(transport=transport or fixture_transport(plan), trust_env=False) as client:
        return run_capture(
            root,
            plan,
            attempt,
            client,
            clock=clock.now,
            monotonic=lambda: clock.elapsed,
            sleep=clock.sleep,
            **kwargs,
        )


def test_offline_end_to_end_and_custody(tmp_path: Path) -> None:
    plan = offline_plan()
    owner = execute(tmp_path)
    doc = owner.to_dict()
    assert doc["ingestion"] == [["success", 1, None]]
    response = doc["responses"][0]
    assert response["metadata"]["http_status"] == 200
    assert response["metadata"]["headers"]["date"] == "Wed, 02 Jul 2025 00:00:00 GMT"
    assert response["receipt"]["collectedAt"] == NOW.isoformat()
    assert not doc["pit_qualified"] and doc["decision_safe_at"] is None
    assert verify_capture(tmp_path, plan, "a", owner.identifier) == owner
    body = readback(tmp_path, plan, "a", owner.identifier, 0)
    assert hashlib.sha256(body).hexdigest() == response["receipt"]["rawSha256"]
    with closing(sqlite3.connect(tmp_path / DB)) as connection:
        target = tmp_path / "receipts"
        target.mkdir()
        exported = export_capture_receipt(connection, response["raw_id"], target)
        verified = verify_exported_capture_receipt(connection, response["raw_id"], target)
        assert verified
        assert hashlib.sha256(exported.path.read_bytes()).hexdigest() == exported.sha256
    assert capture_status(tmp_path).to_dict()["captures"][0]["status"] == "committed_unverified"


def test_duplicate_attempt_is_no_http_and_requires_independent_pin(tmp_path: Path) -> None:
    original = execute(tmp_path)

    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("completed capture executed HTTP")

    with pytest.raises(ValueError, match="trusted_resume_pin"):
        execute(tmp_path, transport=httpx.MockTransport(forbidden))
    assert (
        execute(
            tmp_path,
            transport=httpx.MockTransport(forbidden),
            trusted_resume_pin=original.identifier,
        )
        == original
    )
    with closing(sqlite3.connect(tmp_path / DB)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1


def test_identical_and_revised_observations_are_distinct(tmp_path: Path) -> None:
    plan = offline_plan()
    first = execute(tmp_path)
    second = execute(tmp_path, attempt="b", clock=Clock(NOW + timedelta(seconds=2)))

    def revised(request: httpx.Request) -> httpx.Response:
        response = fixture_transport(plan).handle_request(request)
        payload = json.loads(response.content)
        payload["OutBlock_1"][0]["TDD_CLSPRC"] = "106"
        return httpx.Response(200, json=payload)

    third = execute(
        tmp_path,
        attempt="c",
        clock=Clock(NOW + timedelta(seconds=4)),
        transport=httpx.MockTransport(revised),
    )
    a, b, c = [o.to_dict()["responses"][0] for o in (first, second, third)]
    assert a["raw_id"] != b["raw_id"] != c["raw_id"]
    assert a["receipt"]["rawSha256"] == b["receipt"]["rawSha256"] != c["receipt"]["rawSha256"]
    assert third.to_dict()["previous_captures"][0]["pin"] == first.identifier
    for attempt, owner in (("a", first), ("b", second), ("c", third)):
        assert verify_capture(tmp_path, plan, attempt, owner.identifier) == owner


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_http_failure_retained_no_unapproved_retry(tmp_path: Path, status: int) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, content=b"denied")

    result = execute(tmp_path, transport=httpx.MockTransport(handler)).to_dict()
    assert len(calls) == 1 and not result["ingestion"]
    assert result["stop_reason"] == f"HTTP_{status}"
    assert result["responses"][0]["metadata"]["http_status"] == status
    assert result["responses"][0]["body_bytes"] == 6


def test_timeout_bounded_retry(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("secret must not enter evidence")

    plan = replace(offline_plan(), max_requests=3)
    result = execute(tmp_path, plan, transport=httpx.MockTransport(handler)).to_dict()
    assert len(calls) == 3 and len(result["responses"]) == 3
    assert "secret" not in json.dumps(result)
    assert result["stop_reason"] == "network_transport_error"


def test_rate_limit_recovery_and_retry_after_budget(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return (
            httpx.Response(429, content=b"busy", headers={"Retry-After": "2"})
            if len(calls) == 1
            else fixture_transport(offline_plan()).handle_request(request)
        )

    result = execute(
        tmp_path, replace(offline_plan(), max_requests=2), transport=httpx.MockTransport(handler)
    ).to_dict()
    assert len(calls) == 2 and result["ingestion"] == [["success", 1, None]]
    assert [r["metadata"]["http_status"] for r in result["responses"]] == [429, 200]
    second = tmp_path / "blocked"
    result = execute(
        second,
        replace(offline_plan(), max_requests=3),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, headers={"Retry-After": "300"})
        ),
    ).to_dict()
    assert len(result["responses"]) == 1 and result["stop_reason"] == "retry_budget_exhausted"


def test_oversized_response_is_explicit_partial(tmp_path: Path) -> None:
    plan = replace(offline_plan(), max_response_bytes=16)
    result = execute(tmp_path, plan).to_dict()
    assert result["stop_reason"] == "response_byte_budget"
    assert result["responses"][0]["body_bytes"] == 16
    assert result["responses"][0]["metadata"]["complete"] is False
    assert not result["ingestion"]


def test_cancellation_before_http(tmp_path: Path) -> None:
    result = execute(tmp_path, cancelled=lambda: True).to_dict()
    assert result["responses"] == [] and result["stop_reason"] == "capture_cancelled"
    assert result["ingestion"] == []


def test_cooperative_duration_limit(tmp_path: Path) -> None:
    clock = Clock()

    def slow(request: httpx.Request) -> httpx.Response:
        clock.sleep(31)
        return fixture_transport(offline_plan()).handle_request(request)

    result = execute(tmp_path, clock=clock, transport=httpx.MockTransport(slow)).to_dict()
    assert result["stop_reason"] == "capture_deadline_exhausted" and not result["ingestion"]


def test_clock_regression_not_safe(tmp_path: Path) -> None:
    times = iter([NOW, NOW, NOW, NOW - timedelta(seconds=1)])
    with httpx.Client(transport=fixture_transport(offline_plan())) as client:
        owner = run_capture(tmp_path, offline_plan(), "a", client, clock=lambda: next(times))
    assert owner.to_dict()["stop_reason"] == "capture_clock_regression"
    assert not owner.to_dict()["ingestion"]


@pytest.mark.parametrize("changes", [{"ticker": "000660"}, {"market": "KOSDAQ"}])
def test_wrong_instrument_or_market_retains_invalid_response(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    plan = replace(offline_plan(), **changes)
    with httpx.Client(transport=fixture_transport(offline_plan())) as client:
        owner = run_capture(tmp_path, plan, "a", client, clock=lambda: NOW)
    assert not owner.to_dict()["ingestion"]
    assert owner.to_dict()["responses"][0]["parse_error"] == "invalid_or_missing_scoped_bar"


@pytest.mark.parametrize("partition", ["final_test", "final_holdout", "oos"])
def test_forbidden_scope_before_disk_and_http(tmp_path: Path, partition: str) -> None:
    plan = offline_plan()
    object.__setattr__(plan, "partition", partition)
    with pytest.raises(ValueError, match="forbidden_partition"):
        execute(tmp_path / "absent", plan)
    assert not (tmp_path / "absent").exists()


def test_final_test_date_not_authorized(tmp_path: Path) -> None:
    plan = offline_plan()
    assert plan.split is not None
    with pytest.raises(ValueError, match="outside_authorized_partition"):
        replace(plan, day=plan.split.final_test.start)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_requests", 4),
        ("max_response_bytes", 3000000),
        ("max_seconds", 121),
        ("interval_seconds", 0),
        ("daily_limit", 9501),
        ("mode", "kind"),
    ],
)
def test_invalid_budgets(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        replace(offline_plan(), **{field: value})


def test_credentials_authorization_and_network_permission_before_io(tmp_path: Path) -> None:
    plan = replace(offline_plan(), mode="authorized_provider")
    assert plan.blockers(False) == [
        "missing_krx_api_key",
        "service_authorization_required",
        "terms_and_frequency_review_required",
    ]
    with pytest.raises(ValueError, match="missing_krx_api_key"):
        execute(tmp_path / "absent", plan)
    assert not (tmp_path / "absent").exists()
    plan = replace(
        plan,
        authorization_reference="operator-reviewed-daily-service",
        terms_reference="private-use-review",
    )
    with pytest.raises(ValueError, match="explicit_network_permission_required"):
        execute(tmp_path / "absent", plan, api_key="never_print")


def test_offline_requires_mock_before_disk(tmp_path: Path) -> None:
    with (
        httpx.Client(trust_env=False) as client,
        pytest.raises(ValueError, match="offline_transport_required"),
    ):
        run_capture(tmp_path / "absent", offline_plan(), "a", client, clock=lambda: NOW)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "sql,args",
    [
        ("UPDATE raw_pages SET body=?", (b"altered",)),
        ("UPDATE raw_pages SET sha256=?", ("0" * 64,)),
        ("DELETE FROM raw_pages", ()),
        ("UPDATE bars SET payload=?", ("{}",)),
        ("DELETE FROM bar_sources", ()),
        ("UPDATE collection_runs SET rows=2", ()),
    ],
)
def test_tampering_detected(tmp_path: Path, sql: str, args: tuple[Any, ...]) -> None:
    owner = execute(tmp_path)
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute(sql, args)
    with pytest.raises((ValueError, KeyError)):
        verify_capture(tmp_path, offline_plan(), "a", owner.identifier)


def test_rehashed_forgery_is_not_new_trust_root(tmp_path: Path) -> None:
    owner = execute(tmp_path)
    value = owner.to_dict()
    value["decision_safe_at"] = NOW.isoformat()
    forged = FrozenJSON.freeze(value)
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute("DROP TRIGGER forward_manifests_no_UPDATE")
        connection.execute(
            "UPDATE forward_manifests SET document=?,pin=?",
            (forged.payload_json, forged.identifier),
        )
    with pytest.raises(ValueError, match="capture_pin_mismatch"):
        verify_capture(tmp_path, offline_plan(), "a", owner.identifier)
    with pytest.raises(ValueError, match="capture_semantic_mismatch"):
        verify_capture(tmp_path, offline_plan(), "a", forged.identifier)


def test_response_interruption_resume_without_network(tmp_path: Path) -> None:
    # Original raw+metadata persisted, before original ingestion starts.
    with patch(
        "donghak_stock_vision.storage.forward_capture.KRXMarketPipeline.collect",
        side_effect=KeyboardInterrupt,
    ):
        with pytest.raises(KeyboardInterrupt):
            execute(tmp_path)
    assert capture_status(tmp_path).to_dict()["captures"][0]["status"] == "interrupted"

    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("resume repeated provider request")

    owner = execute(tmp_path, transport=httpx.MockTransport(forbidden))
    assert owner.to_dict()["ingestion"] == [["success", 1, None]]


def test_atomic_manifest_interruption_resume(tmp_path: Path) -> None:
    original = _project
    count = 0

    def interrupt(*args: Any, **kwargs: Any) -> FrozenJSON:
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    with patch("donghak_stock_vision.storage.forward_capture._project", side_effect=interrupt):
        with pytest.raises(KeyboardInterrupt):
            execute(tmp_path)
    owner = execute(
        tmp_path, transport=httpx.MockTransport(lambda r: pytest.fail("HTTP on recovery"))
    )
    assert owner.to_dict()["ingestion"] == [["success", 1, None]]


def test_partial_raw_commit_detected(tmp_path: Path) -> None:
    owner = execute(tmp_path)
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute("DROP TRIGGER forward_responses_no_DELETE")
        connection.execute("DELETE FROM forward_responses")
    with pytest.raises(ValueError, match="interrupted_response_commit"):
        verify_capture(tmp_path, offline_plan(), "a", owner.identifier)


def test_missing_archive_and_unknown_request_outcome(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pilot_archive_missing"):
        verify_capture(tmp_path, offline_plan(), "a", "0" * 64)
    with patch(
        "donghak_stock_vision.providers.krx.KRXProvider.fetch_market", side_effect=KeyboardInterrupt
    ):
        with pytest.raises(KeyboardInterrupt):
            execute(tmp_path)
    with pytest.raises(ValueError, match="request_outcome_unknown"):
        execute(tmp_path)


def test_pit_assessment_preserves_original_blocked_gate(tmp_path: Path) -> None:
    plan = offline_plan()
    assert plan.split is not None and plan.split_pin is not None
    owner = execute(tmp_path)
    pin = hashlib.sha256((tmp_path / DB).read_bytes()).hexdigest()
    scope = HistoricalReadScope(
        Instrument(plan.ticker, Exchange.KOSPI, Currency.KRW),
        ResearchDateRange(plan.day, plan.day),
        plan.partition,
        plan.split,
        plan.split_pin,
        pin,
    )
    assessment = assess_capture_pit(
        tmp_path, plan, "a", owner.identifier, scope, scope.document.identifier
    )
    coverage = assessment.report.to_dict()["coverage"]
    assert coverage["decision_time_availability_established"] == 0
    gate = historical_pit_execution_gate(
        assessment,
        assessment.report.to_dict()["content_fingerprint"],
        tmp_path / DB,
        scope,
        scope.document.identifier,
        (),
        availability_manifest(()).identifier,
    )
    assert gate.to_dict()["status"] == "blocked"
    assert hashlib.sha256((tmp_path / DB).read_bytes()).hexdigest() == pin


def test_cli_offline_pilot_and_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["forward-capture", "offline-pilot", "--root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["manifest"]["origin"] == "offline_fixture"
    assert result["manifest"]["ingestion"] == [["success", 1, None]]
    assert main(["forward-capture", "status", "--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["captures"][0]["pin"] == result["capture_pin"]


def test_plan_roundtrip_and_identity() -> None:
    plan = offline_plan()
    assert ForwardCapturePlan.from_dict(plan.document.to_dict()) == plan
    assert identity(plan, "a") == identity(plan, "a") != identity(plan, "b")
    with pytest.raises(ValueError):
        identity(plan, "../escape")


def test_unchanged_source_files_during_readback_and_assessment(tmp_path: Path) -> None:
    owner = execute(tmp_path)
    before = hashlib.sha256((tmp_path / DB).read_bytes()).hexdigest()
    verify_capture(tmp_path, offline_plan(), "a", owner.identifier)
    readback(tmp_path, offline_plan(), "a", owner.identifier, 0)
    capture_status(tmp_path)
    assert hashlib.sha256((tmp_path / DB).read_bytes()).hexdigest() == before


def test_full_document_determinism(tmp_path: Path) -> None:
    one = execute(tmp_path / "one")
    two = execute(tmp_path / "two")
    assert one == two and one.identifier == two.identifier


def test_no_migration_of_existing_nonpilot_database(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute("CREATE TABLE protected(value TEXT)")
    original = (tmp_path / DB).read_bytes()
    with pytest.raises(ValueError, match="not_a_pilot_archive"):
        execute(tmp_path)
    assert (tmp_path / DB).read_bytes() == original


def test_existing_evidence_tables_are_append_only(tmp_path: Path) -> None:
    execute(tmp_path)
    with closing(sqlite3.connect(tmp_path / DB)) as connection:
        for table in ("forward_intents", "forward_responses", "forward_manifests"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(f"DELETE FROM {table}")


def test_pending_write_lock_prevents_concurrent_capture(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.forward_capture import _writer

    with _writer(tmp_path):
        with pytest.raises(ValueError, match="capture_already_running"):
            execute(tmp_path)


def test_naive_clock_before_io(tmp_path: Path) -> None:
    with httpx.Client(transport=fixture_transport(offline_plan())) as client:
        with pytest.raises(ValueError, match="naive_capture_clock"):
            run_capture(
                tmp_path / "absent", offline_plan(), "a", client, clock=lambda: datetime(2025, 7, 2)
            )
    assert not (tmp_path / "absent").exists()


def test_modified_ingested_price_rejected(tmp_path: Path) -> None:
    owner = execute(tmp_path)
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        payload = json.loads(connection.execute("SELECT payload FROM bars").fetchone()[0])
        payload["close"] = 106
        connection.execute("UPDATE bars SET payload=?", (json.dumps(payload),))
    with pytest.raises(ValueError, match="ingested_economics_mismatch"):
        verify_capture(tmp_path, offline_plan(), "a", owner.identifier)


def test_interrupted_save_resumes_without_duplicate_ingestion(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.sqlite import SQLiteStore

    original = SQLiteStore.save

    def save_then_interrupt(*args: Any, **kwargs: Any) -> int:
        original(*args, **kwargs)
        raise KeyboardInterrupt

    with patch.object(SQLiteStore, "save", save_then_interrupt), pytest.raises(KeyboardInterrupt):
        execute(tmp_path)
    owner = execute(
        tmp_path, transport=httpx.MockTransport(lambda r: pytest.fail("HTTP on resume"))
    )
    assert owner.to_dict()["ingestion"] == [["success", 1, None]]
    with closing(sqlite3.connect(tmp_path / DB)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == 1


def test_stream_disconnect_retains_only_explicit_partial_bytes(tmp_path: Path) -> None:
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self) -> Any:
            yield b"prefix"
            raise httpx.ReadError("sensitive provider failure")

    owner = execute(
        tmp_path,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=BrokenStream())),
    )
    response = owner.to_dict()["responses"][0]
    assert response["body_bytes"] == len(b"prefix")
    assert response["metadata"]["complete"] is False
    assert response["metadata"]["error"] == "network_transport_error"
    assert not owner.to_dict()["ingestion"]


def test_final_scope_rejected_by_pit_bridge_before_disk(tmp_path: Path) -> None:
    plan = offline_plan()
    assert plan.split is not None and plan.split_pin is not None
    scope = HistoricalReadScope(
        Instrument(plan.ticker, Exchange.KOSPI, Currency.KRW),
        ResearchDateRange(plan.day, plan.day),
        plan.partition,
        plan.split,
        plan.split_pin,
        "0" * 64,
    )
    object.__setattr__(scope, "partition", "final_holdout")
    with pytest.raises(ValueError, match="historical_partition_forbidden"):
        assess_capture_pit(tmp_path / "absent", plan, "a", "0" * 64, scope, "0" * 64)
    assert not (tmp_path / "absent").exists()


def test_offline_mount_cannot_escape_to_live_transport(tmp_path: Path) -> None:
    with httpx.Client(
        transport=fixture_transport(offline_plan()),
        mounts={"https://data-dbg.krx.co.kr": httpx.HTTPTransport()},
        trust_env=False,
    ) as client:
        with pytest.raises(ValueError, match="offline_transport_required"):
            run_capture(tmp_path / "absent", offline_plan(), "a", client, clock=lambda: NOW)
    assert not (tmp_path / "absent").exists()


def test_auth_key_not_in_evidence(tmp_path: Path) -> None:
    owner = execute(tmp_path, api_key="secret-not-for-evidence")
    assert "secret-not-for-evidence" not in owner.payload_json
    assert "AUTH_KEY" not in owner.payload_json


def test_stale_ingestion_failure_preserves_original_record(tmp_path: Path) -> None:
    plan = offline_plan()
    first = execute(tmp_path)

    def changed(request: httpx.Request) -> httpx.Response:
        payload = json.loads(fixture_transport(plan).handle_request(request).content)
        payload["OutBlock_1"][0]["TDD_CLSPRC"] = "106"
        return httpx.Response(200, json=payload)

    # Same local receipt time cannot authorize overwriting an earlier different price.
    with httpx.Client(transport=httpx.MockTransport(changed), trust_env=False) as client:
        second = run_capture(
            tmp_path, plan, "stale", client, clock=lambda: NOW, sleep=lambda seconds: None
        )
    assert second.to_dict()["ingestion"] == [["failed", 0, "ValueError"]]
    assert verify_capture(tmp_path, plan, "a", first.identifier) == first
    with closing(sqlite3.connect(tmp_path / DB)) as connection:
        assert (
            json.loads(connection.execute("SELECT payload FROM bars").fetchone()[0])["close"] == 105
        )


def test_cli_plan_pins_history_readback_and_no_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = execute(tmp_path)
    plan = offline_plan()
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.document.payload_json)
    common = [
        "--root",
        str(tmp_path),
        "--plan",
        str(plan_file),
        "--plan-pin",
        plan.document.identifier,
        "--attempt",
        "a",
        "--pin",
        owner.identifier,
    ]
    assert main(["forward-capture", "dry-run", *common]) == 0
    assert json.loads(capsys.readouterr().out)["network_requests"] == 0
    assert main(["forward-capture", "history", *common]) == 0
    assert json.loads(capsys.readouterr().out)["responses"][0]["raw_sha256"]
    output = tmp_path / "response.bin"
    assert main(["forward-capture", "readback", *common, "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["bytes"] == 202
    original = output.read_bytes()
    assert main(["forward-capture", "readback", *common, "--output", str(output)]) == 2
    assert output.read_bytes() == original


def test_raw_metadata_commit_interruption_stops_resume(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.sqlite import SQLiteStore

    original = SQLiteStore.archive

    def archive_then_interrupt(*args: Any, **kwargs: Any) -> int:
        original(*args, **kwargs)
        raise KeyboardInterrupt

    with (
        patch.object(SQLiteStore, "archive", archive_then_interrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        execute(tmp_path)
    assert capture_status(tmp_path).to_dict()["captures"][0]["status"] == "interrupted"
    with pytest.raises(ValueError, match="interrupted_response_commit"):
        execute(
            tmp_path,
            transport=httpx.MockTransport(lambda r: pytest.fail("HTTP on partial recovery")),
        )


def test_synthetic_and_provider_archives_do_not_mix(tmp_path: Path) -> None:
    execute(tmp_path)
    from tests.integration.test_krx_forward_authorization import KEY, permission_fixture

    plans, authorization, trust = permission_fixture(
        tmp_path, [replace(offline_plan(), mode="authorized_provider")], archive_root=tmp_path
    )
    plan = plans[0]
    with pytest.raises(ValueError, match="mixed_capture_origins_forbidden"):
        execute(
            tmp_path,
            plan,
            attempt="a",
            authorization=authorization,
            authorization_trust=trust,
            api_key=KEY,
            allow_network=True,
            transport=httpx.MockTransport(lambda r: pytest.fail("HTTP before origin validation")),
        )


def test_base_cli_does_not_require_optional_extras() -> None:
    import subprocess
    import sys

    source = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'pydantic', 'fastapi', 'boto3', 'sklearn'}:
        raise ImportError('optional extras deliberately absent')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from donghak_stock_vision.cli import parser
args = parser().parse_args(['forward-capture', 'status', '--root', '/tmp/unused'])
assert args.command == 'forward-capture'
"""
    subprocess.run([sys.executable, "-c", source], check=True, capture_output=True, timeout=10)
