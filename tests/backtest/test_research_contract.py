"""All financial values below are synthetic contract fixtures, not policy recommendations."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest

from donghak_stock_vision.backtest.contracts import VirtualFill
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.entry_contract import (
    EntryStatus,
    EntryWindow,
    IntentPurpose,
    ObservedSession,
    SignalChronology,
    TradeIntent,
    assess_entry,
    validate_sessions,
)
from donghak_stock_vision.backtest.research_contract import (
    BacktestContract,
    Fill,
    OrderSide,
    PositionSide,
    ResultProvenance,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.schema import SEOUL, DailyBar

H = "a" * 64


def ref(name: str) -> dict[str, Any]:
    return {"artifact_id": name, "version": "fixture-v1", "content_hash": H}


def config() -> dict[str, Any]:
    return {
        "contract_version": "research_backtest_v1",
        "scope": "historical_research",
        "start": "2025-01-01",
        "end": "2025-01-31",
        "execution": {
            "version": "fixture-v1",
            "timing": "next_observed_session_open",
            "availability": "complete_daily_bar_next_seoul_midnight",
            "future_access": "simulator_only",
            "fill_type": "reuse_virtual_fill_v1",
        },
        "entry": {
            "version": "fixture-v1",
            "reference_price": "open",
            "attempt_policy": "immediate_next_observed_only",
            "invalid_next_session": "unfilled_no_retry",
        },
        "exit": {
            "version": "fixture-v1",
            "maximum_holding_sessions": 7,
            "target_rule": {"mode": "disabled", "specification": None},
            "stop_rule": {"mode": "disabled", "specification": None},
            "time_exit_rule": ref("unimplemented-time-exit"),
            "reference_price": "open",
            "collision": "reject_as_ambiguous",
            "holding_count_basis": "entry_session_is_one",
        },
        "costs": {
            "version": "fixture-v1",
            "basis": "executed_notional",
            "rounding": {"quantum": "1", "mode": "half_up"},
            "schedule": [
                {
                    "start": "2025-01-01",
                    "end": "2025-01-31",
                    "buy_fee_rate": "0.001",
                    "sell_fee_rate": "0.001",
                    "sell_tax_rate": "0.002",
                    "sell_market_levy_rate": "0",
                    "minimum_fee": {"mode": "disabled", "amount_krw": None},
                }
            ],
        },
        "slippage": {
            "version": "fixture-v1",
            "method": "adverse_proportional",
            "rate": "0.001",
            "rounding": {"quantum": "1", "mode": "adverse"},
        },
        "sizing": {
            "version": "fixture-v1",
            "initial_capital_krw": "10000",
            "policy": ref("unimplemented-sizing"),
            "maximum_concurrent_positions": 2,
            "cash_availability": "available_after_reservations",
            "leverage": "1",
            "short_permission": False,
            "fractional_shares": False,
        },
        "liquidity": {
            "version": "fixture-v1",
            "minimum_volume": 1,
            "minimum_trading_value_krw": "1",
            "maximum_participation_rate": "0.01",
            "rejection": "unfilled_no_retry",
            "assumption": "daily_bar_does_not_prove_fill",
        },
        "quality": {
            "version": "fixture-v1",
            "allowed_research_flags": [],
            "segment_validation": "existing_check_segment_required",
            "corporate_actions": "reject_unusable_segment_no_adjustment",
        },
    }


def session(day: date, sequence: int) -> ObservedSession:
    return ObservedSession(
        "005930",
        day,
        sequence,
        datetime.combine(day, time(9), SEOUL),
        datetime.combine(day, time(15, 30), SEOUL),
        datetime.combine(day + timedelta(days=1), time.min, SEOUL),
    )


def window() -> EntryWindow:
    sessions = tuple(session(date(2025, 1, d), i) for i, d in enumerate((3, 6, 7)))
    anchor = sessions[0]
    return EntryWindow(
        SignalChronology(
            anchor,
            anchor.bar_available_at,
            anchor.bar_available_at,
            anchor.bar_available_at + timedelta(minutes=1),
        ),
        sessions,
    )


def bar(day: date) -> DailyBar:
    return DailyBar(
        "005930",
        day,
        100,
        105,
        95,
        101,
        1000,
        100000,
        "fake",
        "KOSPI",
        "unadjusted",
        datetime.combine(day + timedelta(days=1), time.min, SEOUL),
    )


def assess(
    w: EntryWindow,
    *,
    candidate: DailyBar | None,
    attempted: ObservedSession | None = None,
    observed_at: datetime | None = None,
    contract: BacktestContract | None = None,
) -> Any:
    target = w.eligible_execution_session
    snapshot = FrozenJSON.freeze(
        {
            "quality_flags": [],
            "quality": None,
            "unavailable_dates": {},
            "session_basis": "observed_bars_unverified",
        }
    )
    return assess_entry(
        contract or BacktestContract.from_dict(config()),
        w,
        attempted_session=attempted or target,
        previous=bar(w.chronology.signal_anchor.trading_date),
        candidate=candidate,
        quality_snapshot=snapshot,
        observed_at=observed_at or w.sessions[-1].bar_available_at,
    )


def test_next_observed_session_not_calendar_day() -> None:
    w = window()
    assert w.eligible_execution_session == w.sessions[1]
    assert w.eligible_execution_session.trading_date == date(2025, 1, 6)
    result = assess(w, candidate=bar(date(2025, 1, 6)))
    assert result.status == EntryStatus.ELIGIBLE
    assert result.reason == "preflight_only_no_liquidity_guarantee"
    assert Fill is VirtualFill


def test_same_session_and_retry_rejected() -> None:
    w = window()
    same = assess(w, candidate=bar(date(2025, 1, 3)), attempted=w.sessions[0])
    assert same.status == EntryStatus.REJECTED
    assert same.reason == "same_or_prior_session_execution"
    retry = assess(w, candidate=bar(date(2025, 1, 7)), attempted=w.sessions[2])
    assert retry.reason == "entry_retry_forbidden"


def test_no_trade_missing_and_no_retry() -> None:
    w = window()
    no_trade = replace(bar(date(2025, 1, 6)), open=0, high=0, low=0, volume=0, trading_value=0)
    assert assess(w, candidate=no_trade).reason == "no_trade_bar"
    assert assess(w, candidate=no_trade).status == EntryStatus.UNFILLED
    assert assess(w, candidate=None).reason == "next_bar_unavailable"
    assert (
        assess(w, candidate=bar(date(2025, 1, 7)), attempted=w.sessions[2]).status
        == EntryStatus.REJECTED
    )
    last = EntryWindow(w.chronology, w.sessions[:1])
    assert assess(last, candidate=None).reason == "missing_next_session"


def test_invalid_next_open_no_fallback() -> None:
    invalid = bar(date(2025, 1, 6))
    # Emulate corrupt external data that bypassed construction; preflight must revalidate it.
    object.__setattr__(invalid, "open", 0)
    assert assess(window(), candidate=invalid).status == EntryStatus.UNFILLED


def test_lookahead_boundaries() -> None:
    w = window()
    anchor = w.chronology.signal_anchor
    with pytest.raises(ContractError):
        replace(w.chronology, signal_time=anchor.close_at)
    with pytest.raises(ContractError, match="future_bar_access"):
        assess(w, candidate=bar(date(2025, 1, 6)), observed_at=w.chronology.decision_time)
    late = EntryWindow(replace(w.chronology, decision_time=w.sessions[1].open_at), w.sessions)
    assert assess(late, candidate=bar(date(2025, 1, 6))).reason == "decision_not_before_next_open"


@pytest.mark.parametrize("items", [(1, 0, 2), (0, 1, 1)])
def test_nonincreasing_sessions(items: tuple[int, ...]) -> None:
    sessions = window().sessions
    with pytest.raises(ContractError, match="nonincreasing_sessions"):
        validate_sessions(tuple(sessions[i] for i in items))


@pytest.mark.parametrize(
    "missing", ["execution", "entry", "costs", "slippage", "sizing", "liquidity", "exit"]
)
def test_no_implicit_policies(missing: str) -> None:
    p = config()
    del p[missing]
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)


@pytest.mark.parametrize(
    "key",
    [
        "collision",
        "maximum_holding_sessions",
        "target_rule",
        "stop_rule",
        "time_exit_rule",
        "reference_price",
    ],
)
def test_exit_fields_required(key: str) -> None:
    p = config()
    del p["exit"][key]
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)


@pytest.mark.parametrize(
    "field,value",
    [
        ("leverage", "2"),
        ("leverage", "0"),
        ("short_permission", True),
        ("fractional_shares", True),
        ("maximum_concurrent_positions", 0),
        ("initial_capital_krw", "NaN"),
    ],
)
def test_invalid_portfolio_permissions(field: str, value: Any) -> None:
    p = config()
    p["sizing"][field] = value
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)


def test_immutable_canonical_and_changes() -> None:
    p = config()
    first = BacktestContract.from_dict(p)
    reordered = dict(reversed(list(p.items())))
    reordered["sizing"] = dict(p["sizing"], initial_capital_krw=Decimal("10000.00"))
    assert first.fingerprint == BacktestContract.from_dict(reordered).fingerprint
    assert first == BacktestContract(FrozenJSON(first.payload.payload_json))
    for key in (
        "entry",
        "exit",
        "costs",
        "execution",
        "slippage",
        "sizing",
        "liquidity",
        "quality",
    ):
        changed = deepcopy(p)
        changed[key]["version"] = "different"
        assert BacktestContract.from_dict(changed).fingerprint != first.fingerprint
    detached = first.to_dict()
    detached["entry"]["version"] = "mutation"
    assert first.to_dict()["entry"]["version"] == "fixture-v1"
    with pytest.raises(FrozenInstanceError):
        first.payload = FrozenJSON.freeze({})  # type: ignore[misc]


def test_date_aware_costs() -> None:
    p = config()
    original = p["costs"]["schedule"][0]
    early = dict(original, end="2025-01-15")
    late = dict(original, start="2025-01-16", sell_tax_rate="0.003")
    p["costs"]["schedule"] = [late, early]
    result = BacktestContract.from_dict(p)
    assert result.to_dict()["costs"]["schedule"][0]["end"] == "2025-01-15"
    late["start"] = "2025-01-17"
    with pytest.raises(ContractError, match="cost_schedule_gap"):
        BacktestContract.from_dict(p)
    late["start"] = "2025-01-15"
    with pytest.raises(ContractError, match="overlapping_cost_periods"):
        BacktestContract.from_dict(p)


def test_quality_failures_cannot_be_waived() -> None:
    p = config()
    p["quality"]["allowed_research_flags"] = ["suspected_discontinuity"]
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)
    jump = replace(bar(date(2025, 1, 6)), open=150, close=151, high=155, low=145)
    assert assess(window(), candidate=jump).reason == "suspected_discontinuity"


def test_explicit_intent_and_lineage() -> None:
    c = BacktestContract.from_dict(config())
    intent = TradeIntent(
        H,
        H,
        c.fingerprint,
        window().chronology,
        IntentPurpose.ENTRY,
        OrderSide.BUY,
        PositionSide.LONG,
    )
    assert intent.fingerprint == replace(intent).fingerprint
    with pytest.raises(ContractError):
        replace(intent, position_side=PositionSide.SHORT)
    with pytest.raises(ContractError):
        replace(intent, purpose=IntentPurpose.EXIT)
    lineage = ResultProvenance(
        H,
        H,
        H,
        H,
        c.fingerprint,
        c.component_fingerprint("execution"),
        c.component_fingerprint("costs"),
        H,
    )
    lineage.validate_contract(c)
    with pytest.raises(ContractError):
        replace(lineage, cost_policy=H).validate_contract(c)


def test_utc_equivalence() -> None:
    w = window()
    original = w.chronology
    assert (
        original.to_dict()
        == replace(original, decision_time=original.decision_time.astimezone(UTC)).to_dict()
    )


def test_ambiguity_policy_is_explicit_and_never_optimistic() -> None:
    p = config()
    p["exit"]["collision"] = "pessimistic"
    assert BacktestContract.from_dict(p).to_dict()["exit"]["collision"] == "pessimistic"
    p["exit"]["collision"] = "optimistic"
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)


def test_cost_zero_requires_explicit_values() -> None:
    p = config()
    for key in ("buy_fee_rate", "sell_fee_rate", "sell_tax_rate", "sell_market_levy_rate"):
        p["costs"]["schedule"][0][key] = "0"
    assert BacktestContract.from_dict(p).to_dict()["costs"]["schedule"][0]["buy_fee_rate"] == "0"
    del p["costs"]["schedule"][0]["buy_fee_rate"]
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.1", "1", 0.01, None])
def test_invalid_cost_numbers(value: Any) -> None:
    p = config()
    p["costs"]["schedule"][0]["sell_fee_rate"] = value
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)


def test_liquidity_and_period_rejections() -> None:
    p = config()
    p["liquidity"]["minimum_volume"] = 1001
    assert (
        assess(
            window(), candidate=bar(date(2025, 1, 6)), contract=BacktestContract.from_dict(p)
        ).reason
        == "liquidity_minimum_not_met"
    )
    p["end"] = "2025-01-03"
    assert (
        assess(
            window(), candidate=bar(date(2025, 1, 6)), contract=BacktestContract.from_dict(p)
        ).reason
        == "outside_contract_period"
    )
    p["liquidity"]["maximum_participation_rate"] = "1.1"
    with pytest.raises(ContractError):
        BacktestContract.from_dict(p)


def test_price_basis_must_not_change() -> None:
    adjusted = replace(bar(date(2025, 1, 6)), adjustment="adjusted")
    assert assess(window(), candidate=adjusted).reason == "unsupported_or_mixed_price_basis"


def test_existing_signal_chronology_bridge() -> None:
    from donghak_stock_vision.signals.domain import build_signal
    from tests.learning.test_signal_domain import DOWN, POLICY, UP, context, outputs

    anchor = window().chronology.signal_anchor
    ctx = replace(
        context(),
        ticker=anchor.ticker,
        as_of=anchor.bar_available_at,
        data_cutoff=anchor.bar_available_at,
    )
    up, down = outputs()
    signal = build_signal(ctx, POLICY, up_model=UP, down_model=DOWN, up=up, down=down)
    chronology = SignalChronology.from_signal(signal, anchor, anchor.bar_available_at)
    assert chronology.signal_anchor == anchor
    assert signal.up.raw_score == up.score
    with pytest.raises(ContractError, match="signal_anchor_mismatch"):
        SignalChronology.from_signal(
            signal, window().sessions[1], window().sessions[1].bar_available_at
        )
