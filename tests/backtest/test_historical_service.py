"""SQLite -> public research boundary -> real orchestration. Prices/signals are fixtures."""

import json
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_service import (
    HistoricalBacktestRequest,
    HistoricalBacktestResult,
    run_historical_backtest,
)
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from donghak_stock_vision.cli import main
from donghak_stock_vision.storage.analysis import AnalysisStore
from tests.backtest.test_historical_execution import ControlledResearch, historical_runner
from tests.backtest.test_performance import valuation_policy


def setup_request(
    path: Path, patch: pytest.MonkeyPatch, *, cycle: bool = False
) -> HistoricalBacktestRequest:
    import donghak_stock_vision.backtest.historical as builder

    patch.setattr(ControlledResearch, "buy_days", (9, 15))
    r = historical_runner(
        path,
        patch,
        end_day=16 if cycle else 14,
        session_days=(9, 12, 13, 14, 15, 16) if cycle else (9, 12, 13, 14),
        session_prices=(100, 90, 100, 110, 100, 90) if cycle else None,
    )
    assert r.historical_input is not None
    d = r.historical_input.to_dict()
    c = {
        "manifest": r.tape.manifest.to_dict(),
        "policy": r.tape.policy.to_dict(),
        **{
            k: d["source_metadata"][k]
            for k in ("captured_at", "quality", "availability_assumption")
        },
        "decision_policy": r.decision_policy.to_dict(),
        "execution_policy": r.execution_policy.to_dict(),
        "ledger_policy": r.initial.policy.document.to_dict(),
        "valuation_policy": valuation_policy(historical_marks="verified_daily_research").to_dict(),
        "admission_policy": r.config.to_dict()["decision_steps"][0]["admission_policy"],
        "reservation_by_side": {
            "BUY": {"max_notional_krw": "950", "max_cost_krw": "50"},
            "SELL": {"max_notional_krw": "0", "max_cost_krw": "0"},
        },
        "decision_dates": [f"2026-01-{day:02d}" for day in ((9, 13, 15) if cycle else (9, 13))],
        "decision_mark_field": "close",
    }
    patch.setattr(builder, "SignalService", ControlledResearch)
    return HistoricalBacktestRequest(
        date(2026, 1, 9),
        date(2026, 1, 16 if cycle else 14),
        "10000",
        ("005930",),
        d["requested_model_id"],
        FrozenJSON.freeze(c),
    )


def execute(request: HistoricalBacktestRequest, path: Path) -> HistoricalBacktestResult:
    market, analysis = ReadOnlyMarketStore(path / "market.db"), AnalysisStore(path / "analysis.db")
    try:
        return run_historical_backtest(request, market, analysis)
    finally:
        market.close()
        analysis.close()


def change(request: HistoricalBacktestRequest, **kwargs: Any) -> HistoricalBacktestRequest:
    c = request.config.to_dict()
    c.update(kwargs)
    return replace(request, config=FrozenJSON.freeze(c))


def test_round_trip_performance_and_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = setup_request(tmp_path, monkeypatch)
    before = (tmp_path / "market.db").read_bytes()
    first, second = execute(request, tmp_path), execute(request, tmp_path)
    assert first == second and first.identifier == second.identifier
    assert (tmp_path / "market.db").read_bytes() == before
    d = first.document.to_dict()
    p = d["performance"]
    assert (p["initial_equity"], p["final_equity"], p["total_return"]) == (
        "10000",
        "10170.55",
        "0.017055",
    )
    assert p["realized_pnl"] == "170.55" and p["unrealized_pnl"] == "0"
    assert p["mdd"]["ratio"] == "0.001082"
    assert (p["completed_trades"], p["win_rate"], p["fill_count"]) == (1, "1", 2)
    assert p["costs"]["fees"] == "5.09" and p["costs"]["sell_taxes"] == "4.36"
    assert d["pit_verified"] is False and d["oos_verified"] is False
    assert d["model_training_cutoff_verified"] is False
    assert "historical_daily_full_fill_assumption" in d["limitations"]
    assert "Maximum drawdown: 0.1082%" in first.report
    assert "Mode: historical_research" in first.report
    assert "SYNTHETIC" not in first.report


def test_reentry_final_open_position(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = setup_request(tmp_path, monkeypatch, cycle=True)
    result = execute(request, tmp_path).document.to_dict()
    p = result["performance"]
    assert p["fill_count"] == 3
    assert p["completed_trades"] == 1 and p["win_rate"] == "1"
    assert p["final_equity"] == "10159.73"
    assert p["total_return"] == "0.015973"
    assert p["realized_pnl"] == "170.55" and p["unrealized_pnl"] == "-10.82"
    assert p["final_positions"][0]["quantity"] == 10
    assert execute(request, tmp_path).document == FrozenJSON.freeze(result)


@pytest.mark.parametrize("cash", ["0", "-1", "NaN", "Infinity", "no", 1.5])
def test_invalid_cash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cash: Any) -> None:
    request = setup_request(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        replace(request, initial_cash=cash)


def test_request_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = setup_request(tmp_path, monkeypatch)
    for kwargs in (
        {"start": date(2027, 1, 1)},
        {"tickers": ()},
        {"tickers": ("bad",)},
        {"tickers": ("005930", "000660")},
    ):
        with pytest.raises(ValueError):
            replace(request, **kwargs)
    assert replace(request, tickers=("005930", "005930")) == request
    c = request.config.to_dict()
    del c["execution_policy"]
    with pytest.raises(ValueError):
        replace(request, config=FrozenJSON.freeze(c))


@pytest.mark.parametrize("mode", ["synthetic_explicit_full_fill", "live", "operational"])
def test_unsupported_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    request = setup_request(tmp_path, monkeypatch)
    ep = request.config.to_dict()["execution_policy"]
    ep["liquidity"] = mode
    with pytest.raises(ValueError):
        change(request, execution_policy=ep)


def test_unavailable_real_public_phase2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import donghak_stock_vision.backtest.historical as builder
    from donghak_stock_vision.signals.service import SignalService

    request = setup_request(tmp_path, monkeypatch)
    monkeypatch.setattr(builder, "SignalService", SignalService)
    store = AnalysisStore(tmp_path / "analysis.db")
    model = store.get("model", request.model_id)
    model["quality_flags"] = []
    for direction in model["directions"].values():
        direction["evaluation_status"] = "insufficient_data"
    mid = store.put("model", model)
    store.close()
    dp = request.config.to_dict()["decision_policy"]
    dp["allowed_models"] = [mid]
    request = replace(change(request, decision_policy=dp), model_id=mid)
    d = execute(request, tmp_path).document.to_dict()
    assert d["unavailable"] and d["performance"]["fill_count"] == 0
    assert "insufficient" in str(d["unavailable"])
    assert d["status"] == "completed_with_unavailable_inputs"


def test_missing_model_and_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = setup_request(tmp_path, monkeypatch)
    d = execute(replace(request, model_id="0" * 64), tmp_path).document.to_dict()
    assert "missing_model" in str(d["unavailable"])
    market = ReadOnlyMarketStore(tmp_path / "market.db")
    with pytest.raises(sqlite3.OperationalError):
        market.connection.execute("DELETE FROM bars")
    market.close()

    with sqlite3.connect(tmp_path / "market.db") as db:
        db.execute("DELETE FROM bars")
    d = execute(request, tmp_path).document.to_dict()
    assert d["unavailable"] == [{"ticker": "005930", "reason": "missing_historical_data"}]
    assert d["performance"]["fill_count"] == 0


def test_snapshot_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    request = setup_request(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "market.db") as db:
        row = json.loads(
            db.execute("SELECT payload FROM bars ORDER BY trading_date LIMIT 1").fetchone()[0]
        )
        row["volume"] += 1
        db.execute(
            "UPDATE bars SET payload=? WHERE trading_date=?", (json.dumps(row), row["trading_date"])
        )
    d = execute(request, tmp_path).document.to_dict()
    assert "model_snapshot_market_mismatch" in str(d["unavailable"])
    assert d["performance"]["fill_count"] == 0


def test_missing_final_mark_and_policy_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = setup_request(tmp_path, monkeypatch)
    request = change(request, decision_dates=["2026-01-09"])
    a = execute(request, tmp_path).document.to_dict()
    assert a["performance"]["fill_count"] == 1
    assert a["performance"]["completed_trades"] == 0
    vp = request.config.to_dict()["valuation_policy"]
    vp["max_age_seconds"] = 0
    # Final event is published exactly at end. Missing field must stay unavailable.
    vp["version"] = "second-explicit-policy"
    b = execute(change(request, valuation_policy=vp), tmp_path).document.to_dict()
    assert a["performance_hash"] != b["performance_hash"]
    assert a["run_hash"] == b["run_hash"]


def test_last_session_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = setup_request(tmp_path, monkeypatch)
    monkeypatch.setattr(ControlledResearch, "buy_days", (14,))
    request = change(request, decision_dates=["2026-01-14"])
    d = execute(request, tmp_path).document.to_dict()
    assert d["run"]["pending"]
    assert d["performance"]["fill_count"] == 0 and d["performance"]["final_cash"] == "10000"


def test_cli_json_text_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    r = setup_request(tmp_path, monkeypatch)
    config = tmp_path / "config.json"
    config.write_text(r.config.payload_json)
    args = [
        "backtest",
        "--market-db",
        str(tmp_path / "market.db"),
        "--analysis-db",
        str(tmp_path / "analysis.db"),
        "--model-id",
        r.model_id,
        "--config",
        str(config),
        "--start",
        "2026-01-09",
        "--end",
        "2026-01-14",
        "--initial-cash",
        "10000",
        "--ticker",
        "005930",
    ]
    assert main(args + ["--format", "json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["performance"]["final_equity"] == "10170.55"
    assert main(args) == 0
    assert "Total return: 1.7055%" in capsys.readouterr().out
    args[args.index("10000")] = "NaN"
    assert main(args) == 2
    capsys.readouterr()


def test_stale_final_mark_preserves_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = setup_request(tmp_path, monkeypatch)
    request = replace(change(request, decision_dates=["2026-01-09"]), end=date(2026, 1, 16))
    vp = request.config.to_dict()["valuation_policy"]
    vp["max_age_seconds"] = 0
    result = execute(change(request, valuation_policy=vp), tmp_path).document.to_dict()
    p = result["performance"]
    assert p["fill_count"] == 1
    assert p["final_equity"] is None and p["total_return"] is None
    assert p["mdd"]["ratio"] is None
    assert p["final_positions"][0]["reason"] == "stale_mark"
    assert "Final equity: unavailable" in result["report"]


def test_hold_wait_and_end_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = setup_request(tmp_path, monkeypatch)
    request = change(request, decision_dates="all_available")
    dp = request.config.to_dict()["decision_policy"]
    # Explicit fixture thresholds suppress both scores; production engine still decides.
    from tests.strategy.helpers import policy as sample_policy

    assert dp.keys() == sample_policy().keys()
    dp["thresholds"] = {"buy": "0.99", "sell": "0.99"}
    result = execute(change(request, decision_policy=dp), tmp_path).document.to_dict()
    assert result["performance"]["fill_count"] == 0
    assert all(
        x["result"]["action"] == "WAIT" for e in result["run"]["events"] for x in e["decisions"]
    )
    request = replace(request, end=date(2026, 1, 9))
    result = execute(request, tmp_path).document.to_dict()
    assert result["run"]["processed_event_count"] == 1
    assert result["run"]["pending"] and result["performance"]["fill_count"] == 0


def test_historical_performance_requires_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.performance import calculate_performance

    r = historical_runner(tmp_path, monkeypatch)
    run = r.run()
    with pytest.raises(ValueError, match="historical_input_evidence_missing"):
        calculate_performance(
            run, r.tape, valuation_policy(historical_marks="verified_daily_research")
        )
    with pytest.raises(ValueError, match="historical_valuation_assumption_required"):
        calculate_performance(run, r.tape, valuation_policy(), historical_input=r.historical_input)


def test_hold_after_acquisition_and_execution_policy_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = setup_request(tmp_path, monkeypatch)
    monkeypatch.setattr(ControlledResearch, "buy_days", (9, 12, 13, 14))
    request = change(request, decision_dates="all_available")
    first = execute(request, tmp_path).document.to_dict()
    actions = [d["result"]["action"] for e in first["run"]["events"] for d in e["decisions"]]
    assert actions == ["BUY", "HOLD", "HOLD", "HOLD"]
    assert first["performance"]["fill_count"] == 1
    ep = request.config.to_dict()["execution_policy"]
    ep["slippage_rate"] = "0"
    second = execute(change(request, execution_policy=ep), tmp_path).document.to_dict()
    assert first["run_hash"] != second["run_hash"]
    assert first["performance_hash"] != second["performance_hash"]
