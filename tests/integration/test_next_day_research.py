"""Real validators on offline KRX-format fixtures; never real forward performance."""

import copy
import json
import shutil
import socket
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_real_market_dataset import request_data as request_data
from tests.integration.test_real_model_evaluation import evaluation_request as evaluation_request
from tests.integration.test_shadow_prediction import sha
from tests.integration.test_shadow_prediction import shadow_fixture as shadow_fixture

from donghak_stock_vision.cli import main
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.signals.dataset import build_dataset, split_dataset
from donghak_stock_vision.storage import forward_signal_journal as journal_owner
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.forward_signal_journal import GENESIS
from donghak_stock_vision.storage.next_day_research import (
    CONTRACT,
    REQUEST,
    build,
    describe,
    evaluate,
    load,
    prepare,
    train,
)
from donghak_stock_vision.storage.operational_market import DB, OperationalMarketStore
from donghak_stock_vision.storage.shadow_prediction import (
    _calendar,
    _horizon,
    events,
    record,
    recover,
    resolve,
    verify,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


@pytest.fixture(scope="module")
def v2_request(request_data: dict[str, Any]) -> dict[str, Any]:
    return dict(
        version=REQUEST,
        dataset_version="fixture-next-day-v2",
        contract=CONTRACT,
        base_request=request_data,
    )


@pytest.fixture(scope="module")
def v2_prepared(v2_request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot, manifest = prepare(v2_request)
    return snapshot.to_dict(), manifest.to_dict()


@pytest.fixture(scope="module")
def v2_model(
    v2_request: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, dict[str, Any], Path]:
    root = tmp_path_factory.mktemp("next-day-model")
    path = root / "model.sqlite3"
    result = train(v2_request, path).to_dict()
    assert result["training"]["status"] == "trained"
    pins = root / "pins"
    pins.mkdir()
    with closing(ReadOnlyAnalysisStore(path)) as store:
        manifest = store.get("real_market_dataset", result["manifest_id"])
    for name, value in (
        ("snapshot", result["snapshot_id"]),
        ("manifest", result["manifest_id"]),
        ("source", manifest["source_pin"]),
    ):
        (pins / (name + ".pin")).write_text(value)
        (pins / (name + ".pin")).chmod(0o600)
    return path, result, pins


def test_reproducible_source_and_sealed_test(
    v2_request: dict[str, Any], v2_prepared: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    base = v2_request["base_request"]
    before = sha(Path(base["research"]["database"]))
    snapshot, manifest = prepare(v2_request)
    assert (snapshot.to_dict(), manifest.to_dict()) == v2_prepared
    assert sha(Path(base["research"]["database"])) == before
    assert manifest.to_dict()["historical_replay_only"]
    assert not manifest.to_dict()["official_final_test"]
    access = ExplicitResearchSplit.from_dict(
        manifest.to_dict()["source"]["request"]["access_split"]
    )
    assert all(
        not access.final_test.start.isoformat()
        <= row["trading_date"]
        <= access.final_test.end.isoformat()
        for rows in snapshot.to_dict()["rows"].values()
        for row in rows
    )


def test_features_frozen_labels_shifted_and_split_purged(
    v2_prepared: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    snapshot, manifest = v2_prepared
    old, _ = build_dataset(snapshot)
    new, _ = build(snapshot)
    by_key = {(s.ticker, s.trading_date): s for s in old}
    fit = ExplicitResearchSplit.from_dict(manifest["source"]["request"]["fit_split"])
    for sample in new:
        original = by_key[sample.ticker, sample.trading_date]
        assert sample.values == original.values and sample.input_hash == original.input_hash
        if sample.label is not None:
            assert sample.label_start is not None and original.label_start is not None
            assert sample.label_start > original.label_start
            assert len(sample.label_sessions) == 5
    split = split_dataset(new, "historical_research", fit)
    for name, rows in split["partitions"].items():
        span = {"train": fit.training, "validation": fit.validation, "test": fit.final_test}[name]
        assert all(
            span.start
            <= date.fromisoformat(r["trading_date"])
            < date.fromisoformat(r["label_start"])
            <= date.fromisoformat(r["label_end"])
            <= span.end
            for r in rows
        )
    assert split["purged"]


def test_future_prices_cannot_change_features(
    v2_prepared: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    snapshot, _ = v2_prepared
    original, _ = build(snapshot)
    altered = copy.deepcopy(snapshot)
    ticker = sorted(snapshot["rows"])[0]
    day = original[50].trading_date
    for row in altered["rows"][ticker]:
        if row["trading_date"] > day.isoformat():
            for field in ("open", "high", "low", "close"):
                row[field] += 1
    rebuilt, _ = build(altered)
    assert {
        (s.ticker, s.trading_date): (s.values, s.input_hash)
        for s in original
        if s.trading_date <= day
    } == {
        (s.ticker, s.trading_date): (s.values, s.input_hash)
        for s in rebuilt
        if s.trading_date <= day
    }


@pytest.mark.parametrize("damage", ["version", "contract", "source", "sealed"])
def test_bad_requests_fail_closed(v2_request: dict[str, Any], damage: str) -> None:
    d = copy.deepcopy(v2_request)
    if damage == "version":
        d["version"] = "future"
    elif damage == "contract":
        d["contract"]["skipped_sessions"] = 0
    elif damage == "source":
        d["base_request"]["research"]["pin"] = "0" * 64
    else:
        d["base_request"]["diagnostic_test"]["start"] = json.loads(
            Path(d["base_request"]["protocol"]["path"]).read_text()
        )["split"]["final_test"]["start"]
    with pytest.raises(ValueError):
        prepare(d)


def test_train_reload_evaluation_and_v1_isolation(
    v2_model: tuple[Path, dict[str, Any], Path],
) -> None:
    path, result, _ = v2_model
    before = sha(path)
    with closing(ReadOnlyAnalysisStore(path)) as store:
        version = result["training"]["model_version"]
        model = load(store, version)
        assert evaluate(store, version) == result["evaluation"]
        assert describe(store, version) == result["diagnostics"]
        assert all(
            key in result["diagnostics"]["directions"]["up"]["test"]["overall"]["metrics"]
            for key in (
                "accuracy",
                "balanced_accuracy",
                "precision",
                "recall",
                "f1",
                "roc_auc",
                "pr_auc_ap",
            )
        )
        assert not model["performance_approved"] and not model["pit_qualified"]
        with pytest.raises(ValueError, match="missing_model"):
            load_model(store, version)
    assert sha(path) == before


def test_prepare_verify_cli(v2_request: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(v2_request))
    pin = tmp_path / "request.pin"
    pin.write_text(digest(v2_request))
    pin.chmod(0o600)
    args = [
        "real-market-dataset",
        "prepare",
        "--request",
        str(path),
        "--request-pin-file",
        str(pin),
        "--output",
        str(tmp_path / "prepared"),
        "--pins-root",
        str(tmp_path / "pins"),
    ]
    assert main(args) == 0
    args[1] = "verify"
    assert main(args) == 0


@pytest.mark.parametrize("target", ["2026-11-17", "2026-11-18", "2026-11-23"])
def test_calendar_gap_including_weekend_and_holiday(target: str) -> None:
    days = {}
    day = date(2026, 10, 1)
    while day <= date(2026, 11, 30):
        days[day.isoformat()] = "session" if day.weekday() < 5 else "weekend"
        day += timedelta(days=1)
    # Explicit fixture holiday; never a claim about the real KRX calendar.
    days["2026-11-16"] = "holiday"
    info = dict(
        access_split={
            "version": 1,
            "training": {"start": "2020-01-01", "end": "2021-01-01"},
            "validation": {"start": "2021-01-02", "end": "2022-01-01"},
            "final_test": {"start": "2024-07-01", "end": "2025-06-30"},
        }
    )
    history, future = _horizon(
        dict(version="research_shadow_request_v2", target_trading_date=target), info, days
    )
    sessions = sorted(d for d, s in days.items() if s == "session")
    assert sessions.index(future[0]) - sessions.index(history[-1]) == 2
    v1_history, _ = _horizon(
        dict(version="research_shadow_request_v1", target_trading_date=target), info, days
    )
    assert history[-1] < v1_history[-1]
    del days["2026-11-16"]
    with pytest.raises(ValueError, match="calendar_coverage_gap"):
        _horizon(dict(version="research_shadow_request_v2", target_trading_date=target), info, days)


def test_shadow_lifecycle_and_cross_contract_rejection(
    monkeypatch: pytest.MonkeyPatch,
    v2_model: tuple[Path, dict[str, Any], Path],
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    path, trained, pins = v2_model
    d, at, mature = copy.deepcopy(shadow_fixture)
    d["version"] = "research_shadow_request_v2"
    d["model"] = dict(
        database=str(path),
        pin=sha(path),
        model_version=trained["training"]["model_version"],
        source_pins_root=str(pins),
    )
    original_root = Path(d["operational"]["root"])
    op = tmp_path / "op"
    shutil.copytree(original_root, op)
    d["operational"]["root"] = str(op)
    days, _ = _calendar(d)
    sessions = sorted(day for day, state in days.items() if state == "session")
    i = sessions.index(d["target_trading_date"])
    # Add exactly the extra earliest history row needed for a one-session gap.
    earliest = date.fromisoformat(sessions[i - 13])
    rows = [
        dict(
            ISU_CD=t,
            BAS_DD=earliest.strftime("%Y%m%d"),
            MKT_NM="KOSPI",
            TDD_OPNPRC="9990",
            TDD_HGPRC="9993",
            TDD_LWPRC="9987",
            TDD_CLSPRC="9990",
            ACC_TRDVOL="9900",
            ACC_TRDVAL="98901000",
        )
        for t in d["tickers"]
    ]
    page = RawPage(
        json.dumps({"OutBlock_1": rows}).encode(),
        datetime.combine(earliest + timedelta(days=1), time(), SEOUL),
        earliest,
    )
    with closing(OperationalMarketStore(op, writable=True)) as store:
        batch = store.ingest("v2-earliest", "KOSPI", page, source_kind="synthetic_fixture")
    d["operational"]["evidence"].append(
        dict(kind="offline_fixture_batch", batch_id="v2-earliest", batch_pin=batch.identifier)
    )
    d["operational"]["pin"] = sha(op / DB)
    before = sha(op / DB)
    journal, intents, output_pins = (
        tmp_path / "v2.sqlite3",
        tmp_path / "intents",
        tmp_path / "event-pins",
    )
    r = record(d, journal, GENESIS, intents, output_pins, clock=lambda: at).to_dict()
    assert r["recorded"] == 5
    head = r["head_pin"]
    assert (
        record(d, journal, head, intents, output_pins, clock=lambda: at).to_dict()["head_pin"]
        == head
    )
    assert all(e["version"] == "research_shadow_event_v2" for e in events(journal, head))
    assert verify(d, journal, head).to_dict()["predictions_verified"] == 5
    pending = resolve(d, journal, head, intents, output_pins, clock=lambda: at).to_dict()
    assert pending["groups"]["offline_fixture_rehearsal"]["pending"] == 5
    missing_gap = copy.deepcopy(d)
    missing_gap["operational"]["evidence"] = [
        e for e in d["operational"]["evidence"] if e.get("batch_id") != "fixture-11"
    ]
    blocked = resolve(
        missing_gap, journal, pending["head_pin"], intents, output_pins, clock=lambda: mature
    ).to_dict()
    assert blocked["groups"]["offline_fixture_rehearsal"]["pending"] == 5
    assert any(
        e["payload"].get("reason") == "missing_required_gap_session"
        for e in events(journal, blocked["head_pin"])
    )
    final = resolve(
        d, journal, blocked["head_pin"], intents, output_pins, clock=lambda: mature
    ).to_dict()
    assert final["groups"]["offline_fixture_rehearsal"]["evaluated"] == 5
    assert all(
        "gap" in e["payload"]["future"]
        for e in events(journal, final["head_pin"])
        if e["kind"] == "outcome" and e["payload"]["state"] == "evaluated"
    )
    assert verify(d, journal, final["head_pin"]).to_dict()["terminal_outcomes_verified"] == 5
    assert (
        resolve(
            d, journal, final["head_pin"], intents, output_pins, clock=lambda: mature
        ).to_dict()["head_pin"]
        == final["head_pin"]
    )
    wrong = copy.deepcopy(d)
    wrong["version"] = "research_shadow_request_v1"
    with pytest.raises(ValueError, match="cross_contract"):
        verify(wrong, journal, final["head_pin"])
    assert (
        sha(op / DB) == before
        and sha(original_root / DB) == shadow_fixture[0]["operational"]["pin"]
    )

    # Same transaction owner handles v2 lost acknowledgements without duplicate writes.
    commit = journal_owner._commit

    def lost(conn: sqlite3.Connection) -> None:
        commit(conn)
        raise OSError("lost acknowledgement")

    monkeypatch.setattr(journal_owner, "_commit", lost)
    interrupted, ii, ip = tmp_path / "interrupted.sqlite3", tmp_path / "ii", tmp_path / "ip"
    with pytest.raises(OSError):
        record(d, interrupted, GENESIS, ii, ip, clock=lambda: at)
    intent = next(ii.glob("*.json"))
    event_id = json.loads(intent.read_text())["event_id"]
    recovered = recover(d, interrupted, GENESIS, intent, ip / (event_id + ".pin")).to_dict()
    monkeypatch.setattr(journal_owner, "_commit", commit)
    resumed = record(d, interrupted, recovered["head_pin"], ii, ip, clock=lambda: at).to_dict()
    assert (
        len([e for e in events(interrupted, resumed["head_pin"]) if e["kind"] == "prediction"]) == 5
    )
    late = record(
        d,
        tmp_path / "late.sqlite3",
        GENESIS,
        tmp_path / "li",
        tmp_path / "lp",
        clock=lambda: mature,
    ).to_dict()
    assert late["recorded"] == 0 and late["blocked"] == 5
    with closing(sqlite3.connect(journal)) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM signal_entries")


def test_insufficient_diagnostic_does_not_train(v2_request: dict[str, Any], tmp_path: Path) -> None:
    d = copy.deepcopy(v2_request)
    start = date.fromisoformat(d["base_request"]["diagnostic_test"]["start"])
    d["base_request"]["diagnostic_test"]["end"] = (start + timedelta(days=14)).isoformat()
    result = train(d, tmp_path / "insufficient.sqlite3").to_dict()
    assert result["training"]["model_version"] is None
    assert result["training"]["status"] == "insufficient_data"
    assert result["evaluation"] is None and not result["model_validation_completed"]
    with closing(ReadOnlyAnalysisStore(tmp_path / "insufficient.sqlite3")) as store:
        assert not store.all("next_day_model")
