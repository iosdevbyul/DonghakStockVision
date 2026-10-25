"""Read-only retrospective SMA projection; no forward eligibility or persistence."""

from collections import Counter
from datetime import date
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import CalendarMonthSource
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.storage.market_analytics import analyze_market_data, validate_access
from donghak_stock_vision.strategy.forward_reference import calculate, parameters, reference
from donghak_stock_vision.trading.domain import Currency, Exchange, Instrument, Market

MODE = "RETROSPECTIVE_HISTORICAL_ANALYSIS"


def validate_sma_access(
    ticker: str,
    start: date,
    end: date,
    split: ExplicitResearchSplit,
    split_pin: str,
    partition: str,
    fast: int,
    slow: int,
    max_rows: int = 1250,
    market: str = "KOSPI",
) -> None:
    # All partition/parameter guards precede source and calendar payload I/O.
    validate_access(ticker, start, end, split, split_pin, max_rows)
    require(partition in ("training", "tuning"), "historical_sma_partition_forbidden")
    span = split.training if partition == "training" else split.validation
    require(span.start <= start <= end <= span.end, "outside_declared_analysis_partition")
    parameters(fast, slow)
    instrument = Instrument(ticker, Exchange(market), Currency.KRW)
    require(instrument.market is Market.KR, "historical_sma_requires_korean_equity")


def analyze_historical_sma(
    database: Path,
    ticker: str,
    start: date,
    end: date,
    *,
    expected_sha256: str,
    split: ExplicitResearchSplit,
    split_pin: str,
    partition: str,
    data_origin: str,
    fast: int,
    slow: int,
    max_rows: int = 1250,
    market: str = "KOSPI",
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> FrozenJSON:
    validate_sma_access(
        ticker, start, end, split, split_pin, partition, fast, slow, max_rows, market
    )
    owner = analyze_market_data(
        database,
        ticker,
        start,
        end,
        expected_sha256=expected_sha256,
        split=split,
        split_pin=split_pin,
        data_origin=data_origin,
        market=market,
        view="history",
        max_rows=max_rows,
        calendars=calendars,
    )
    source = owner.to_dict()
    require(not source["quality"]["issues"], "historical_sma_invalid_or_duplicate_observations")
    require(
        not source["quality"]["off_session_observations"], "historical_sma_off_session_observations"
    )
    require(
        not source["observations"] or source["price_semantics"] == ["unadjusted"],
        "historical_sma_requires_unadjusted_prices",
    )
    strategy = reference()  # Shared mathematics and exact source hash; not forward authorization.
    rows = []
    closes: list[int] = []
    for entry in source["observations"]:
        bar = entry["ohlcv"]
        closes.append(bar["close"])
        # Only the current prefix enters the original bounded calculator. No pre-range reads.
        calculation = calculate(tuple(closes[-(slow + 1) :]), fast, slow).to_dict()
        indicators = calculation["indicators"]
        rows.append(
            {
                "trading_date": entry["date"],
                "close": bar["close"],
                "fast_sma": indicators["current_fast"] if indicators is not None else None,
                "slow_sma": indicators["current_slow"] if indicators is not None else None,
                "crossover_evidence": indicators,
                "signal": calculation["signal"],
                "reason": calculation["reason"],
                "warm_up_only": calculation["signal"] == "INSUFFICIENT_DATA",
                "source_classification": source["data_origin"],
                "source_ohlcv_fingerprint": FrozenJSON.freeze(bar).identifier,
                "source_retrieved_at": bar["collected_at"],
                "strategy_id": strategy["strategy_id"],
                "strategy_version": strategy["strategy_version"],
                "historical_decision_at": None,
                "publication_at": None,
            }
        )
    counts = Counter(r["signal"] for r in rows)
    result = FrozenJSON.freeze(
        {
            "version": "historical_sma_analysis_v1",
            "analysis_mode": MODE,
            "symbol": ticker,
            "market": market,
            "currency": "KRW",
            "selected_period": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "inclusive": True,
            },
            "strategy": strategy,
            "parameters": {"fast": fast, "slow": slow},
            "warm_up_policy": "selected_window_only_require_slow_plus_one_before_any_indicator",
            "minimum_observations": slow + 1,
            "observation_count": len(rows),
            "warm_up_count": counts["INSUFFICIENT_DATA"],
            "signal_counts": {
                key: counts[key] for key in ("BUY", "SELL", "HOLD", "INSUFFICIENT_DATA")
            },
            "status": "empty"
            if not rows
            else "insufficient_data"
            if len(rows) < slow + 1
            else "analyzed",
            "source": {
                "classification": source["data_origin"],
                "classification_assurance": source["origin_assurance"],
                "provider": "krx",
                "price_semantics": source["price_semantics"],
                "database_sha256": source["database_sha256"],
                "query_fingerprint": owner.identifier,
            },
            "research_scope": {
                "original_split_pin": split_pin,
                "partition": partition,
                "final_test_payload_accessed": False,
                "oos_approved": False,
                "historical_pit_qualified": False,
            },
            "quality": source["quality"],
            "limits": source["limits"],
            "observations": rows,
            "limitations": [
                "retrospective_calculation_not_contemporaneously_recorded_decisions",
                "database_presence_and_retrieval_time_do_not_prove_historical_availability",
                "observed_bars_not_assumed_consecutive_exchange_sessions",
                "no_missing_price_interpolation_or_pre_range_warm_up",
                "unadjusted_prices_can_contain_corporate_action_discontinuities",
                "no_orders_positions_returns_profitability_or_backtest_claim",
                "no_forward_journal_analysis_store_or_decision_store_writes",
            ],
        }
    )
    require(len(result.payload_json.encode()) <= 4 * 1024 * 1024, "historical_sma_output_limit")
    return result
