"""Synthetic policy examples only: no prediction, backtest or portfolio simulation."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Any

import pytest

from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy, tax_rates, tax_schedule
from donghak_stock_vision.backtest.baseline_policy import (
    BaselineBacktestPolicy,
    BaselineCandidate,
    CandidateAccountContext,
    CandidateAssessment,
    assess_candidate,
    barrier_width,
    baseline_rules,
    desired_quantity,
    exit_levels,
    order_candidates,
    participation_quantity,
)
from donghak_stock_vision.backtest.entry_contract import EntryStatus, EntryWindow
from donghak_stock_vision.backtest.research_contract import BacktestContract, OrderSide
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import FEATURES, canonical, digest
from donghak_stock_vision.models.inference import HGBArtifact
from donghak_stock_vision.signals.domain import SignalCandidate, build_signal
from tests.backtest.test_research_contract import H, assess, bar, config, window
from tests.learning.test_signal_domain import DOWN, POLICY, VALUES, context, outputs

COSTS = BaselineCostPolicy(Decimal(1), "down_per_component_KRW")
BASELINE = BaselineBacktestPolicy(COSTS, ())
START, END = date(2025, 1, 1), date(2025, 1, 31)


def artifact() -> HGBArtifact:
    return HGBArtifact(
        "up",
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "fixture-snapshot",
        canonical(
            {
                "max_leaf_nodes": 7,
                "learning_rate": 0.05,
                "max_iter": 150,
                "min_samples_leaf": 100,
                "l2_regularization": 1.0,
                "max_bins": 255,
                "early_stopping": False,
                "class_weight": "balanced",
                "random_state": 42,
            }
        ),
        (("numpy", "fixture"), ("scikit-learn", "fixture"), ("scipy", "fixture")),
        FEATURES,
        "ohlcv_value_v1",
        "reversal_barrier_v1",
    )


def signal(
    score: float = 0.5, *, population: str = "prior_decline", blocked: bool = False
) -> SignalCandidate:
    anchor = window().chronology.signal_anchor
    ctx = replace(
        context(),
        as_of=anchor.bar_available_at,
        data_cutoff=anchor.bar_available_at,
        population=population,
        quality_flags=("unapproved",) if blocked else (),
    )
    up, down = outputs()
    up = replace(up, score=score, artifact_fingerprint=artifact().fingerprint)
    if population != "prior_decline":
        up = replace(up, score=None, status="context_mismatch")
        down = replace(down, score=0.99, status="scored")
    return build_signal(
        ctx,
        POLICY,
        up_model=artifact().fingerprint,
        down_model=DOWN,
        up=None if blocked else up,
        down=None if blocked else down,
    )


def account() -> CandidateAccountContext:
    return CandidateAccountContext(H, window().chronology.signal_time, frozenset(), frozenset())


def evaluate(
    candidate: SignalCandidate | None,
    *,
    policy: BaselineBacktestPolicy = BASELINE,
    state: CandidateAccountContext | None = None,
    w: EntryWindow | None = None,
) -> CandidateAssessment:
    return assess_candidate(
        policy,
        BASELINE.contract(START, END),
        candidate,
        up_artifact=artifact(),
        values=VALUES,
        window=w or window(),
        account=state or account(),
    )


@pytest.mark.parametrize("score,eligible", [(0.499999, False), (0.5, True), (1.0, True)])
def test_threshold(score: float, eligible: bool) -> None:
    result = evaluate(signal(score))
    assert (result.candidate is not None) == eligible
    if result.candidate:
        assert result.candidate.intent.order_side == OrderSide.BUY
        assert result.candidate.raw_up_score == Decimal(str(score))


@pytest.mark.parametrize("case", ["down", "blocked", "missing"])
def test_nontrade_signals(case: str) -> None:
    candidate = (
        None
        if case == "missing"
        else signal(
            population="prior_rise" if case == "down" else "prior_decline",
            blocked=case == "blocked",
        )
    )
    if case == "blocked":
        with pytest.raises(ContractError, match="unknown_quality_state:unapproved"):
            evaluate(candidate)
    else:
        assert evaluate(candidate).candidate is None


def test_no_pyramiding_pending_and_position_limit() -> None:
    for state, reason in [
        (
            replace(account(), held_tickers=frozenset({"005930"})),
            "held_signal_ignored_no_pyramiding",
        ),
        (replace(account(), pending_entry_tickers=frozenset({"005930"})), "pending_entry"),
        (
            replace(account(), held_tickers=frozenset(f"{i:06d}" for i in range(20))),
            "position_limit",
        ),
    ]:
        assert evaluate(signal(), state=state).reason == reason
    with pytest.raises(ContractError, match="future_account"):
        evaluate(
            signal(), state=replace(account(), known_at=account().known_at + timedelta(days=1))
        )
    with pytest.raises(ContractError, match="pyramiding_context"):
        replace(
            account(),
            held_tickers=frozenset({"005930"}),
            pending_entry_tickers=frozenset({"005930"}),
        )


def test_cost_required_and_no_zero_fallback() -> None:
    blocked = BaselineBacktestPolicy(None, ())
    assert evaluate(signal(), policy=blocked).reason == "BLOCKED_ON_COST_POLICY"
    with pytest.raises(ContractError, match="BLOCKED_ON_COST_POLICY"):
        blocked.contract(START, END)
    with pytest.raises(ContractError, match="BLOCKED_ON_COST_POLICY"):
        desired_quantity(
            blocked,
            current_equity=Decimal(100),
            available_cash=Decimal(100),
            entry_execution_price=Decimal(1),
        )


def test_existing_entry_preflight_and_no_retry() -> None:
    contract = BASELINE.contract(START, END)
    assert contract.to_dict()["liquidity"]["minimum_trading_value_krw"] is None
    w = window()
    assert (
        assess(w, candidate=bar(date(2025, 1, 6)), contract=contract).status == EntryStatus.ELIGIBLE
    )
    assert (
        assess(
            w, candidate=bar(date(2025, 1, 3)), attempted=w.sessions[0], contract=contract
        ).reason
        == "same_or_prior_session_execution"
    )
    assert (
        assess(
            w, candidate=bar(date(2025, 1, 7)), attempted=w.sessions[2], contract=contract
        ).reason
        == "entry_retry_forbidden"
    )
    no_trade = replace(bar(date(2025, 1, 6)), open=0, high=0, low=0, volume=0, trading_value=0)
    assert assess(w, candidate=no_trade, contract=contract).reason == "no_trade_bar"
    late = EntryWindow(replace(w.chronology, decision_time=w.sessions[1].open_at), w.sessions)
    assert evaluate(signal(), w=late).reason == "decision_not_before_next_open"
    assert (
        evaluate(signal(), w=EntryWindow(w.chronology, w.sessions[:1])).reason
        == "missing_next_session"
    )


@pytest.mark.parametrize("volatility,width", [("0", "0.02"), ("0.009", "0.02"), ("0.02", "0.04")])
def test_barrier_and_exit_configuration(volatility: str, width: str) -> None:
    assert barrier_width(Decimal(volatility)) == Decimal(width)
    assert exit_levels(Decimal(100), Decimal("0.02")) == (Decimal(102), Decimal(98))
    exit_policy = BASELINE.contract(START, END).to_dict()["exit"]
    assert exit_policy["maximum_holding_sessions"] == 5
    assert exit_policy["holding_count_basis"] == "entry_session_is_one"
    assert exit_policy["collision"] == "pessimistic"
    assert baseline_rules()["collision"] == "pessimistic_stop_first"
    assert "fifth_observed_session_close" in baseline_rules()["time_exit"]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-0.1", "0.5"])
def test_invalid_volatility(bad: str) -> None:
    with pytest.raises(ContractError):
        barrier_width(Decimal(bad))


def test_deterministic_ordering() -> None:
    def candidate(ticker: str, score: float) -> BaselineCandidate:
        w = window()
        sessions = tuple(replace(s, ticker=ticker) for s in w.sessions)
        w = EntryWindow(replace(w.chronology, signal_anchor=sessions[0]), sessions)
        s = signal(score)
        s = replace(s, context=replace(s.context, ticker=ticker))
        result = evaluate(s, w=w).candidate
        assert result is not None
        return result

    a, b, c = candidate("005930", 0.8), candidate("000001", 0.8), candidate("000002", 0.9)
    assert order_candidates((a, b, c)) == (c, b, a)
    assert order_candidates((c, a, b)) == (c, b, a)
    with pytest.raises(ContractError, match="duplicate_ticker"):
        order_candidates((a, a))
    with pytest.raises(ContractError, match="mixed_account"):
        order_candidates((a, replace(b, account_hash="f" * 64)))


@pytest.mark.parametrize(
    "cash,expected", [("100000000", 500), ("10000", 0), ("10002", 1), ("5000750", 500)]
)
def test_five_percent_fee_aware_integer_sizing(cash: str, expected: int) -> None:
    assert (
        desired_quantity(
            BASELINE,
            current_equity=Decimal("100000000"),
            available_cash=Decimal(cash),
            entry_execution_price=Decimal("10000"),
        )
        == expected
    )
    # No future-volume input is accepted by this function.


@pytest.mark.parametrize("volume,expected", [(0, 0), (99, 0), (100, 1), (999, 9), (100000, 500)])
def test_participation(volume: int, expected: int) -> None:
    session = window().sessions[1]
    assert (
        participation_quantity(
            500,
            session_volume=volume,
            bar_available_at=session.bar_available_at,
            observed_at=session.bar_available_at,
        )
        == expected
    )
    with pytest.raises(ContractError, match="future_session_volume"):
        participation_quantity(
            500,
            session_volume=volume,
            bar_available_at=session.bar_available_at,
            observed_at=session.open_at,
        )


def test_slippage_commission_and_taxes() -> None:
    assert COSTS.adverse_price(Decimal(10000), OrderSide.BUY) == Decimal(10010)
    assert COSTS.adverse_price(Decimal(10000), OrderSide.SELL) == Decimal(9990)
    assert COSTS.adverse_price(Decimal(10001), OrderSide.BUY) == Decimal(10012)
    assert COSTS.adverse_price(Decimal(10001), OrderSide.SELL) == Decimal(9990)
    args: dict[str, Any] = dict(
        execution_price=Decimal(10010),
        quantity=3,
        execution_date=date(2025, 1, 6),
        settlement_date=date(2025, 1, 8),
        settlement_evidence_id=H,
    )
    assert COSTS.charges(side=OrderSide.BUY, **args) == (Decimal(5), Decimal(0), Decimal(0))
    assert COSTS.charges(side=OrderSide.SELL, **args) == (Decimal(5), Decimal(0), Decimal(45))
    assert COSTS.commission(Decimal("0")) == 0
    assert COSTS.commission(Decimal("0.01")) == 1
    assert COSTS.to_dict()["commission"]["minimum"] is None
    assert COSTS.to_dict()["commission"]["maximum"] is None


@pytest.mark.parametrize(
    "year,tax",
    [(2022, "0.0008"), (2023, "0.0005"), (2024, "0.0003"), (2025, "0"), (2026, "0.0005")],
)
def test_tax_schedule(year: int, tax: str) -> None:
    assert tax_rates(date(year, 1, 1)) == (Decimal(tax), Decimal("0.0015"))
    assert tax_rates(date(year, 6, 30)) == (Decimal(tax), Decimal("0.0015"))


def test_settlement_not_trade_date_and_unsupported_dates() -> None:
    charge = COSTS.charges(
        execution_price=Decimal(10000),
        quantity=100,
        side=OrderSide.SELL,
        execution_date=date(2025, 12, 29),
        settlement_date=date(2026, 1, 2),
        settlement_evidence_id=H,
    )
    assert charge == (Decimal(150), Decimal(500), Decimal(1500))
    assert tax_schedule()["date_basis"] == "settlement_date"
    for day in [date(2021, 12, 31), date(2026, 10, 2)]:
        with pytest.raises(ContractError, match="BLOCKED_ON_COST_POLICY"):
            tax_rates(day)


def test_policy_identity_and_immutability() -> None:
    assert BASELINE.fingerprint == replace(BASELINE).fingerprint
    changed = replace(BASELINE, allowed_research_flags=("research_sessions_excluded_not_holidays",))
    assert BASELINE.fingerprint != changed.fingerprint
    assert BASELINE.fingerprint != replace(BASELINE, costs=None).fingerprint
    assert (
        BASELINE.contract(START, END).fingerprint
        != BASELINE.contract(START, date(2025, 1, 30)).fingerprint
    )
    candidate = evaluate(signal()).candidate
    repeated = evaluate(signal()).candidate
    assert candidate is not None and repeated is not None
    assert candidate.fingerprint == repeated.fingerprint
    detached = BASELINE.to_dict()
    detached["rules"]["signal_threshold"] = "0.9"
    assert BASELINE.to_dict()["rules"]["signal_threshold"] == "0.5"
    with pytest.raises(FrozenInstanceError):
        BASELINE.costs = None  # type: ignore[misc]
    with localcontext() as ctx:
        ctx.prec = 3
        assert BASELINE.fingerprint == replace(BASELINE).fingerprint
        assert COSTS.commission(Decimal(10010)) == Decimal(2)
        assert barrier_width(Decimal("0.0123456789")) == Decimal("0.0246913578")


def test_quality_and_provenance_fail_closed() -> None:
    with pytest.raises(ContractError, match="quality_failure"):
        replace(BASELINE, allowed_research_flags=("suspected_discontinuity",))
    with pytest.raises(ContractError, match="model_provenance_mismatch"):
        s = signal()
        evaluate(replace(s, up=replace(s.up, model_fingerprint="e" * 64)))
    with pytest.raises(ContractError, match="feature_mismatch"):
        s = signal()
        evaluate(replace(s, context=replace(s.context, feature_hash="f" * 64)))


def test_legacy_contract_identity_unchanged() -> None:
    original = config()
    normalized = BacktestContract.from_dict(original)
    assert normalized.to_dict() == original
    assert normalized.fingerprint == digest(original)
    changed = config()
    changed["liquidity"]["minimum_trading_value_krw"] = None
    assert BacktestContract.from_dict(changed).fingerprint != normalized.fingerprint
    assert "date_basis" not in normalized.to_dict()["costs"]
    with pytest.raises(ContractError):
        changed["costs"]["date_basis"] = "settlement_date"
        BacktestContract.from_dict(changed)  # Separate tax rounding must also be supplied.


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
def test_invalid_sizing_and_slippage(bad: Decimal) -> None:
    with pytest.raises(ContractError):
        desired_quantity(
            BASELINE,
            current_equity=bad,
            available_cash=Decimal(1000),
            entry_execution_price=Decimal(100),
        )
    with pytest.raises(ContractError):
        COSTS.adverse_price(bad, OrderSide.BUY)


def test_fixed_permissions_and_cost_assumptions() -> None:
    rules = baseline_rules()
    assert rules["executable_direction"] == "up"
    assert rules["down_model"] == "retained_non_executable"
    assert rules["initial_capital_krw"] == "100000000"
    assert rules["maximum_concurrent_positions"] == 20
    assert rules["short_permission"] is rules["fractional_shares"] is rules["pyramiding"] is False
    assert rules["leverage"] == "1"
    for quantum, rounding in (
        (Decimal("0.01"), "down_per_component_KRW"),
        (Decimal(1), "half_up"),
    ):
        with pytest.raises(ContractError):
            BaselineCostPolicy(quantum, rounding)


def test_all_rule_components_participate_in_fingerprint() -> None:
    for name in baseline_rules():
        changed = BASELINE.to_dict()
        changed["rules"][name] = "tampered"
        assert digest(changed) != BASELINE.fingerprint
    changed = BASELINE.to_dict()
    changed["cost_policy"]["taxes"]["sources"] = ["different evidence"]
    assert digest(changed) != BASELINE.fingerprint


def test_settlement_evidence_required_and_no_date_fallback() -> None:
    args: dict[str, Any] = dict(
        execution_price=Decimal(10001),
        quantity=2,
        side=OrderSide.SELL,
        execution_date=date(2026, 1, 5),
        settlement_date=date(2026, 1, 7),
        settlement_evidence_id=H,
    )
    # Separate flooring: 10.001 + 30.003 => 10 + 30, distinct from commission ceiling.
    assert COSTS.charges(**args) == (Decimal(4), Decimal(10), Decimal(30))
    for changes in (
        {"settlement_evidence_id": ""},
        {"settlement_date": None},
        {"settlement_date": date(2025, 12, 31)},
        {"quantity": 0},
    ):
        with pytest.raises(ContractError):
            COSTS.charges(**(args | changes))


def test_score_ordering_independent_of_decimal_context() -> None:
    first = evaluate(signal(0.50001)).candidate
    second = evaluate(signal(0.50002)).candidate
    assert first is not None and second is not None
    second = replace(
        second,
        intent=replace(
            second.intent,
            chronology=replace(
                second.intent.chronology,
                signal_anchor=replace(second.intent.chronology.signal_anchor, ticker="005931"),
            ),
        ),
    )
    with localcontext() as ctx:
        ctx.prec = 2
        assert order_candidates((first, second)) == (second, first)
