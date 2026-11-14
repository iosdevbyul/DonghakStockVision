"""Real validators, committed synthetic fixtures; no provider or launchd calls."""

import calendar
import copy
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_krx_recurring import AT
from tests.integration.test_krx_scheduler import client, enabled
from tests.integration.test_krx_scheduler import config as config
from tests.integration.test_shadow_prediction import (
    evaluation_request as evaluation_request,
)
from tests.integration.test_shadow_prediction import (
    offline as offline,
)
from tests.integration.test_shadow_prediction import (
    request_data as request_data,
)
from tests.integration.test_shadow_prediction import (
    sha,
)
from tests.integration.test_shadow_prediction import (
    shadow_fixture as shadow_fixture,
)

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.official_evidence import KIND_URL, normalize_month
from donghak_stock_vision.cli import main, parser
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage import forward_signal_journal as journal_owner
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, history
from donghak_stock_vision.storage.krx_backlog import _records, _verify
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig, run_once
from donghak_stock_vision.storage.operational_market import DB
from donghak_stock_vision.storage.shadow_handoff import (
    _derived_request,
    after_collection,
    alignment,
    schedule_alignment,
)
from donghak_stock_vision.storage.shadow_prediction import _calendar, _model, recover


def prepared(
    config: SchedulerConfig, d: dict[str, Any], root: Path
) -> tuple[SchedulerConfig, FrozenJSON, tuple[Path, ...]]:
    cfg = SchedulerConfig(
        FrozenJSON.freeze(
            config.document.to_dict() | {"operational_root": d["operational"]["root"]}
        )
    )
    request = root / "request.json"
    request.write_text(json.dumps(d))
    pin = root / "request.pin"
    pin.write_text(digest(d))
    pin.chmod(0o600)
    head = root / "head.pin"
    head.write_text(GENESIS)
    head.chmod(0o600)
    days, _ = _calendar(d)
    day = max(
        day for day, state in days.items() if state == "session" and day < d["target_trading_date"]
    )
    collection = FrozenJSON.freeze(
        dict(version="krx_scheduler_run_v1", outcome="succeeded", day=day)
    )
    return (
        cfg,
        collection,
        (request, pin, root / "journal.sqlite3", head, root / "intents", root / "pins"),
    )


def test_committed_fixture_handoff_and_duplicate(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    d, at, _ = shadow_fixture
    before = sha(Path(d["operational"]["root"]) / DB), sha(Path(d["model"]["database"]))
    cfg, collection, paths = prepared(config, d, tmp_path)
    first = after_collection(cfg, collection, *paths, clock=lambda: at).to_dict()
    assert first["collection"] == collection.to_dict()
    research = first["research"]
    assert research["status"] == "completed"
    assert research["recording"]["new_predictions"] == 5
    assert not research["real_collection_completion_verified"]
    paths[3].write_text(research["recording"]["head_pin"])
    head = sha(paths[2])
    duplicate = after_collection(cfg, collection, *paths, clock=lambda: at).to_dict()
    assert duplicate["research"]["recording"]["duplicate_predictions"] == 5
    assert sha(paths[2]) == head
    assert before == (sha(Path(d["operational"]["root"]) / DB), sha(Path(d["model"]["database"])))
    assert not first["pit_qualified"] and not first["performance_approved"]


@pytest.mark.parametrize("outcome", ["failed", "stopped", "blocked"])
def test_collection_failure_never_opens_request(
    config: SchedulerConfig, tmp_path: Path, outcome: str
) -> None:
    collection = FrozenJSON.freeze(dict(version="krx_scheduler_run_v1", outcome=outcome))
    p = tmp_path / "absent"
    result = after_collection(config, collection, p, p, p, p, p, p).to_dict()
    assert result["collection"] == collection.to_dict()
    assert result["research"]["status"] == "skipped" and not p.exists()


@pytest.mark.parametrize("change", ["pin", "model", "day", "owner", "real_promotion", "missing"])
def test_fail_closed_and_preserve_collection(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    change: str,
) -> None:
    original, at, _ = shadow_fixture
    d = copy.deepcopy(original)
    if change == "model":
        d["model"]["pin"] = "0" * 64
    if change == "real_promotion":
        d["source_kind"] = "local_provider_capture"
    if change == "missing":
        d["operational"]["evidence"] = []
    cfg, collection, paths = prepared(config, d, tmp_path)
    if change == "pin":
        paths[1].write_text("0" * 64)
    if change == "owner":
        cfg = config
    if change == "day":
        collection = FrozenJSON.freeze(collection.to_dict() | {"day": "2000-01-01"})
    result = after_collection(cfg, collection, *paths, clock=lambda: at).to_dict()
    assert result["collection"] == collection.to_dict()
    assert result["research"]["status"] == "blocked"
    if paths[2].exists():
        # Missing inputs may append failure records, never predictions.
        head = result["research"]["recording"]["head_pin"]
        entries = history(paths[2], head).to_dict()["entries"]
        assert all(e["entry"]["event"]["kind"] == "failure" for e in entries)


def test_alignment_deadline_and_missing_input(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
) -> None:
    d, at, _ = shadow_fixture
    valid = alignment(d, clock=lambda: at).to_dict()
    assert valid["input_timing_ready"] and not valid["prediction_created"]
    deadline = datetime.fromisoformat(valid["generation_deadline_exclusive"])
    assert alignment(d, clock=lambda: deadline - timedelta(microseconds=1)).to_dict()[
        "input_timing_ready"
    ]
    late = alignment(d, clock=lambda: deadline).to_dict()
    assert all("shadow_target_not_future" in r["reasons"] for r in late["results"])
    changed = copy.deepcopy(d)
    changed["operational"]["evidence"] = []
    missing = alignment(changed, clock=lambda: at).to_dict()
    assert not missing["input_timing_ready"] and all(
        r["missing_input_dates"] for r in missing["results"]
    )
    assert valid["source_publication_time"] == "UNKNOWN"


def test_before_original_collection_and_backwards_clock(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
) -> None:
    d, at, _ = shadow_fixture
    early = alignment(d, clock=lambda: at - timedelta(days=2)).to_dict()
    assert all("shadow_input_collected_after_execution" in r["reasons"] for r in early["results"])
    times = iter([at, at - timedelta(seconds=1)])
    with pytest.raises(ValueError, match="clock_order"):
        alignment(d, clock=lambda: next(times))


def test_handoff_interruption_recovery(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, _ = shadow_fixture
    cfg, collection, paths = prepared(config, d, tmp_path)
    commit = journal_owner._commit

    def lost(conn: sqlite3.Connection) -> None:
        commit(conn)
        raise OSError("lost acknowledgement")

    monkeypatch.setattr(journal_owner, "_commit", lost)
    result = after_collection(cfg, collection, *paths, clock=lambda: at).to_dict()
    assert result["collection"] == collection.to_dict()
    assert result["research"]["journal_write_outcome"] == "inspect_retained_checkpoints_and_intents"
    monkeypatch.setattr(journal_owner, "_commit", commit)
    intent = next(paths[4].glob("*.json"))
    event = FrozenJSON(intent.read_text()).to_dict()
    recovered = recover(d, paths[2], GENESIS, intent, paths[5] / (event["event_id"] + ".pin"))
    paths[3].write_text(recovered.to_dict()["head_pin"])
    resumed = after_collection(cfg, collection, *paths, clock=lambda: at).to_dict()
    assert resumed["research"]["recording"]["recorded"] == 5


def test_alignment_cli_needs_no_journal_or_credentials(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    d, _, _ = shadow_fixture
    _, _, paths = prepared(config, d, tmp_path)
    # Real current time is before the fixture's collection timestamps.
    report = tmp_path / "report.json"
    main(
        [
            "shadow-prediction",
            "alignment",
            "--request",
            str(paths[0]),
            "--request-pin-file",
            str(paths[1]),
            "--report",
            str(report),
        ]
    )
    assert not paths[2].exists()
    assert json.loads(report.read_text())["prediction_created"] is False


def test_incomplete_post_collection_cli_options(config: SchedulerConfig, tmp_path: Path) -> None:
    path = tmp_path / "scheduler.json"
    path.write_text(config.document.payload_json)
    path.chmod(0o600)
    args = parser().parse_args(
        [
            "krx-scheduler",
            "run-once",
            "--config",
            str(path),
            "--config-pin",
            config.document.identifier,
            "--shadow-request",
            str(tmp_path / "absent"),
        ]
    )
    from donghak_stock_vision.krx_scheduler_cli import run

    with pytest.raises(ValueError, match="complete_exclusive"):
        run(args)


def test_existing_weekday_schedule_misses_monday_event(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
) -> None:
    d, at, _ = shadow_fixture
    policy_path = config.path("policy_root") / "recurring-policy.json"
    before = policy_path.read_bytes()
    result = schedule_alignment(config, d, at).to_dict()
    slot = datetime.fromisoformat(result["next_configured_weekday_slot"])
    assert slot.weekday() == 0 and slot.hour == 19 and slot.tzinfo is not None
    assert slot.date().isoformat() == d["target_trading_date"]
    assert not result["configured_slot_before_prediction_deadline"]
    assert not result["configured_schedule_aligned"]
    assert not result["policy_changed"] and policy_path.read_bytes() == before


@pytest.mark.parametrize("when", ["midnight", "after_close"])
def test_handoff_never_retargets_elapsed_event(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    when: str,
) -> None:
    d, _, _ = shadow_fixture
    cfg, collection, paths = prepared(config, d, tmp_path)
    at = datetime.fromisoformat(d["target_trading_date"]).replace(tzinfo=SEOUL)
    if when == "after_close":
        at += timedelta(hours=19)
    result = after_collection(cfg, collection, *paths, clock=lambda: at).to_dict()
    assert result["research"]["reason"] == "shadow_target_not_future"
    assert not paths[2].exists()
    assert json.loads(paths[0].read_text())["target_trading_date"] == d["target_trading_date"]


def test_handoff_clock_reversal_before_record(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    d, at, _ = shadow_fixture
    cfg, collection, paths = prepared(config, d, tmp_path)
    values = iter([at, at - timedelta(seconds=1)])
    result = after_collection(cfg, collection, *paths, clock=lambda: next(values)).to_dict()
    assert result["research"]["reason"] == "handoff_clock_order" and not paths[2].exists()


def test_post_commit_request_freeze_retains_original_pin_and_capture(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    # Original capture/receipt/registry validators over MockTransport fixtures.
    enabled(config)
    calls: list[int] = []
    with client(calls) as http:
        collected = run_once(config, execute=True, client=http, clock=lambda: AT).to_dict()
    assert collected["outcome"] == "succeeded" and calls == [1]
    completion = _records(config)[0]
    assert _verify(config, completion)["observation_count"] == 940
    original = copy.deepcopy(shadow_fixture[0])
    original["operational"] = dict(
        root=str(config.path("operational_root")), pin="0" * 64, evidence=[]
    )
    original_pin = digest(original)
    database = config.path("operational_root") / DB
    capture = config.path("capture_root") / "pilot.sqlite3"
    protected = sha(database), sha(capture)
    intents, pins = tmp_path / "derived-intents", tmp_path / "derived-pins"
    derived, lineage = _derived_request(config, original, completion, intents, pins)
    assert derived["operational"]["pin"] == sha(database)
    assert original["operational"]["pin"] == "0" * 64 and digest(original) == original_pin
    assert lineage["template_pin"] == original_pin and not lineage["independently_authenticated"]
    assert digest(derived) == Path(lineage["request_pin_file"]).read_text().strip()
    assert _derived_request(config, original, completion, intents, pins) == (derived, lineage)
    assert protected == (sha(database), sha(capture))
    Path(lineage["request"]).write_text("{}")
    with pytest.raises(ValueError, match="retained_input_conflict"):
        _derived_request(config, original, completion, intents, pins)


@pytest.mark.parametrize("outcome,exit_code", [("succeeded", 0), ("failed", 1)])
def test_scheduler_cli_keeps_collection_exit_status(
    config: SchedulerConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    exit_code: int,
) -> None:
    # An untrusted collection selector is not evidence; the absent request blocks
    # research while the already returned collection status is preserved.
    from donghak_stock_vision.krx_scheduler_cli import run

    path = tmp_path / "scheduler.json"
    path.write_text(config.document.payload_json)
    path.chmod(0o600)
    collection = FrozenJSON.freeze(
        dict(version="krx_scheduler_run_v1", outcome=outcome, day="2025-07-07")
    )
    monkeypatch.setattr(
        "donghak_stock_vision.krx_scheduler_cli.run_once", lambda *a, **k: collection
    )
    argv = [
        "krx-scheduler",
        "run-once",
        "--config",
        str(path),
        "--config-pin",
        config.document.identifier,
        "--execute",
    ]
    for flag in ("request", "request-pin-file", "journal", "head-pin-file", "intents", "pins-root"):
        argv.extend(["--shadow-" + flag, str(tmp_path / ("absent-" + flag))])
    assert run(parser().parse_args(argv)) == exit_code
    output = json.loads(capsys.readouterr().out)
    assert output["collection"] == collection.to_dict()
    assert output["research"]["status"] == ("blocked" if outcome == "succeeded" else "skipped")


def test_handoff_sealed_window_before_collection_source_replay(
    config: SchedulerConfig,
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = copy.deepcopy(shadow_fixture[0])
    d["source_kind"] = "local_provider_capture"
    _, info = _model(d)
    target = date.fromisoformat(info["access_split"]["final_test"]["start"]) + timedelta(days=14)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    d["target_trading_date"] = target.isoformat()
    start, end = target - timedelta(days=40), target + timedelta(days=14)
    months = set()
    while start <= end:
        months.add((start.year, start.month))
        start += timedelta(days=1)
    d["calendars"] = []
    for year, month in sorted(months):
        body = '<table id="calBig">'
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            weekday = date(year, month, day).weekday()
            css = "sat" if weekday == 5 else "sun" if weekday == 6 else ""
            body += f'<tr><td class="{css}">{day}</td></tr>'
        raw = (body + "</table>").encode()
        source = dict(
            url=KIND_URL,
            status=200,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            params=dict(
                method="searchTraidingCalendar",
                ldMktTpCd="1",
                selYear=str(year),
                selMonth=str(month),
            ),
        )
        raw_path, metadata = tmp_path / f"{year}-{month}.html", tmp_path / f"{year}-{month}.json"
        raw_path.write_bytes(raw)
        metadata.write_text(json.dumps(source))
        d["calendars"].append(
            dict(
                raw=str(raw_path),
                source=str(metadata),
                normalized_pin=normalize_month(raw, source).identifier,
            )
        )
    cfg, collection, paths = prepared(config, d, tmp_path)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("sealed source must not be replayed")

    monkeypatch.setattr("donghak_stock_vision.storage.shadow_handoff._verify", forbidden)
    result = after_collection(cfg, collection, *paths).to_dict()
    assert result["research"]["reason"] == "shadow_protected_final_test_forbidden"
    assert not paths[2].exists()
