"""Genuine fitted/scored SYNTHETIC mechanics; never real performance or PIT evidence."""

from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape, capture_market
from donghak_stock_vision.backtest.ledger import initialize
from donghak_stock_vision.backtest.performance import calculate_performance
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.service import freeze_request, run_backtest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import TrainingService
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import (
    AnalysisStore,
    ReadOnlyAnalysisStore,
    ResearchRunStore,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from donghak_stock_vision.strategy.engine import decide
from tests.backtest.helpers import manifest, payload, policy
from tests.backtest.test_decision_bridge import scenario
from tests.backtest.test_execution import execution_policy
from tests.backtest.test_ledger import command
from tests.backtest.test_ledger import policy as ledger_policy
from tests.backtest.test_performance import valuation_policy
from tests.learning.helpers import CUTOFF, bars, quality, seed
from tests.strategy.helpers import policy as decision_policy


def scored_runner(path: Path) -> BacktestRunner:
    pytest.importorskip("sklearn")
    market = SQLiteStore(path / "market.db")
    source = AnalysisStore(path / "source.db")
    # Fixed seed and unchanged TrainingService. No scores or fitted parameters are replaced.
    rows = [replace(b, ticker="005930" if b.ticker == "000001" else b.ticker) for b in bars()]
    seed(market, rows)
    q = quality(rows)
    snapshot = capture(
        market,
        ["005930", "000002"],
        rows[0].trading_date,
        max(b.trading_date for b in rows),
        CUTOFF,
        "historical_research",
        quality=q,
        synthetic=True,
    )
    sid = source.put("snapshot", snapshot)
    fitted = TrainingService(source).train(sid)
    assert fitted["status"] == "trained"
    mid = fitted["model_version"]
    source.close()
    before = (path / "source.db").read_bytes()
    reader = ReadOnlyAnalysisStore(path / "source.db")
    output = AnalysisStore(path / "run.db")
    run_store = ResearchRunStore(reader, output)
    # Fixed mechanics window and policy, chosen for contexts, not profitable held-out returns.
    selected = [b for b in rows if b.ticker == "005930"][100:110]
    indices = (0, 2, 3, 7, 9)
    sources = capture_market(
        market,
        ["005930"],
        selected[0].trading_date,
        selected[-1].trading_date,
        information_mode="historical_research",
        data_origin="synthetic",
        captured_at=CUTOFF,
        quality=FrozenJSON.freeze(q),
    )
    market.close()
    stamps = [
        datetime.combine(b.trading_date + timedelta(days=1), time(), SEOUL).isoformat()
        for b in selected
    ]
    signals = {
        i: SignalService(run_store).research(mid, sid, ["005930"], selected[i].trading_date)[0]
        for i in indices
    }
    assert all(s["input_status"] == "ok" for s in signals.values())
    m = manifest().to_dict()
    provenance = {k: m[k] for k in ("information_mode", "data_origin", "usage_restriction")}

    def ref(identifier: str) -> dict[str, Any]:
        return {
            **provenance,
            "artifact_id": identifier,
            "content_hash": identifier,
            "version": "synthetic-genuine-scoring-v1",
        }

    plan = FrozenJSON.freeze(
        {
            **provenance,
            "releases": [
                {
                    "sequence": i + 1,
                    "previous_revision": None,
                    "field_times": {k: stamps[i] for k in s.to_dict()["fields"]},
                }
                for i, s in enumerate(sources)
            ],
            "analysis_releases": [
                {
                    "model_id": mid,
                    "snapshot_id": sid,
                    "analysis_id": s["analysis_id"],
                    "available_at": stamps[i],
                    "sequence": i + 1,
                }
                for i, s in signals.items()
            ],
        }
    )
    ep = policy().to_dict()
    ep["availability_policy"]["content_hash"] = plan.identifier
    tape_policy = ExecutionPolicy.from_dict(ep)
    m.update(
        start_at=stamps[0],
        end_at=stamps[-1],
        execution_policy_id=tape_policy.identifier,
        availability_assumption_id=plan.identifier,
        market_snapshots=[ref(s.identifier) for s in sources] + [ref(sid)],
        models=[ref(mid)],
        analyses=[ref(s["analysis_id"]) for s in signals.values()],
        quality_manifest=ref(FrozenJSON.freeze(q).identifier),
    )
    m["initial_account"].update(
        available_cash_krw="1000000", observed_at=stamps[0], received_at=stamps[0]
    )
    run_manifest = RunManifest.from_dict(m)
    events = []
    for i, s in enumerate(sources):
        original = s.to_dict()
        e = payload(MarketEvent)
        e.update(
            {
                k: original[k]
                for k in (
                    "ticker",
                    "trading_date",
                    "revision",
                    "source_received_at",
                    "adjustment",
                    "market",
                )
            }
        )
        e.update(
            run_id=run_manifest.identifier,
            sequence=i + 1,
            event_at=stamps[i],
            available_at=stamps[i],
            quality_available_at=stamps[i],
            source=ref(s.identifier),
            public_fields=original["fields"],
            quality_evidence_ids=[FrozenJSON.freeze(q).identifier],
            availability_assumption_id=plan.identifier,
        )
        events.append(MarketEvent.from_dict(e))
    tape = FrozenTape(run_manifest, tape_policy, tuple(events), sources, plan, FrozenJSON.freeze(q))
    analyses = tuple(
        FrozenAnalysis.capture(
            run_store,
            run_manifest,
            plan,
            model_id=mid,
            snapshot_id=sid,
            analysis_id=s["analysis_id"],
        )
        for s in signals.values()
    )
    dp = decision_policy()
    dp.update(
        allowed_models=[mid],
        session_basis="verified_sessions",
        # Explicit zero thresholds exercise context-driven mechanics, not model quality.
        thresholds={"buy": "0", "sell": "0"},
    )
    dp["allowed_quality_flags"] = sorted(
        dp["allowed_quality_flags"] + ["quality_review_may_be_retrospective"]
    )
    dp["ttl_seconds"].update(analysis=86400, max_skew=86400)
    dp["sizing"]["buy_budget_krw"] = "100000"
    dp["costs"]["buy"].update(slippage_rate="0.01", fee_rate="0.002")
    dp["limits"]["max_order_notional_krw"] = "200000"
    steps = []
    for i, signal in signals.items():
        ap = scenario()["policy"]
        ap.update(
            order_budget_krw="200000",
            max_order_quantity=20,
            max_position_quantity={"005930": 20},
            max_position_notional_krw={"005930": "200000"},
            marks={"005930": {"sequence": i + 1, "field": "close"}},
        )
        common = {
            "origin": "virtual",
            "snapshot_version": "synthetic-mechanics-v1",
            "observed_at": stamps[i],
            "received_at": stamps[i],
        }
        steps.append(
            {
                "sequence": i + 1,
                "ticker": "005930",
                "analysis_id": signal["analysis_id"],
                "market": {
                    **common,
                    "ticker": "005930",
                    "currency": "KRW",
                    "tradable": True,
                    "session": "open",
                    "listing_status": "listed",
                    "quality_status": "verified",
                    "market": "KOSPI",
                    "adjustment": "unadjusted",
                    "reference_price_krw": str(selected[i].close),
                    "price_observed_at": stamps[i],
                },
                "admission_policy": ap,
                "reservation": {
                    "BUY": {"max_notional_krw": "99000", "max_cost_krw": "1000"},
                    "SELL": {"max_notional_krw": "0", "max_cost_krw": "0"},
                },
            }
        )
    initial = initialize(
        run_manifest,
        tape.identifier,
        ledger_policy(),
        command("initialize", 0, {}, effective=stamps[0], known=stamps[0]),
        cutoff=stamps[0],
    )
    reader.close()
    output.close()
    assert (path / "source.db").read_bytes() == before
    runner = BacktestRunner(
        tape,
        analyses,
        initial,
        DecisionPolicy.from_dict(dp),
        execution_policy(),
        FrozenJSON.freeze(
            {
                "run_id": run_manifest.identifier,
                "start_at": stamps[0],
                "end_at": stamps[-1],
                "tickers": ["005930"],
                "event_clock": "available_at",
                "history_mode": "verified_execution",
                "decision_steps": steps,
            }
        ),
    )

    # Plan fixture reservation ceilings from the public engine's current-price estimate.
    # Never inspect future execution prices or resize an admitted order.
    for step, analysis in zip(steps, analyses, strict=True):
        clock = VirtualClock.at(step["market"]["received_at"], step["sequence"])
        estimate = decide(
            runner.project(initial, step, analysis, clock), runner.decision_policy
        ).to_dict()
        if estimate["action"] == "BUY":
            cost = estimate["estimated_cost"]
            step["reservation"]["BUY"] = {
                "max_notional_krw": cost["notional"],
                "max_cost_krw": str(Decimal(cost["fee"]) + Decimal(cost["tax"])),
            }
    return replace(
        runner, config=FrozenJSON.freeze({**runner.config.to_dict(), "decision_steps": steps})
    )


def test_genuine_public_scoring_synthetic_lifecycle_and_replay(tmp_path: Path) -> None:
    runner = scored_runner(tmp_path)
    run = runner.run()
    d = run.to_dict()
    actions = [x["result"]["action"] for e in d["events"] for x in e["decisions"]]
    assert actions == ["BUY", "HOLD", "SELL", "BUY", "HOLD"], d
    assert len(d["final_account"]["fills"]) == 3
    assert d["position_history"]["complete"] is True
    assert len(d["position_history"]["last_exits"]) == 1
    assert all(a.bundle.to_dict()["signal"]["reasons"] for a in runner.analyses)
    assert all(a.bundle.to_dict()["signal"]["data_origin"] == "synthetic" for a in runner.analyses)
    performance = calculate_performance(run, runner.tape, valuation_policy())
    p = performance.to_dict()
    assert p["completed_trades"] == 1 and p["fill_count"] == 3
    assert p["unrealized_pnl"] is not None
    request = freeze_request(runner, valuation_policy())
    replay, again, report = run_backtest(
        request, start=d["start_at"], end=d["end_at"], initial_cash="1000000", tickers=["005930"]
    )
    assert replay == run and again == performance
    assert "SYNTHETIC" in report
    # Genuine feature values and fitted model weights are preserved in factual evidence.
    assert all(
        len(
            a.bundle.to_dict()["signal"]["reasons"][
                "up" if a.bundle.to_dict()["signal"]["context"] == "prior_decline" else "down"
            ]["contributions"]
        )
        == 8
        for a in runner.analyses
    )
