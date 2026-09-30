from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.historical import build_historical_input
from donghak_stock_vision.backtest.historical_execution import validate_input
from donghak_stock_vision.backtest.performance import calculate_performance
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from tests.backtest.test_historical import setup_inputs
from tests.backtest.test_historical_execution import historical_runner
from tests.backtest.test_performance import valuation_policy


def test_all_tickers_decision_points_and_identity(tmp_path: Path) -> None:
    store, kwargs = setup_inputs(tmp_path / "market.db")
    try:
        original = build_historical_input(store, **kwargs)
        policy = ResearchCalendarPolicy((date(2020, 1, 3),))
        frozen = build_historical_input(store, **kwargs, research_calendar=policy)
        assert frozen.identifier != original.identifier
        assert frozen.tape.identifier != original.tape.identifier
        assert build_historical_input(store, **kwargs, research_calendar=policy) == frozen
        assert all(e.to_dict()["trading_date"] != "2020-01-03" for e in frozen.tape.events)
        assert all(
            p["trading_date"] != "2020-01-03" for p in frozen.document.to_dict()["decision_points"]
        )
        validate_input(frozen.tape, frozen.document)
        # Even an exclusion without a row changes research identity through quality hash.
        other = build_historical_input(
            store, **kwargs, research_calendar=ResearchCalendarPolicy((date(2019, 1, 1),))
        )
        assert other.tape.identifier != original.tape.identifier
        assert store.read("000001", date(2020, 1, 3), date(2020, 1, 3))
    finally:
        store.close()


def test_excluded_period_boundary_rejected(tmp_path: Path) -> None:
    store, kwargs = setup_inputs(tmp_path / "market.db")
    try:
        with pytest.raises(ValueError, match="research_boundary"):
            build_historical_input(
                store, **kwargs, research_calendar=ResearchCalendarPolicy((kwargs["end"],))
            )
    finally:
        store.close()


def test_next_retained_session_fill_and_valuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = historical_runner(
        tmp_path,
        monkeypatch,
        decisions=(1,),
        session_prices=(100, 90, 90, 110),
        research_calendar=ResearchCalendarPolicy((date(2026, 1, 12),)),
    )
    assert runner.historical_input is not None
    assert [e.to_dict()["trading_date"] for e in runner.tape.events] == [
        "2026-01-09",
        "2026-01-13",
        "2026-01-14",
    ]
    run = runner.run()
    assert runner.run() == run
    d = run.to_dict()
    assert len(d["final_account"]["fills"]) == 1
    assert all(e.to_dict()["trading_date"] != "2026-01-12" for e in runner.tape.events)
    p = calculate_performance(
        run,
        runner.tape,
        valuation_policy(historical_marks="verified_daily_research"),
        historical_input=runner.historical_input,
    ).to_dict()
    assert p["fill_count"] == 1
    # No excluded bar's publication point and no marks sourced from excluded sessions.
    assert all(
        point["timestamp"] != "2026-01-12T15:00:00.000000+00:00" for point in p["equity_curve"]
    )
    assert all(
        item["trading_date"] != "2026-01-12"
        for item in runner.tape.view(VirtualClock.at("2026-01-15T00:00:00+09:00", 3)).to_dict()[
            "items"
        ]
    )
    assert FrozenTape.from_json(runner.tape.to_json()) == runner.tape


def test_forged_tape_cannot_reintroduce_excluded_bar(tmp_path: Path) -> None:
    store, kwargs = setup_inputs(tmp_path / "market.db")
    try:
        frozen = build_historical_input(store, **kwargs)
        q = frozen.tape.quality.to_dict()
        q["research_calendar"] = ResearchCalendarPolicy((date(2020, 1, 3),)).to_dict()
        # Quality hash and policy checks both fail closed on changed evidence.
        with pytest.raises(ValueError):
            replace(frozen.tape, quality=FrozenJSON.freeze(q))
    finally:
        store.close()


def test_policy_mismatch_blocks_all_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.contracts import RunManifest
    from donghak_stock_vision.backtest.historical import ASSUMPTION
    from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
    from donghak_stock_vision.storage.analysis import AnalysisStore

    r = historical_runner(tmp_path, monkeypatch, decisions=(1,))
    assert r.historical_input is not None
    old = r.historical_input.to_dict()
    market, analysis = (
        ReadOnlyMarketStore(tmp_path / "market.db"),
        AnalysisStore(tmp_path / "analysis.db"),
    )
    try:
        frozen = build_historical_input(
            market,
            start=date(2026, 1, 9),
            end=date(2026, 1, 14),
            tickers=["005930"],
            captured_at=old["source_metadata"]["captured_at"],
            quality=FrozenJSON.freeze(old["source_metadata"]["quality"]),
            manifest_template=RunManifest.from_dict(r.tape.manifest.to_dict()),
            policy=r.tape.policy,
            availability_assumption=ASSUMPTION,
            analysis_store=analysis,
            model_id=old["requested_model_id"],
            research_calendar=ResearchCalendarPolicy((date(2026, 1, 12),)),
        )
        assert not frozen.analyses
        assert all(
            p["reason"] == "research_calendar_mismatch"
            for p in frozen.document.to_dict()["decision_points"]
        )
    finally:
        market.close()
        analysis.close()


def test_excluded_valuation_call_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from donghak_stock_vision.backtest.performance import policy_values, valuation

    r = historical_runner(
        tmp_path,
        monkeypatch,
        decisions=(1,),
        session_prices=(100, 90, 90, 110),
        research_calendar=ResearchCalendarPolicy((date(2026, 1, 12),)),
    )
    with pytest.raises(ValueError, match="known_incomplete"):
        valuation(
            r.initial,
            r.tape,
            VirtualClock.at("2026-01-13T00:00:00+09:00", 1),
            policy_values(valuation_policy(historical_marks="verified_daily_research")),
            "event",
        )


def test_historical_service_propagates_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.backtest.test_historical_service import execute, setup_request

    # Exclude a decision date, not merely a fill date; no HOLD/WAIT is manufactured.
    policy = ResearchCalendarPolicy((date(2026, 1, 13),))
    request = setup_request(tmp_path, monkeypatch, research_calendar=policy)
    result = execute(request, tmp_path).document.to_dict()
    assert result["request"]["config"]["research_calendar"] == policy.to_dict()
    assert all(
        p["trading_date"] != "2026-01-13" for p in result["historical_input"]["decision_points"]
    )
    assert "research_sessions_excluded_not_holidays" in result["limitations"]
    assert execute(request, tmp_path).document.to_dict() == result
