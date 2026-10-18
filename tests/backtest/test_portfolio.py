"""Five exact securities, one bounded account; genuine scoring and labelled mechanics."""

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from datetime import time as midnight
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.portfolio import (
    FiveTickerRequest,
    frozen_runner,
    run_five_ticker,
)
from donghak_stock_vision.backtest.portfolio_report import replay_five_ticker
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from tests.backtest.portfolio_helpers import TICKERS, execute_fixture, mechanics_run, prepared


def changed(request: FiveTickerRequest, **values: Any) -> FiveTickerRequest:
    d = request.document.to_dict()
    d.update(values)
    return FiveTickerRequest(FrozenJSON.freeze(d))


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, FiveTickerRequest]:
    root = tmp_path_factory.mktemp("five-mechanics")
    request, _ = prepared(root, controlled=True)
    return root, request


@pytest.fixture(scope="module", params=["linear", "hgb"])
def genuine(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    root = tmp_path_factory.mktemp(f"five-{request.param}")
    specification, provider = prepared(root, str(request.param))
    before = {
        n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in ("market.db", "analysis.db")
    }
    start = time.perf_counter()
    result = execute_fixture(root, specification, provider)
    elapsed = time.perf_counter() - start
    d = result.to_dict()
    metrics = {
        "family": request.param,
        "securities": 5,
        "decision_dates": len(specification.document.to_dict()["config"]["decision_dates"]),
        "analysis_records": len(d["frozen_input"]["historical_input"]["analyses"]),
        "virtual_orders": len(d["run"]["final_account"]["orders"]),
        "fills": d["performance"]["fill_count"],
        "frozen_bytes": len(FrozenJSON.freeze(d["frozen_input"]).payload_json.encode()),
        "report_bytes": len(FrozenJSON.freeze(d["detailed_report"]).payload_json.encode()),
        "acquisition_and_replay_seconds": elapsed,
        "python_peak_bytes": None,
        "memory_note": (
            "unmeasured; expanded receipts and Python/native allocations "
            "are not bounded by a measured peak"
        ),
    }
    (root / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (root / "result.json").write_text(result.payload_json)
    assert before == {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in before}
    return {
        "result": result,
        "request": specification,
        "root": root,
        "provider": provider,
        "metrics": metrics,
    }


@pytest.fixture(scope="module")
def mechanics(base: tuple[Path, FiveTickerRequest]) -> FrozenJSON:
    root, request = base
    result = mechanics_run(root, request)
    (root / "mechanics-result.json").write_text(result.payload_json)
    return result


@pytest.fixture(scope="module")
def limited(base: tuple[Path, FiveTickerRequest]) -> FrozenJSON:
    root, request = base
    d = request.document.to_dict()
    first = d["config"]["decision_dates"][0]
    market = ReadOnlyMarketStore(root / "market.db")
    day = min(
        b.trading_date.isoformat()
        for b in market.read(TICKERS[0], date.fromisoformat(first), date.fromisoformat(d["end"]))
        if b.trading_date.isoformat() > first
    )
    market.close()
    d["initial_cash"] = "3500"
    d["end"] = day
    d["config"]["decision_dates"] = [first, day]
    return mechanics_run(
        root,
        FiveTickerRequest(FrozenJSON.freeze(d)),
        output_name="limited.db",
        contexts={day: "flat"},
    )


def test_five_exact_tickers_and_request_permutation(base: tuple[Path, FiveTickerRequest]) -> None:
    _, request = base
    assert request.document.to_dict()["tickers"] == list(TICKERS)
    assert changed(request, tickers=list(reversed(TICKERS))) == request
    assert "000075" in request.document.to_dict()["tickers"]
    assert "00088K" in request.document.to_dict()["tickers"]


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate",
        "four",
        "six",
        "bad_ticker",
        "market",
        "family",
        "range",
        "dates",
        "empty_dates",
        "calendar",
        "scope",
        "policy",
        "capture",
    ],
)
def test_invalid_request_rejected(base: tuple[Path, FiveTickerRequest], damage: str) -> None:
    _, request = base
    d = request.document.to_dict()
    if damage == "duplicate":
        d["tickers"][-1] = d["tickers"][0]
    elif damage == "four":
        d["tickers"].pop()
    elif damage == "six":
        d["tickers"].append("000660")
    elif damage == "bad_ticker":
        d["tickers"][-1] = "AAPL"
    elif damage == "market":
        d["market"] = "US"
    elif damage == "family":
        d["model_family"] = "unknown"
    elif damage == "range":
        d["start"] = "2010-01-01"
    elif damage == "dates":
        d["config"]["decision_dates"] = "all_available"
    elif damage == "empty_dates":
        d["config"]["decision_dates"] = []
    elif damage == "calendar":
        d["calendar"]["session_basis"] = "assumed"
    elif damage == "scope":
        d["evidence_scope"] = "retrospective_historical_research"
    elif damage == "capture":
        d["config"]["captured_at"] = "2010-01-01T00:00:00Z"
    else:
        d["config"]["execution_policy"]["fill_mode"] = "partial"
    with pytest.raises(ValueError):
        FiveTickerRequest(FrozenJSON.freeze(d))


def test_genuine_public_model_output_and_provenance(genuine: dict[str, Any]) -> None:
    d = genuine["result"].to_dict()
    assert d["run"]["status"] == "completed", [
        (e.get("reason"), e.get("blocked"))
        for e in d["run"]["events"]
        if e["status"] != "processed"
    ]
    report = d["detailed_report"]
    assert len(report["timeline"]) == 10
    assert report["scope"] == "synthetic_fixture" and report["data_origin"] == "synthetic"
    assert report["usage_restriction"] == "synthetic_test_only"
    assert all(row["analysis_id"] and row["model_id"] for row in report["timeline"])
    assert all(
        row["signal_evidence_scope"] == "public_model_inference" for row in report["timeline"]
    )
    assert d["frozen_input"]["historical_input"]["version"] == 2
    table = d["frozen_input"]["historical_input"]["evidence_table"]
    kinds = [a["kind"] for a in table["artifacts"]]
    if genuine["request"].document.to_dict()["model_family"] == "linear":
        assert kinds.count("linear_model") == 1
    else:
        assert kinds.count("hgb_model") == 2
        assert all(row["hgb_pair"] for row in report["timeline"])
        assert all(
            row["feature_contributions"] is None
            and row["unavailable"]["feature_contributions"] == "hgb_attribution_not_provided"
            for row in report["timeline"]
        )
    assert d["detailed_report_hash"] == FrozenJSON.freeze(report).identifier
    assert FrozenJSON(genuine["result"].payload_json) == genuine["result"]
    assert (
        not report["pit_verified"]
        and not report["oos_verified"]
        and not report["operational_eligible"]
    )


def test_simultaneous_buys_fills_reentry_and_exact_shared_account(mechanics: FrozenJSON) -> None:
    d = mechanics.to_dict()
    assert d["run"]["status"] == "completed", [
        (e.get("reason"), e.get("blocked"))
        for e in d["run"]["events"]
        if e["status"] != "processed"
    ]
    timeline = d["detailed_report"]["timeline"]
    assert [r["action"] for r in timeline] == ["BUY"] * 5 + ["SELL"] * 5 + ["BUY"] * 5 + [
        "SELL"
    ] * 5
    assert d["performance"]["fill_count"] == 20
    assert d["performance"]["completed_trades"] == 10
    assert all(r["signal_evidence_scope"] == "controlled_mechanics_test_double" for r in timeline)
    assert all(row["reservation"]["status"] == "accepted" for row in timeline)
    assert all(row["executions"][0]["status"] == "filled" for row in timeline)
    assert all(
        row["executions"][0]["source_event"]["event_at"] > row["decision_at"] for row in timeline
    )
    assert sum(
        Decimal(v) for v in d["detailed_report"]["realized_pnl_by_ticker"].values()
    ) == Decimal(d["run"]["final_account"]["realized_pnl_krw"])
    assert all(p["quantity"] == 0 for p in d["run"]["final_account"]["positions"].values())
    assert len({p["ticker"] for p in d["detailed_report"]["positions"]}) == 5
    assert (
        sum(
            p["previous_completed_position_id"] is not None
            for p in d["detailed_report"]["positions"]
        )
        == 5
    )
    assert all(
        p["holding_duration"]["calendar_days"] is not None
        for p in d["detailed_report"]["positions"]
    )
    assert all(
        a["historical_input"]["version"] == 2
        for e in d["run"]["events"]
        for a in e["admissions"]
        if a["status"] == "accepted"
    )
    initial = d["performance"]["initial_equity"]
    assert initial == "1000000"
    assert Decimal(d["performance"]["final_equity"]) == Decimal(initial) + Decimal(
        d["performance"]["ledger_realized_pnl"]
    )


def test_three_pending_then_filled_and_rejections_recorded(limited: FrozenJSON) -> None:
    d = limited.to_dict()
    assert d["performance"]["fill_count"] == 3
    assert len(d["run"]["final_account"]["orders"]) == 3
    timeline = d["detailed_report"]["timeline"]
    assert [r["reservation"]["status"] for r in timeline[:3]] == ["accepted"] * 3
    assert [r["admission"]["reason"] for r in timeline[3:5]] == ["insufficient_cash"] * 2
    assert [r["action"] for r in timeline[5:]] == ["HOLD"] * 3 + ["WAIT"] * 2
    assert all(r["conditions"]["entry"]["code"] == "existing_position" for r in timeline[5:8])
    assert len(d["detailed_report"]["unaccepted_order_requests"]) == 2
    assert all(p["quantity"] == 10 for p in d["run"]["final_account"]["positions"].values())
    points = d["detailed_report"]["account_timeline"]
    assert any(
        p["reserved_cash_krw"] == "3000" and p["available_cash_krw"] == "500" for p in points
    )
    assert all(
        Decimal(p["available_cash_krw"]) >= 0 and Decimal(p["reserved_cash_krw"]) >= 0
        for p in points
    )
    assert all(p["status"] == "still_open" for p in d["detailed_report"]["positions"])


def test_replay_exact_and_permuted_unordered_evidence(
    limited: FrozenJSON, monkeypatch: pytest.MonkeyPatch
) -> None:
    import donghak_stock_vision.backtest.historical as builder

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("replay must not acquire inference")

    monkeypatch.setattr(builder, "SignalService", forbidden)
    source = limited.to_dict()["frozen_input"]
    replay = replay_five_ticker(FrozenJSON.freeze(source))
    assert replay == limited
    # Artifact list order carries no priority; graph resolution preserves economics.
    source["historical_input"]["evidence_table"]["artifacts"].reverse()
    permuted = frozen_runner(FrozenJSON.freeze(source)).run().to_dict()
    assert permuted == limited.to_dict()["run"]


@pytest.mark.parametrize(
    "damage",
    [
        "model",
        "checkpoint",
        "missing_artifact",
        "artifact",
        "decision_policy",
        "execution_policy",
        "valuation",
        "initial_cash",
        "schedule",
        "opportunity_time",
        "analysis",
        "cutoff",
    ],
)
def test_frozen_tamper_fails_closed(limited: FrozenJSON, damage: str) -> None:
    source = limited.to_dict()["frozen_input"]
    if damage == "model":
        source["request"]["model_references"]["dataset_id"] = "0" * 64
    elif damage == "checkpoint":
        source["initial_checkpoint"]["state"]["available_cash_krw"] = "99999"
    elif damage == "missing_artifact":
        source["historical_input"]["evidence_table"]["artifacts"].pop()
    elif damage == "artifact":
        source["historical_input"]["evidence_table"]["artifacts"][0]["payload"]["tampered"] = True
    elif damage == "decision_policy":
        source["decision_policy"]["thresholds"]["buy"] = "0"
    elif damage == "execution_policy":
        source["execution_policy"]["slippage_rate"] = "0"
    elif damage == "valuation":
        source["valuation_policy"]["max_age_seconds"] = 0
    elif damage == "initial_cash":
        source["request"]["initial_cash"] = "9999"
    elif damage == "schedule":
        source["request"]["config"]["decision_dates"] = []
    elif damage == "opportunity_time":
        source["historical_input"]["decision_points"][0]["available_at"] = "2099-01-01T00:00:00Z"
    elif damage == "analysis":
        source["historical_input"]["decision_points"][0]["analysis_id"] = "0" * 64
    else:
        source["request"]["config"]["captured_at"] = "2010-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        frozen_runner(FrozenJSON.freeze(source))


def test_readonly_and_same_file_guards(base: tuple[Path, FiveTickerRequest]) -> None:
    root, request = base
    from donghak_stock_vision.storage.sqlite import SQLiteStore

    writable = SQLiteStore(root / "market.db")
    analysis = ReadOnlyAnalysisStore(root / "analysis.db")
    output = AnalysisStore(root / "guard-output.db")
    try:
        with pytest.raises(ValueError, match="readonly_source_required"):
            run_five_ticker(request, writable, analysis, run_output=output)
        readonly_market = ReadOnlyMarketStore(root / "market.db")
        alias = ReadOnlyAnalysisStore(root / "analysis.db")
        try:
            with pytest.raises(ValueError, match="run_output_must_be_separate"):
                run_five_ticker(request, readonly_market, analysis, run_output=alias)
        finally:
            readonly_market.close()
            alias.close()
    finally:
        writable.close()
        analysis.close()
        output.close()


def test_missing_bar_unrelated_pending_and_stale_portfolio(tmp_path: Path) -> None:
    request, _ = prepared(tmp_path, controlled=True, omit_last=True)
    d = request.document.to_dict()
    d["config"]["valuation_policy"]["max_age_seconds"] = 1
    result = mechanics_run(tmp_path, FiveTickerRequest(FrozenJSON.freeze(d))).to_dict()
    assert result["performance"]["fill_count"] == 19
    report = result["detailed_report"]
    pending = [o for o in report["terminal_orders"] if o["status"] == "open"]
    assert len(pending) == 1 and pending[0]["ticker"] == TICKERS[-1]
    assert pending[0]["reserved_quantity"] == 10
    assert report["final_valuation"]["total_equity"] is None
    assert report["final_valuation"]["positions"][0]["reason"] == "stale_mark"
    assert any(
        p["ticker"] == TICKERS[-1] and p["status"] == "still_open" for p in report["positions"]
    )


def test_explicit_cancel_and_expiry(base: tuple[Path, FiveTickerRequest]) -> None:
    root, request = base
    d = request.document.to_dict()
    first = d["config"]["decision_dates"][0]
    market = ReadOnlyMarketStore(root / "market.db")
    next_day = min(
        b.trading_date.isoformat()
        for b in market.read(TICKERS[0], date.fromisoformat(first), date.fromisoformat(d["end"]))
        if b.trading_date.isoformat() > first
    )
    market.close()
    d["end"] = next_day
    d["config"]["decision_dates"] = [first]
    d["config"]["admission_policy"]["expires_at"] = datetime.combine(
        date.fromisoformat(next_day) + timedelta(days=1), midnight(), SEOUL
    ).isoformat()
    d["config"]["release_steps"] = [
        {"trading_date": next_day, "ticker": TICKERS[0], "status": "cancelled"},
        {"trading_date": next_day, "ticker": TICKERS[1], "status": "expired"},
    ]
    result = mechanics_run(
        root, FiveTickerRequest(FrozenJSON.freeze(d)), output_name="release.db"
    ).to_dict()
    orders = {o["ticker"]: o for o in result["detailed_report"]["terminal_orders"]}
    assert orders[TICKERS[0]]["status"] == "cancelled"
    assert orders[TICKERS[1]]["status"] == "expired"
    assert result["performance"]["fill_count"] == 0
    assert result["run"]["final_account"]["reserved_cash_krw"] == "3000"


def test_missing_opportunity_is_unavailable_and_other_orders_stay_pending(tmp_path: Path) -> None:
    request, _ = prepared(tmp_path, controlled=True, omit_first_decision=True)
    d = request.document.to_dict()
    first = d["config"]["decision_dates"][0]
    d["end"] = first
    d["config"]["decision_dates"] = [first]
    result = mechanics_run(tmp_path, FiveTickerRequest(FrozenJSON.freeze(d))).to_dict()
    timeline = result["detailed_report"]["timeline"]
    assert len(timeline) == 5
    assert timeline[-1]["action"] is None
    assert timeline[-1]["reasons"][0]["code"] == "missing_decision_bar"
    assert timeline[-1]["analysis_id"] is None
    assert result["performance"]["fill_count"] == 0
    assert len(result["run"]["pending"]) == 4
    assert result["run"]["final_account"]["reserved_cash_krw"] == "4000"


def test_checkpoint_reference_mismatch_precedes_acquisition(genuine: dict[str, Any]) -> None:
    if genuine["request"].document.to_dict()["model_family"] != "hgb":
        return
    root = genuine["root"]
    raw = genuine["request"].document.to_dict()
    raw["model_references"]["up"]["checkpoint_sha256"] = "0" * 64
    market = ReadOnlyMarketStore(root / "market.db")
    output = AnalysisStore(root / "wrong-checkpoint.db")
    try:
        with pytest.raises(ValueError, match="hgb_checkpoint_reference_mismatch"):
            run_five_ticker(
                FiveTickerRequest(FrozenJSON.freeze(raw)),
                market,
                None,
                run_output=output,
                hgb_provider=genuine["provider"],
            )
        assert output.all("hgb_analysis") == []
    finally:
        market.close()
        output.close()


def test_owner_validation_cache_rejects_other_tape_and_new_tampered_owner(
    genuine: dict[str, Any],
) -> None:
    from dataclasses import replace

    from donghak_stock_vision.backtest.historical_execution import validate_input

    source = genuine["result"].to_dict()["frozen_input"]
    runner = frozen_runner(FrozenJSON.freeze(source))
    owner = runner.historical_input
    assert owner is not None
    assert validate_input(runner.tape, owner) is validate_input(runner.tape, owner)
    other = replace(runner.tape, sources=tuple(reversed(runner.tape.sources)))
    with pytest.raises(ValueError, match="historical_tape_mismatch"):
        validate_input(other, owner)
    tampered = owner.to_dict()
    tampered["evidence_table"]["artifacts"].pop()
    with pytest.raises(ValueError):
        validate_input(runner.tape, FrozenJSON.freeze(tampered))


def test_one_snapshot_market_mismatch_does_not_hide_other_four(tmp_path: Path) -> None:
    from dataclasses import replace

    from donghak_stock_vision.data.schema import DailyBar

    request, _ = prepared(tmp_path)
    raw = request.document.to_dict()
    raw["config"]["decision_dates"] = raw["config"]["decision_dates"][:1]

    class RevisedFixtureMarket(ReadOnlyMarketStore):
        def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
            rows = super().read(ticker, start, end)
            if ticker == TICKERS[-1] and rows:
                rows[0] = replace(rows[0], volume=rows[0].volume + 1)
            return rows

    market = RevisedFixtureMarket(tmp_path / "market.db")
    analysis = ReadOnlyAnalysisStore(tmp_path / "analysis.db")
    output = AnalysisStore(tmp_path / "mismatch-output.db")
    try:
        result = run_five_ticker(
            FiveTickerRequest(FrozenJSON.freeze(raw)), market, analysis, run_output=output
        ).to_dict()
        timeline = result["detailed_report"]["timeline"]
        assert all(row["analysis_id"] is not None for row in timeline[:4])
        assert timeline[-1]["analysis_id"] is None
        assert timeline[-1]["reasons"][0]["code"] == "model_snapshot_market_mismatch"
        assert result["run"]["status"] == "completed"
    finally:
        market.close()
        analysis.close()
        output.close()
