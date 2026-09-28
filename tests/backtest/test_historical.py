"""Offline storage fixtures: provider metadata is not evidence of real performance."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical import ASSUMPTION, build_historical_input
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.backtest.helpers import manifest, policy
from tests.learning.helpers import CUTOFF, bars, quality


def setup_inputs(path: Path) -> tuple[SQLiteStore, dict[str, Any]]:
    rows = [replace(b, provider="fixture_historical") for b in bars(count=3, tickers=2)]
    store = SQLiteStore(path)
    for bar in rows:
        rid = store.archive(
            "fixture",
            bar.provider,
            bar.ticker,
            RawPage(b"fixture", bar.collected_at, bar.trading_date),
        )
        store.save([bar], [rid])
    m = manifest().to_dict()

    def real(v: Any) -> None:
        if isinstance(v, dict):
            if "data_origin" in v:
                v.update(data_origin="real", usage_restriction="research_only")
            for x in v.values():
                real(x)
        elif isinstance(v, list):
            for x in v:
                real(x)

    real(m)
    m["start_at"] = "2020-01-02T00:00:00+09:00"
    m["end_at"] = "2020-01-07T00:00:00+09:00"
    m["initial_account"]["observed_at"] = m["initial_account"]["received_at"] = m["start_at"]
    return store, dict(
        start=date(2020, 1, 2),
        end=date(2020, 1, 6),
        tickers=["000002", "000001"],
        captured_at=CUTOFF.isoformat(),
        quality=FrozenJSON.freeze(quality(rows)),
        manifest_template=RunManifest.from_dict(m),
        policy=policy(),
        availability_assumption=ASSUMPTION,
    )


def test_freeze_order_range_duplicate_replay_and_deletion(tmp_path: Path) -> None:
    store, k = setup_inputs(tmp_path / "market.db")
    a = build_historical_input(store, **k)
    b = build_historical_input(store, **{**k, "tickers": ["000001", "000002", "000001"]})
    assert a.identifier == b.identifier
    assert [e.to_dict()["ticker"] for e in a.tape.events] == ["000001", "000002"] * 3
    wire = a.document.payload_json
    store.close()
    (tmp_path / "market.db").unlink()
    assert a.document.payload_json == wire
    assert a.tape.to_json() == b.tape.to_json()
    assert a.document.to_dict()["execution_reason"] == "real_runner_unsupported"


def test_no_same_day_ohlc_and_no_fabricated_days(tmp_path: Path) -> None:
    store, k = setup_inputs(tmp_path / "market.db")
    a = build_historical_input(store, **k)
    t = a.tape
    assert not t.view(VirtualClock.at("2020-01-02T23:59:59+09:00", 6)).to_dict()["items"]
    view = t.view(VirtualClock.at("2020-01-03T00:00:00+09:00", 1)).to_dict()["items"]
    assert len(view) == 1 and view[0]["trading_date"] == "2020-01-02"
    assert view[0]["source_received_at"].startswith("2024-")
    assert {e.to_dict()["trading_date"] for e in t.events} == {
        "2020-01-02",
        "2020-01-03",
        "2020-01-06",
    }
    assert len(t.events) == 6
    store.close()


def test_single_boundary_missing_ticker_and_analysis(tmp_path: Path) -> None:
    store, k = setup_inputs(tmp_path / "market.db")
    a = build_historical_input(
        store,
        **{
            **k,
            "start": date(2020, 1, 3),
            "end": date(2020, 1, 3),
            "tickers": ["000001", "999999"],
        },
    )
    assert len(a.tape.events) == 1
    d = a.document.to_dict()
    assert d["missing_tickers"] == ["999999"]
    assert all(p["reason"] == "analysis_artifact_unavailable" for p in d["decision_points"])
    assert "not_pit_verified" in d["limitations"]
    assert "model_training_cutoff_unverified" in d["limitations"]
    assert not a.analyses
    store.close()


@pytest.mark.parametrize(
    "change",
    [
        {"tickers": []},
        {"tickers": ["BAD"]},
        {"start": date(2020, 1, 7), "end": date(2020, 1, 2)},
        {"captured_at": "2024-02-01T00:00:00"},
        {"availability_assumption": "midnight_utc"},
    ],
)
def test_invalid_requests(tmp_path: Path, change: dict[str, Any]) -> None:
    store, k = setup_inputs(tmp_path / "market.db")
    with pytest.raises(ValueError):
        build_historical_input(store, **{**k, **change})
    store.close()


def test_missing_model_is_explicit(tmp_path: Path) -> None:
    store, k = setup_inputs(tmp_path / "market.db")
    analysis = AnalysisStore(tmp_path / "analysis.db")
    a = build_historical_input(store, **k, analysis_store=analysis, model_id="0" * 64)
    assert {p["reason"] for p in a.document.to_dict()["decision_points"]} == {"missing_model"}
    analysis.close()
    store.close()


def test_public_phase2_insufficient_history_and_repeat(tmp_path: Path) -> None:
    from donghak_stock_vision.data.learning import FEATURES, digest, event_contract
    from donghak_stock_vision.data.snapshot import capture

    store, k = setup_inputs(tmp_path / "market.db")
    analysis = AnalysisStore(tmp_path / "analysis.db")
    snap = capture(
        store,
        k["tickers"],
        k["start"],
        k["end"],
        CUTOFF,
        "historical_research",
        quality=k["quality"].to_dict(),
        acknowledge=True,
    )
    sid = analysis.put("snapshot", snap)
    split = {"fixture": "no_training_performed"}
    did = analysis.put("dataset", {"snapshot_id": sid, "split": split})
    model = {
        "schema_version": 1,
        "features": list(FEATURES),
        "snapshot_id": sid,
        "dataset_id": did,
        "split_hash": digest(split),
        "quality_flags": snap["quality_flags"],
        **{
            key: snap[key]
            for key in (
                "mode",
                "data_origin",
                "usage_restriction",
                "anchor_policy",
                "session_basis",
            )
        },
        **event_contract(snap["session_basis"]),
        "directions": {
            d: {"parameters": None, "evaluation_status": "insufficient_data"}
            for d in ("up", "down")
        },
    }
    mid = analysis.put("model", model)
    a = build_historical_input(store, **k, analysis_store=analysis, model_id=mid)
    b = build_historical_input(store, **k, analysis_store=analysis, model_id=mid)
    assert a.identifier == b.identifier
    assert len(a.analyses) == 6
    from donghak_stock_vision.backtest.historical import HistoricalBacktestInput

    audit = a.document.to_dict()
    audit["analyses"][0]["created_at"]["signal"] = "2099-01-01T00:00:00Z"
    different_audit = HistoricalBacktestInput(FrozenJSON.freeze(audit))
    assert different_audit.identifier == a.identifier
    assert different_audit.document.identifier != a.document.identifier
    assert all(p["status"] == "blocked" for p in a.document.to_dict()["decision_points"])
    assert all("history" in p["reason"] for p in a.document.to_dict()["decision_points"])
    for artifact in a.analyses:
        assert artifact.bundle.to_dict()["model_id"] == mid
    # A changed latest row cannot silently enter the immutable training snapshot inference.
    row = store.read("000001", k["start"], k["end"])[0]
    changed = replace(
        row, volume=row.volume + 1, collected_at=row.collected_at + timedelta(hours=1)
    )
    rid = store.archive(
        "changed",
        row.provider,
        row.ticker,
        RawPage(b"changed", changed.collected_at, row.trading_date),
    )
    store.save([changed], [rid])
    c = build_historical_input(store, **k, analysis_store=analysis, model_id=mid)
    assert c.identifier != a.identifier
    assert all(
        p["reason"] == "model_snapshot_market_mismatch"
        for p in c.document.to_dict()["decision_points"]
    )
    assert len(a.analyses) == 6
    analysis.close()
    store.close()


def test_cli_freeze_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from donghak_stock_vision.cli import main

    store, k = setup_inputs(tmp_path / "market.db")
    store.close()
    config = FrozenJSON.freeze(
        {
            "captured_at": k["captured_at"],
            "quality": k["quality"].to_dict(),
            "manifest": k["manifest_template"].to_dict(),
            "policy": k["policy"].to_dict(),
            "availability_assumption": ASSUMPTION,
        }
    )
    path = tmp_path / "config.json"
    path.write_text(config.payload_json)
    out = tmp_path / "input.json"
    args = [
        "backtest-input",
        "--market-db",
        str(tmp_path / "market.db"),
        "--start",
        "2020-01-02",
        "--end",
        "2020-01-06",
        "--tickers",
        "000001",
        "--config",
        str(path),
        "--output",
        str(out),
    ]
    assert main(args) == 0
    assert "real_runner_unsupported" in capsys.readouterr().out
    assert out.exists()
    assert main(args) == 2


def test_real_input_never_implicitly_becomes_synthetic(tmp_path: Path) -> None:
    from donghak_stock_vision.backtest.service import run_backtest

    store, k = setup_inputs(tmp_path / "market.db")
    a = build_historical_input(store, **k)
    with pytest.raises(ValueError, match="real_runner_unsupported"):
        run_backtest(
            a.document,
            start="2020-01-02T00:00:00+09:00",
            end="2020-01-07T00:00:00+09:00",
            initial_cash="10000",
            tickers=["000001", "000002"],
        )
    store.close()


def test_empty_data_has_no_fabricated_events(tmp_path: Path) -> None:
    store, k = setup_inputs(tmp_path / "market.db")
    a = build_historical_input(store, **{**k, "tickers": ["999999"]})
    assert not a.tape.events and not a.analyses
    assert a.document.to_dict()["missing_tickers"] == ["999999"]
    store.close()
