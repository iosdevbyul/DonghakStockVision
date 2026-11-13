"""Offline KRX-schema fixtures, not genuine exchange observations or performance."""

import hashlib
import json
import socket
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_real_market_training import source

from donghak_stock_vision.cli import main
from donghak_stock_vision.data.learning import FEATURES, digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.signals.dataset import build_dataset, split_dataset
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.operational_market import DB, OperationalMarketStore
from donghak_stock_vision.storage.real_market_dataset import (
    _request,
    prepare_dataset,
    train_dataset,
)
from donghak_stock_vision.storage.real_market_training import validate_real_daily_model


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


@pytest.fixture(scope="module")
def request_data(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("dataset-contract-fixture")
    original = source(root, 1000)
    with closing(sqlite3.connect(original.database)) as db:
        days = [
            r[0] for r in db.execute("SELECT DISTINCT trading_date FROM bars ORDER BY trading_date")
        ]
        # A protected original cannot be parsed: successful preparation proves no replay.
        db.execute(
            "UPDATE raw_pages SET body=x'00' WHERE source_date BETWEEN ? AND ?",
            (days[670], days[699]),
        )
        db.commit()
    split = ExplicitResearchSplit(
        ResearchDateRange(date.fromisoformat(days[0]), date.fromisoformat(days[499])),
        ResearchDateRange(date.fromisoformat(days[500]), date.fromisoformat(days[669])),
        ResearchDateRange(date.fromisoformat(days[670]), date.fromisoformat(days[699])),
    )
    protocol = dict(
        source_market_sha256=original.database_pin,
        split=split.to_dict(),
        research_calendar={
            "version": 1,
            "reason": "known_incomplete_trading_date",
            "action": "exclude_research_session",
            "incomplete_dates": [],
        },
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        horizon_sessions=5,
    )
    path = root / "protocol.json"
    path.write_text(json.dumps(protocol))
    operational = root / "operational"
    OperationalMarketStore.initialize(operational)
    return dict(
        version="real_market_dataset_request_v1",
        dataset_version="fixture-v1",
        research=dict(
            database=str(original.database),
            pin=sha(original.database),
            source_contract="receipt_archive_v1",
        ),
        operational=dict(root=str(operational), pin=sha(operational / DB), evidence=[]),
        protocol=dict(path=str(path), sha256=sha(path)),
        tickers=list(original.tickers),
        cutoff=original.cutoff.isoformat(),
        diagnostic_test=dict(start=days[700], end=days[999]),
        known_missing_sessions=[],
    )


@pytest.fixture(scope="module")
def prepared(request_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot, manifest = prepare_dataset(request_data)
    return snapshot.to_dict(), manifest.to_dict()


def test_pins_originals_roles_and_reproducibility(
    request_data: dict[str, Any], prepared: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    before = {
        p: sha(Path(p))
        for p in [request_data["research"]["database"], request_data["protocol"]["path"]]
    }
    snapshot, manifest = prepare_dataset(request_data)
    assert (snapshot.to_dict(), manifest.to_dict()) == prepared
    assert before == {p: sha(Path(p)) for p in before}
    assert manifest.to_dict()["observations"] == 970 * 5
    assert manifest.to_dict()["can_train"]
    assert not manifest.to_dict()["protected_final_test_read"]
    assert (
        manifest.to_dict()["source"]["request"]["original_source_pin"]
        != request_data["research"]["pin"]
    )
    access = ExplicitResearchSplit.from_dict(
        prepared[0]["real_market_source"]["request"]["access_split"]
    )
    assert all(
        not access.final_test.start.isoformat()
        <= b["trading_date"]
        <= access.final_test.end.isoformat()
        for rows in prepared[0]["rows"].values()
        for b in rows
    )


def test_features_and_labels_do_not_cross_boundaries(
    prepared: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    snapshot, manifest = prepared
    samples, _ = build_dataset(snapshot)
    fit = ExplicitResearchSplit.from_dict(snapshot["real_market_source"]["request"]["fit_split"])
    split = split_dataset(samples, "historical_research", fit)
    for name, rows in split["partitions"].items():
        span = {"train": fit.training, "validation": fit.validation, "test": fit.final_test}[name]
        assert all(
            span.start
            <= date.fromisoformat(r["trading_date"])
            <= date.fromisoformat(r["label_end"])
            <= span.end
            for r in rows
        )
    assert manifest["exclusions"]["insufficient_point_in_time_data"] > 0


def test_actual_trainer_reloading_and_daily_gate(
    request_data: dict[str, Any], tmp_path: Path
) -> None:
    result = train_dataset(request_data, tmp_path / "model.sqlite3").to_dict()
    assert result["training"]["status"] == "trained"
    assert not result["performance_approved"] and not result["pit_qualified"]
    with closing(ReadOnlyAnalysisStore(tmp_path / "model.sqlite3")) as store:
        model = load_model(store, result["training"]["model_version"], "historical_research")
        fit_end = date.fromisoformat(request_data["diagnostic_test"]["end"])
        validate_real_daily_model(store, model, fit_end + timedelta(days=1))
        with pytest.raises(ValueError, match="training_overlap"):
            validate_real_daily_model(store, model, fit_end)
        protected = _request(request_data)[0].access_split.final_test.start
        with pytest.raises(ValueError, match="protected_final_test"):
            validate_real_daily_model(store, model, protected)
    with pytest.raises(ValueError, match="new_training_output"):
        train_dataset(request_data, tmp_path / "model.sqlite3")


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "research_pin",
        "operational_pin",
        "protocol_pin",
        "protected_test",
        "cutoff",
        "missing_present",
    ],
)
def test_fail_closed(request_data: dict[str, Any], damage: str) -> None:
    d = json.loads(json.dumps(request_data))
    if damage == "version":
        d["version"] = "future"
    elif damage == "research_pin":
        d["research"]["pin"] = "0" * 64
    elif damage == "operational_pin":
        d["operational"]["pin"] = "0" * 64
    elif damage == "protocol_pin":
        d["protocol"]["sha256"] = "0" * 64
    elif damage == "protected_test":
        d["diagnostic_test"]["start"] = "2020-01-01"
    elif damage == "cutoff":
        d["cutoff"] = "2020-01-01T00:00:00+00:00"
    else:
        d["known_missing_sessions"] = ["2020-01-02"]
    with pytest.raises(ValueError):
        prepare_dataset(d)


def test_rehashed_raw_bytes_fail_original_receipt(tmp_path: Path) -> None:
    original = source(tmp_path, 30)
    from dataclasses import replace

    from donghak_stock_vision.storage.real_market_training import _VerifiedMarket

    with closing(sqlite3.connect(original.database)) as db:
        db.execute("UPDATE raw_pages SET body=x'00' WHERE id=1")
        db.commit()
    with pytest.raises(ValueError):
        with closing(_VerifiedMarket(replace(original, database_pin=sha(original.database)))):
            pass


def test_unknown_gap_is_retained_and_not_filled(request_data: dict[str, Any]) -> None:
    d = json.loads(json.dumps(request_data))
    with closing(
        sqlite3.connect(Path(d["research"]["database"]).as_uri() + "?mode=ro", uri=True)
    ) as db:
        day = db.execute(
            "SELECT source_date FROM raw_pages ORDER BY source_date LIMIT 1 OFFSET 800"
        ).fetchone()[0]
    # Known missing cannot silently erase a present observation.
    d["known_missing_sessions"] = [day]
    with pytest.raises(ValueError, match="missing_session_has_observation"):
        prepare_dataset(d)


def test_cli_request_pin_fails_before_output(request_data: dict[str, Any], tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps(request_data))
    pin = tmp_path / "request.pin"
    pin.write_text("0" * 64)
    pin.chmod(0o600)
    output = tmp_path / "output"
    assert (
        main(
            [
                "real-market-dataset",
                "prepare",
                "--request",
                str(request),
                "--request-pin-file",
                str(pin),
                "--output",
                str(output),
                "--pins-root",
                str(tmp_path / "pins"),
            ]
        )
        == 2
    )
    assert not output.exists()
    assert digest(request_data) != pin.read_text()


def test_empty_original_is_verified_without_calendar_erasure(tmp_path: Path) -> None:
    from dataclasses import replace

    from donghak_stock_vision.providers.base import RawPage
    from donghak_stock_vision.storage.real_market_dataset import _missing_sources
    from donghak_stock_vision.storage.sqlite import SQLiteStore

    original = source(tmp_path, 30)
    missing = original.access_split.training.start + timedelta(days=1)
    db_path = tmp_path / "empty.sqlite3"
    with closing(SQLiteStore(db_path)) as store:
        store.archive(
            "retained-empty-fixture",
            "krx",
            "@krx_market:KOSPI",
            RawPage(b'{"OutBlock_1": []}', original.cutoff, missing),
        )
    request = replace(original, database=db_path, database_pin=sha(db_path))
    evidence = _missing_sources(request, [missing.isoformat()])
    assert evidence[0]["classification"] == "retained_empty_original_cause_unknown"
    with closing(sqlite3.connect(db_path)) as db:
        # Deliberately defeat the fixture's immutable trigger to simulate disk tampering.
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='raw_capture_receipts'"
        ).fetchall():
            db.execute('DROP TRIGGER "' + name + '"')
        db.execute("DELETE FROM raw_capture_receipts")
        db.commit()
    with pytest.raises(ValueError):
        _missing_sources(request, [missing.isoformat()])


def test_cli_prepare_reconstructs_and_checks_independent_pin(
    request_data: dict[str, Any], tmp_path: Path
) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps(request_data))
    pin = tmp_path / "request.pin"
    pin.write_text(digest(request_data))
    pin.chmod(0o600)
    output = tmp_path / "prepared"
    pins = tmp_path / "pins"
    args = [
        "--request",
        str(request),
        "--request-pin-file",
        str(pin),
        "--output",
        str(output),
        "--pins-root",
        str(pins),
    ]
    assert main(["real-market-dataset", "prepare", *args]) == 0
    assert main(["real-market-dataset", "verify", *args]) == 0
    (pins / "source.pin").write_text("0" * 64)
    assert main(["real-market-dataset", "verify", *args]) == 2


def test_duplicate_merge_retains_times_and_rejects_conflicts(
    prepared: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    from dataclasses import replace

    from donghak_stock_vision.data.schema import DailyBar
    from donghak_stock_vision.storage.real_market_dataset import _merge_bars

    bar = DailyBar.from_dict(prepared[0]["rows"]["000001"][0])
    original = {(bar.ticker, bar.trading_date): bar}
    assert (
        _merge_bars(original, [replace(bar, collected_at=bar.collected_at + timedelta(hours=1))])
        == 1
    )
    assert original[bar.ticker, bar.trading_date] == bar
    with pytest.raises(ValueError, match="conflicting_duplicate"):
        _merge_bars(original, [replace(bar, volume=bar.volume + 1)])
