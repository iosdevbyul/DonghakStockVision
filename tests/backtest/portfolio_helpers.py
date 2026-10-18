"""Bounded five-security genuine inference fixtures. Never real market evidence."""

from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.hgb_provider import HGBHistoricalProvider
from donghak_stock_vision.backtest.historical import ASSUMPTION
from donghak_stock_vision.backtest.historical_execution import MODE
from donghak_stock_vision.backtest.portfolio import FiveTickerRequest, run_five_ticker
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from donghak_stock_vision.data.learning import canonical, event_contract
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.inference import ResearchPolicy
from donghak_stock_vision.models.service import TrainingService
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.signals.domain import SignalPolicy
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.backtest.helpers import manifest, policy
from tests.backtest.test_decision_bridge import scenario
from tests.backtest.test_execution import execution_policy
from tests.backtest.test_hgb_trading import genuine_resolver
from tests.backtest.test_ledger import policy as ledger_policy
from tests.backtest.test_performance import valuation_policy
from tests.learning.helpers import CUTOFF, bars, quality
from tests.strategy.helpers import inputs
from tests.strategy.helpers import policy as decision_policy

TICKERS = ("000075", "00088K", "00104K", "005930", "035420")


def prepared(
    root: Path,
    family: str = "linear",
    *,
    controlled: bool = False,
    omit_last: bool = False,
    omit_first_decision: bool = False,
) -> tuple[FiveTickerRequest, HGBHistoricalProvider | None]:
    root.mkdir(parents=True, exist_ok=True)
    base = bars(count=160, tickers=1)
    if controlled:
        base = [
            replace(
                b,
                open=(90 if i in (101, 108) else 110 if i in (104, 110) else 100),
                high=120,
                low=80,
                close=100,
            )
            if 88 <= i <= 110
            else b
            for i, b in enumerate(base)
        ]
    rows = [replace(b, ticker=t, provider="fake") for t in TICKERS for b in base]
    if omit_first_decision:
        rows = [
            b
            for b in rows
            if not (b.ticker == TICKERS[-1] and b.trading_date == base[100].trading_date)
        ]
    if omit_last:
        rows = [
            b
            for b in rows
            if not (b.ticker == TICKERS[-1] and b.trading_date == base[110].trading_date)
        ]
    market = SQLiteStore(root / "market.db")
    for ticker in TICKERS:
        selected_rows = [b for b in rows if b.ticker == ticker]
        raw = RawPage(
            canonical([b.to_dict() for b in selected_rows]).encode(), selected_rows[0].collected_at
        )
        rid = market.archive("fixture", selected_rows[0].provider, ticker, raw)
        market.save(selected_rows, [rid])
    q = quality(rows)
    source = AnalysisStore(root / "analysis.db")
    snapshot = capture(
        market,
        list(TICKERS),
        base[0].trading_date,
        base[-1].trading_date,
        CUTOFF,
        "historical_research",
        quality=q,
        synthetic=True,
    )
    sid = source.put("snapshot", snapshot)
    trained = TrainingService(source).train(sid)
    assert trained["status"] == "trained"
    mid = trained["model_version"]
    model = source.get("model", mid)
    refs: dict[str, Any] = {
        "model_id": mid,
        "training_snapshot_id": sid,
        "dataset_id": model["dataset_id"],
    }
    provider = None
    if family == "hgb":
        resolver = genuine_resolver(root / "trusted")
        provider = HGBHistoricalProvider(
            resolver,
            SignalPolicy(
                "portfolio-mechanics",
                ResearchPolicy("portfolio-mechanics", frozenset(snapshot["quality_flags"])),
            ),
            CUTOFF,
        )
        mid = provider.model_id
        refs = {
            name: {"fingerprint": a.fingerprint, "checkpoint_sha256": a.checkpoint_sha256}
            for name, a in zip(("up", "down"), resolver.artifacts, strict=True)
        }
    dp = decision_policy()
    dp.update(
        allowed_models=[mid],
        session_basis="verified_sessions",
        effective_from="2020-01-01T00:00:00Z",
        effective_until="2025-01-01T00:00:00Z",
    )
    dp["costs"].update(effective_from=dp["effective_from"], effective_until=dp["effective_until"])
    # Explicit mechanics policy fixed before inference; never tune to measured returns.
    dp["ttl_seconds"].update(analysis=86400, max_skew=86400)
    dp["allowed_quality_flags"] = sorted(
        set(dp["allowed_quality_flags"] + snapshot["quality_flags"])
    )
    dp["sizing"]["buy_budget_krw"] = "100000"
    dp["costs"]["buy"].update(slippage_rate="0.01", fee_rate="0.002")
    dp["limits"].update(
        max_order_notional_krw="200000",
        max_positions=5,
        max_position_weight="1",
        max_gross_weight="1",
    )
    if controlled:
        dp["sizing"]["buy_budget_krw"] = "1000"
        dp["costs"]["buy"].update(slippage_rate="0", fee_rate="0")
    ap = scenario()["policy"]
    ap.update(
        order_budget_krw="200000",
        max_order_quantity=20,
        max_position_quantity={t: 20 for t in TICKERS},
        max_position_notional_krw={t: "200000" for t in TICKERS},
        ordered_tickers=list(TICKERS),
        marks={},
    )
    m = manifest().to_dict()
    m.update(
        information_mode="historical_research",
        data_origin="synthetic",
        usage_restriction="synthetic_test_only",
    )
    m["initial_account"].update(positions=[])
    m["calendar"].update(data_origin="synthetic", usage_restriction="synthetic_test_only")
    m["universe"].update(data_origin="synthetic", usage_restriction="synthetic_test_only")
    for key in ("market_snapshots", "models", "analyses"):
        m[key] = [
            {**r, "data_origin": "synthetic", "usage_restriction": "synthetic_test_only"}
            for r in m[key]
        ]
    m["quality_manifest"].update(data_origin="synthetic", usage_restriction="synthetic_test_only")
    selected = (100, 103, 107, 109) if controlled else (100, 103)
    c = {
        "manifest": m,
        "policy": policy().to_dict(),
        "captured_at": CUTOFF.isoformat(),
        "quality": q,
        "availability_assumption": ASSUMPTION,
        "decision_policy": dp,
        "execution_policy": execution_policy(liquidity=MODE).to_dict(),
        "ledger_policy": ledger_policy().document.to_dict(),
        "valuation_policy": valuation_policy(historical_marks="verified_daily_research").to_dict(),
        "admission_policy": ap,
        "reservation_by_side": {
            "BUY": {"max_notional_krw": "95000", "max_cost_krw": "5000"},
            "SELL": {"max_notional_krw": "0", "max_cost_krw": "0"},
        },
        "decision_dates": [base[i].trading_date.isoformat() for i in selected],
        "decision_mark_field": "close",
        "release_steps": [],
    }
    if controlled:
        c["reservation_by_side"]["BUY"] = {"max_notional_krw": "950", "max_cost_krw": "50"}
    request = FiveTickerRequest(
        FrozenJSON.freeze(
            {
                "version": "five_ticker_historical_request_v1",
                "tickers": list(TICKERS),
                "start": base[88].trading_date.isoformat(),
                "end": base[110].trading_date.isoformat(),
                "initial_cash": "1000000",
                "model_family": family,
                "model_id": mid,
                "model_references": refs,
                "market": "KRX_KOSPI",
                "calendar": {"session_basis": "verified_sessions", "research_calendar": None},
                "evidence_scope": "synthetic_fixture",
                "config": c,
            }
        )
    )
    source.close()
    market.close()
    return request, provider


def execute_fixture(
    root: Path,
    request: FiveTickerRequest,
    provider: HGBHistoricalProvider | None = None,
    *,
    output_name: str = "run.db",
) -> FrozenJSON:
    market = ReadOnlyMarketStore(root / "market.db")
    analysis = (
        ReadOnlyAnalysisStore(root / "analysis.db")
        if request.document.to_dict()["model_family"] == "linear"
        else None
    )
    output = AnalysisStore(root / output_name)
    try:
        return run_five_ticker(request, market, analysis, run_output=output, hgb_provider=provider)
    finally:
        market.close()
        if analysis is not None:
            analysis.close()
        output.close()


class MechanicsResearch(SignalService):
    """Controlled public-boundary test double, explicitly not a fitted-model output."""

    buy_days: list[str] = []
    contexts: dict[str, str] = {}

    def research(
        self, version: str, snapshot_id: str, tickers: list[str], anchor_date: Any
    ) -> list[dict[str, Any]]:
        model = self.store.get("model", version)
        buying = anchor_date.isoformat() in self.buy_days
        context = self.contexts.get(
            anchor_date.isoformat(), "prior_decline" if buying else "prior_rise"
        )
        a = inputs(0, context, 0.8)["analysis"]
        a.update(
            ticker=tickers[0],
            model_version=version,
            snapshot_id=snapshot_id,
            mode="historical_research",
            data_origin=model["data_origin"],
            usage_restriction=model["usage_restriction"],
            model_created_at=self.store.created_at("model", version).isoformat(),
            snapshot_as_of=self.store.get("snapshot", snapshot_id)["cutoff"],
            anchor_at=datetime.combine(anchor_date, time(16), SEOUL).isoformat(),
            last_trading_date=anchor_date.isoformat(),
            quality_flags=model["quality_flags"],
            reasons={
                "fixture": {
                    "kind": "controlled_mechanics_test_double",
                    "not_model_prediction": True,
                }
            },
            **event_contract("verified_sessions"),
        )
        return [{**a, "analysis_id": self.store.put("signal", a)}]


def mechanics_run(
    root: Path,
    request: FiveTickerRequest,
    *,
    output_name: str = "mechanics.db",
    contexts: dict[str, str] | None = None,
) -> FrozenJSON:
    import donghak_stock_vision.backtest.historical as builder

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            MechanicsResearch,
            "buy_days",
            request.document.to_dict()["config"]["decision_dates"][::2],
        )
        patch.setattr(MechanicsResearch, "contexts", contexts or {})
        patch.setattr(builder, "SignalService", MechanicsResearch)
        return execute_fixture(root, request, output_name=output_name)
