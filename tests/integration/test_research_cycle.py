"""Reuse actual validators/fitters on explicitly synthetic operational fixtures."""

import copy
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_krx_scheduler import config as config
from tests.integration.test_next_day_research import (
    evaluation_request as evaluation_request,
)
from tests.integration.test_next_day_research import make_shadow_v2
from tests.integration.test_next_day_research import (
    offline as offline,
)
from tests.integration.test_next_day_research import (
    request_data as request_data,
)
from tests.integration.test_next_day_research import (
    shadow_fixture as shadow_fixture,
)
from tests.integration.test_next_day_research import (
    v2_model as v2_model,
)
from tests.integration.test_next_day_research import (
    v2_request as v2_request,
)
from tests.integration.test_shadow_prediction import sha

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage import forward_signal_journal as journal_owner
from donghak_stock_vision.storage.forward_signal_journal import GENESIS
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig
from donghak_stock_vision.storage.operational_market import DB, OperationalMarketStore
from donghak_stock_vision.storage.research_cycle import VERSION, run_cycle
from donghak_stock_vision.storage.shadow_handoff import alignment, schedule_alignment
from donghak_stock_vision.storage.shadow_prediction import events, recover


@pytest.fixture(scope="module")
def source_v2(
    v2_model: tuple[Path, dict[str, Any], Path],
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[dict[str, Any], datetime, datetime]:
    root = tmp_path_factory.mktemp("cycle-source")
    d, at, mature, _ = make_shadow_v2(v2_model, shadow_fixture, root)
    return d, at - timedelta(days=1), mature


def setup(
    config: SchedulerConfig, d: dict[str, Any], root: Path
) -> tuple[SchedulerConfig, dict[str, Any]]:
    template = root / "template.json"
    template.write_text(json.dumps(d))
    template.chmod(0o600)
    pin = root / "template.pin"
    pin.write_text(digest(d))
    pin.chmod(0o600)
    head = root / "head.pin"
    head.write_text(GENESIS)
    head.chmod(0o600)
    cfg = SchedulerConfig(
        FrozenJSON.freeze(
            config.document.to_dict() | {"operational_root": d["operational"]["root"]}
        )
    )
    request = dict(
        version=VERSION,
        active_v2="v2",
        streams=[
            dict(
                name="v2",
                request=str(template),
                request_pin_file=str(pin),
                journal=str(root / "shadow.sqlite3"),
                head_pin_file=str(head),
                intents=str(root / "intents"),
                pins_root=str(root / "events"),
            )
        ],
    )
    return cfg, request


def execute(
    cfg: SchedulerConfig,
    d: dict[str, Any],
    root: Path,
    name: str,
    at: datetime,
    previous: str | None = None,
) -> dict[str, Any]:
    return run_cycle(
        cfg,
        d,
        root / name / "report.json",
        root / (name + "-pins"),
        clock=lambda: at,
        resume_report=root / previous / "report.json" if previous else None,
        resume_pin=root / (previous + "-pins") / "report.pin" if previous else None,
    ).to_dict()


def test_cycle_record_duplicate_pending_resolve_and_report(
    config: SchedulerConfig,
    source_v2: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, mature = source_v2
    cfg, request = setup(config, d, tmp_path)
    protected = [Path(d["operational"]["root"]) / DB, Path(d["model"]["database"])]
    before = [sha(p) for p in protected]
    replayed = []
    original = OperationalMarketStore._verify_batch

    def spy(self: OperationalMarketStore, batch: str, raw_id: int) -> FrozenJSON:
        value = original(self, batch, raw_id)
        replayed.append(value.to_dict()["request"]["source_date"])
        return value

    monkeypatch.setattr(OperationalMarketStore, "_verify_batch", spy)
    first = execute(cfg, request, tmp_path, "first", at)
    assert first["streams"]["v2"]["status"] == "completed"
    assert first["new_predictions"] == 5 and first["new_actual_forward_candidates"] == 0
    assert first["new_fixture_predictions"] == 5
    assert all(day < d["target_trading_date"] for day in replayed)
    stream = first["streams"]["v2"]
    assert stream["source_pin_verified"] and stream["journal_verified"]
    assert stream["summary"]["groups"]["offline_fixture_rehearsal"]["pending"] == 5
    second = execute(cfg, request, tmp_path, "second", at + timedelta(seconds=1), "first")
    assert second["new_predictions"] == 0 and second["streams"]["v2"]["duplicates"] == 5
    assert second["streams"]["v2"]["head_pin_file"] == stream["head_pin_file"]
    final = execute(cfg, request, tmp_path, "final", mature, "second")
    assert (
        final["streams"]["v2"]["summary"]["groups"]["offline_fixture_rehearsal"]["evaluated"] == 5
    )
    repeated = execute(cfg, request, tmp_path, "repeat", mature, "final")
    assert repeated["streams"]["v2"]["head_pin_file"] == final["streams"]["v2"]["head_pin_file"]
    assert [sha(p) for p in protected] == before
    assert (
        not final["pit_qualified"]
        and not final["performance_approved"]
        and not final["execution_authorized"]
    )
    assert read_pin(tmp_path / "final-pins/report.pin") == digest(final)


@pytest.mark.parametrize("damage", ["model", "raw", "owner", "history"])
def test_failure_preserves_collection(
    config: SchedulerConfig,
    source_v2: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    damage: str,
) -> None:
    original, at, _ = source_v2
    d = copy.deepcopy(original)
    op = tmp_path / "op"
    shutil.copytree(Path(d["operational"]["root"]), op)
    d["operational"]["root"] = str(op)
    if damage == "model":
        d["model"]["pin"] = "0" * 64
    elif damage == "owner":
        d["source_kind"] = "local_provider_capture"
    elif damage == "history":
        d["operational"]["evidence"] = [
            e for e in d["operational"]["evidence"] if e["batch_id"] != "v2-earliest"
        ]
    else:
        with closing(sqlite3.connect(op / DB)) as conn:
            trigger = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='raw_pages_immutable_update'"
            ).fetchone()[0]
            conn.execute("DROP TRIGGER raw_pages_immutable_update")
            conn.execute("UPDATE raw_pages SET body=x'00' WHERE id=(SELECT MIN(id) FROM raw_pages)")
            conn.execute(trigger)
            conn.commit()
    cfg, request = setup(config, d, tmp_path)
    before = sha(op / DB)
    result = execute(cfg, request, tmp_path, "failed", at)
    assert result["blocked"] > 0 and result["new_actual_forward_candidates"] == 0
    assert not result["collection_changed"] and sha(op / DB) == before
    assert result["streams"]["v2"]["status"] == "blocked" or result["streams"]["v2"]["blocked"] > 0


def test_partial_failure_exact_recovery(
    config: SchedulerConfig,
    source_v2: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, _ = source_v2
    cfg, request = setup(config, d, tmp_path)
    commit = journal_owner._commit

    def lost(conn: sqlite3.Connection) -> None:
        commit(conn)
        raise OSError("lost acknowledgement")

    monkeypatch.setattr(journal_owner, "_commit", lost)
    failed = execute(cfg, request, tmp_path, "interrupted", at)
    row = failed["streams"]["v2"]
    assert row["journal_write_outcome"] == "inspect_retained_checkpoints_and_intents"
    assert row["status"] == "blocked" and not row["journal_verified"]
    assert row["new_predictions"] is None and failed["new_predictions"] is None
    ref = request["streams"][0]
    intent = next(Path(ref["intents"]).glob("*.json"))
    event_id = json.loads(intent.read_text())["event_id"]
    derived = json.loads(Path(row["request"]).read_text())
    recovered = recover(
        derived, Path(ref["journal"]), GENESIS, intent, Path(ref["pins_root"]) / (event_id + ".pin")
    ).to_dict()
    monkeypatch.setattr(journal_owner, "_commit", commit)
    # Operator explicitly adopts the existing recovery checkpoint in a new pinned input.
    corrected = copy.deepcopy(request)
    corrected["streams"][0]["head_pin_file"] = str(
        Path(ref["pins_root"]) / ("head-" + recovered["head_pin"] + ".pin")
    )
    result = execute(cfg, corrected, tmp_path, "recovered", at)
    assert result["streams"]["v2"]["summary"]["prediction_count"] == 5
    head = read_pin(Path(result["streams"]["v2"]["head_pin_file"]))
    assert len([e for e in events(Path(ref["journal"]), head) if e["kind"] == "prediction"]) == 5


def test_tampered_resume_and_cross_contract_rejected(
    config: SchedulerConfig, source_v2: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, at, _ = source_v2
    cfg, request = setup(config, d, tmp_path)
    execute(cfg, request, tmp_path, "first", at)
    p = tmp_path / "first/report.json"
    value = json.loads(p.read_text())
    value["request_pin"] = "0" * 64
    p.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="resume_binding"):
        execute(cfg, request, tmp_path, "bad", at, "first")
    wrong = copy.deepcopy(request)
    wrong["version"] = "future"
    with pytest.raises(ValueError):
        execute(cfg, wrong, tmp_path, "version", at)
    template = tmp_path / "template.json"
    altered = copy.deepcopy(d)
    altered["version"] = "research_shadow_request_v1"
    template.write_text(json.dumps(altered))
    (tmp_path / "template.pin").write_text(digest(altered))
    with pytest.raises(ValueError, match="active_v2"):
        execute(cfg, request, tmp_path, "contract", at)


def test_cli_and_v2_alignment(
    config: SchedulerConfig, source_v2: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, at, _ = source_v2
    cfg, request = setup(config, d, tmp_path)
    assessment = alignment(d, clock=lambda: at).to_dict()
    assert assessment["event_rule"] == "delayed_reversal_barrier_v2"
    schedule = schedule_alignment(cfg, d, at, explain=True).to_dict()
    assert (
        schedule["version"] == "research_shadow_schedule_alignment_v3"
        and schedule["input_trading_date"] == assessment["input_latest_trading_date"]
    )
    # CLI is read-only/network-free; the actual host clock precedes these fixture inputs.
    request_file = tmp_path / "cycle.json"
    request_file.write_text(json.dumps(request))
    pin = tmp_path / "cycle.pin"
    pin.write_text(digest(request))
    pin.chmod(0o600)
    config_file = tmp_path / "config.json"
    config_file.write_text(cfg.document.payload_json)
    config_file.chmod(0o600)
    config_pin = tmp_path / "config.pin"
    config_pin.write_text(cfg.document.identifier)
    config_pin.chmod(0o600)
    result = main(
        [
            "shadow-prediction",
            "cycle",
            "--request",
            str(request_file),
            "--request-pin-file",
            str(pin),
            "--scheduler-config",
            str(config_file),
            "--scheduler-config-pin-file",
            str(config_pin),
            "--report",
            str(tmp_path / "cli/report.json"),
            "--pins-root",
            str(tmp_path / "cli-pins"),
        ]
    )
    assert result in (0, 1) and (tmp_path / "cli/report.json").exists()
    report = json.loads((tmp_path / "cli/report.json").read_text())
    assert report["network_requests"] == 0 and report["new_actual_forward_candidates"] == 0


def test_contract_streams_and_failure_isolation(
    config: SchedulerConfig,
    source_v2: tuple[dict[str, Any], datetime, datetime],
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    from donghak_stock_vision.storage.shadow_prediction import record, resolve

    d, at, _ = source_v2
    cfg, request = setup(config, d, tmp_path)
    ref = request["streams"][0]
    for version, template, folder, moment in (
        ("v2", d, tmp_path, at),
        (
            "v1",
            d | {"version": "research_shadow_request_v1", "model": shadow_fixture[0]["model"]},
            tmp_path / "legacy",
            at + timedelta(days=1),
        ),
    ):
        folder.mkdir(mode=0o700, exist_ok=True)
        if version == "v1":
            _, old = setup(config, template, folder)
            ref = old["streams"][0] | {"name": "v1"}
            request["streams"].append(ref)

        def frozen_clock(value: datetime = moment) -> datetime:
            return value

        r = record(
            template,
            Path(ref["journal"]),
            GENESIS,
            Path(ref["intents"]),
            Path(ref["pins_root"]),
            clock=frozen_clock,
        ).to_dict()
        p = resolve(
            template,
            Path(ref["journal"]),
            r["head_pin"],
            Path(ref["intents"]),
            Path(ref["pins_root"]),
            clock=frozen_clock,
        ).to_dict()
        ref["head_pin_file"] = str(Path(ref["pins_root"]) / ("head-" + p["head_pin"] + ".pin"))
    original = {
        ref["name"]: [
            e
            for e in events(Path(ref["journal"]), read_pin(Path(ref["head_pin_file"])))
            if e["kind"] == "prediction"
        ]
        for ref in request["streams"]
    }
    first = execute(cfg, request, tmp_path, "separate", at + timedelta(days=1))
    assert (
        first["totals_by_contract"]["research_shadow_request_v1"]["offline_fixture_rehearsal"][
            "pending"
        ]
        == 5
    )
    assert (
        first["totals_by_contract"]["research_shadow_request_v2"]["offline_fixture_rehearsal"][
            "pending"
        ]
        >= 5
    )
    for ref in request["streams"]:
        entries = events(
            Path(ref["journal"]), read_pin(Path(first["streams"][ref["name"]]["head_pin_file"]))
        )
        assert all(e in entries for e in original[ref["name"]])
    # A bad v1 model blocks that stream without invalidating verified v2 work.
    legacy = request["streams"][1]
    value = json.loads(Path(legacy["request"]).read_text())
    value["model"]["pin"] = "0" * 64
    Path(legacy["request"]).write_text(json.dumps(value))
    Path(legacy["request_pin_file"]).write_text(digest(value))
    failed = execute(cfg, request, tmp_path, "isolated", at + timedelta(days=1), "separate")
    assert failed["streams"]["v1"]["status"] == "blocked"
    assert failed["streams"]["v2"]["journal_verified"]


def test_late_latest_input_is_not_retargeted(
    config: SchedulerConfig,
    source_v2: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    from datetime import date

    from donghak_stock_vision.data.learning import timestamp
    from donghak_stock_vision.providers.base import RawPage

    template, at, _ = source_v2
    d = copy.deepcopy(template)
    # Retain only already available originals in a NEW synthetic fixture store.
    op = tmp_path / "older-only"
    OperationalMarketStore.initialize(op)
    evidence = []
    with (
        closing(OperationalMarketStore(op, writable=True)) as target,
        closing(sqlite3.connect(Path(d["operational"]["root"]) / DB)) as source,
    ):
        for body, observed, day, batch in source.execute(
            "SELECT body,collected_at,source_date,run_id FROM raw_pages ORDER BY id"
        ):
            if timestamp(observed) > at:
                continue
            value = target.ingest(
                batch,
                "KOSPI",
                RawPage(body, timestamp(observed), date.fromisoformat(day)),
                source_kind="synthetic_fixture",
            )
            evidence.append(
                dict(kind="offline_fixture_batch", batch_id=batch, batch_pin=value.identifier)
            )
    # A normal empty KOSPI response and a different market cannot advance KOSPI input.
    friday = date.fromisoformat(d["target_trading_date"]) - timedelta(days=3)
    observed = datetime.combine(friday + timedelta(days=1), datetime.min.time(), SEOUL)
    with closing(OperationalMarketStore(op, writable=True)) as target:
        target.ingest(
            "empty",
            "KOSPI",
            RawPage(b'{"OutBlock_1":[]}', observed, friday),
            source_kind="synthetic_fixture",
        )
        row = dict(
            ISU_CD="000001",
            BAS_DD=friday.strftime("%Y%m%d"),
            MKT_NM="KOSDAQ",
            TDD_OPNPRC="10000",
            TDD_HGPRC="10010",
            TDD_LWPRC="9990",
            TDD_CLSPRC="10000",
            ACC_TRDVOL="10000",
            ACC_TRDVAL="100000000",
        )
        target.ingest(
            "foreign",
            "KOSDAQ",
            RawPage(json.dumps({"OutBlock_1": [row]}).encode(), observed, friday),
            source_kind="synthetic_fixture",
        )
    d["operational"] = dict(root=str(op), pin=sha(op / DB), evidence=evidence)
    cfg, request = setup(config, d, tmp_path)
    deadline = datetime.fromisoformat(d["target_trading_date"]).replace(tzinfo=SEOUL)
    result = execute(cfg, request, tmp_path, "late", deadline)
    assert result["latest_available_source_date"] < friday.isoformat()
    assert result["new_predictions"] == 0
    assert "shadow_target_not_future" in result["streams"]["v2"]["reasons"]
    assert not Path(request["streams"][0]["journal"]).exists()


def test_sealed_window_before_operational_source_replay(
    config: SchedulerConfig,
    source_v2: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import calendar
    from datetime import date

    from donghak_stock_vision.backtest.official_evidence import KIND_URL, normalize_month
    from donghak_stock_vision.storage.shadow_prediction import _model

    original, at, _ = source_v2
    d = copy.deepcopy(original)
    _, info = _model(d)
    sealed = date.fromisoformat(info["access_split"]["final_test"]["start"])
    start = sealed - timedelta(days=40)
    end = sealed + timedelta(days=15)
    refs = []
    months = set()
    day = start
    while day <= end:
        months.add((day.year, day.month))
        day += timedelta(days=1)
    for year, month in sorted(months):
        body = (
            '<table id="calBig">'
            + "".join(
                '<tr><td class="'
                + (
                    "sat"
                    if date(year, month, n).weekday() == 5
                    else "sun"
                    if date(year, month, n).weekday() == 6
                    else ""
                )
                + f'">{n}</td></tr>'
                for n in range(1, calendar.monthrange(year, month)[1] + 1)
            )
            + "</table>"
        ).encode()
        path = tmp_path / f"{year}-{month}.html"
        path.write_bytes(body)
        meta = dict(
            url=KIND_URL,
            status=200,
            raw_sha256=hashlib.sha256(body).hexdigest(),
            params=dict(
                method="searchTraidingCalendar",
                ldMktTpCd="1",
                selYear=str(year),
                selMonth=str(month),
            ),
        )
        source = tmp_path / f"{year}-{month}.json"
        source.write_text(json.dumps(meta))
        refs.append(
            dict(
                raw=str(path),
                source=str(source),
                normalized_pin=normalize_month(body, meta).identifier,
            )
        )
    while sealed.weekday() >= 5:
        sealed += timedelta(days=1)
    d["calendars"] = refs
    d["target_trading_date"] = sealed.isoformat()
    cfg, request = setup(config, d, tmp_path)

    def forbid(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("protected source replay")

    monkeypatch.setattr(OperationalMarketStore, "_verify_batch", forbid)
    result = execute(cfg, request, tmp_path, "sealed", at)
    assert "shadow_protected_final_test_forbidden" in result["streams"]["v2"]["reasons"]
    assert result["new_predictions"] == 0
