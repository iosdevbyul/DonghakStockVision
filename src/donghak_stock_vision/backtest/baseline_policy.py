"""One approved baseline: pure policy/preflight arithmetic, never a backtest runner."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy, tax_schedule
from donghak_stock_vision.backtest.entry_contract import (
    EntryWindow,
    IntentPurpose,
    SignalChronology,
    TradeIntent,
)
from donghak_stock_vision.backtest.research_contract import (
    BacktestContract,
    OrderSide,
    PositionSide,
)
from donghak_stock_vision.backtest.validation import integer, money, require, sha, utc
from donghak_stock_vision.data.learning import FEATURES, digest
from donghak_stock_vision.data.schema import validate_ticker
from donghak_stock_vision.models.inference import HGBArtifact, validate_features
from donghak_stock_vision.signals.domain import SignalCandidate


def baseline_rules() -> dict[str, Any]:
    """Fixed approval, not tunable defaults or an OOS selection space."""
    return {
        "strategy_version": "long_only_up_reversal_baseline_v1",
        "scope": "historical_research",
        "executable_direction": "up",
        "model": "hist_gradient_boosting_leaves7",
        "down_model": "retained_non_executable",
        "feature_version": "ohlcv_value_v1",
        "label_reference": "reversal_barrier_v1_not_automatic_holding_default",
        "signal_threshold": "0.5",
        "threshold_basis": "preapproved_classification_reuse_not_optimal_trade_threshold",
        "entry": "immediate_next_observed_session_open_no_retry",
        "barrier": "max(0.02,2*anchor_volatility_10)",
        "target": "entry_execution_price*(1+barrier)",
        "stop": "entry_execution_price*(1-barrier)",
        "maximum_holding_sessions": 5,
        "holding_count_basis": "entry_session_is_one",
        "time_exit": "fifth_observed_session_close_if_no_barrier_hit",
        "collision": "pessimistic_stop_first",
        "initial_capital_krw": "100000000",
        "target_notional_fraction": "0.05",
        "maximum_concurrent_positions": 20,
        "candidate_ordering": ["raw_up_score_descending", "ticker_ascending"],
        "cash": "available_after_reservations_including_commission",
        "minimum_quantity": 1,
        "maximum_positions_per_ticker": 1,
        "pyramiding": False,
        "signals_while_held": "ignore",
        "pending_entry": "block_duplicate_and_reserve_position_slot",
        "short_permission": False,
        "leverage": "1",
        "fractional_shares": False,
        "minimum_volume": 1,
        "minimum_trading_value_krw": None,
        "maximum_participation_rate": "0.01",
        "participation": "execution_side_reduce_integer_quantity_after_volume_publication",
        "volume_timing": "never_decision_input_or_open_time_knowledge",
        "slippage_per_side": "0.001",
        "slippage_basis": "adverse_research_assumption_not_observed",
        "quality": "existing_segment_checks_required_no_price_correction",
        "gap_exit": "unresolved_no_optimistic_barrier_fill_assumption",
        "operational_eligible": False,
        "executable": False,
    }


@dataclass(frozen=True)
class BaselineBacktestPolicy:
    costs: BaselineCostPolicy | None
    allowed_research_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        require(self.costs is None or type(self.costs) is BaselineCostPolicy, "invalid_cost_policy")
        require(
            type(self.allowed_research_flags) is tuple
            and all(type(v) is str and bool(v) for v in self.allowed_research_flags)
            and tuple(sorted(set(self.allowed_research_flags))) == self.allowed_research_flags,
            "invalid_quality_allowlist",
        )
        require(
            not set(self.allowed_research_flags)
            & {
                "suspected_discontinuity",
                "no_trade_bar",
                "suspected_gap",
                "excluded_corporate_action",
                "known_incomplete_trading_date",
                "invalid_market_order_or_duplicates",
            },
            "quality_failure_cannot_be_waived",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": baseline_rules(),
            "cost_policy": self.costs.to_dict() if self.costs else None,
            "tax_schedule": tax_schedule(),
            "allowed_research_flags": list(self.allowed_research_flags),
            "cost_status": "configured" if self.costs else "BLOCKED_ON_COST_POLICY",
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def require_costs(self) -> BaselineCostPolicy:
        require(self.costs is not None, "BLOCKED_ON_COST_POLICY")
        assert self.costs is not None
        return self.costs

    def contract(self, start: date, end: date) -> BacktestContract:
        """Compile to the merged contract; never dispatch the older backtest runner."""
        costs = self.require_costs()
        require(type(start) is date and type(end) is date and start <= end, "invalid_period")
        require(
            date(2022, 1, 1) <= start <= end <= date(2026, 10, 1),
            "BLOCKED_ON_COST_POLICY:period_outside_verified_schedule",
        )

        def ref(name: str) -> dict[str, str]:
            return {"artifact_id": name, "version": "baseline_v1", "content_hash": self.fingerprint}

        return BacktestContract.from_dict(
            {
                "contract_version": "research_backtest_v1",
                "scope": "historical_research",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "execution": {
                    "version": "baseline_v1",
                    "timing": "next_observed_session_open",
                    "availability": "complete_daily_bar_next_seoul_midnight",
                    "future_access": "simulator_only",
                    "fill_type": "reuse_virtual_fill_v1",
                },
                "entry": {
                    "version": "baseline_v1",
                    "reference_price": "open",
                    "attempt_policy": "immediate_next_observed_only",
                    "invalid_next_session": "unfilled_no_retry",
                },
                "exit": {
                    "version": "baseline_v1",
                    "maximum_holding_sessions": 5,
                    "holding_count_basis": "entry_session_is_one",
                    "target_rule": {"mode": "external_rule", "specification": ref("anchor_target")},
                    "stop_rule": {"mode": "external_rule", "specification": ref("anchor_stop")},
                    "time_exit_rule": ref("fifth_session_close"),
                    "reference_price": "explicit_rule_price",
                    "collision": "pessimistic",
                },
                "costs": {
                    "version": costs.fingerprint,
                    "basis": "executed_notional",
                    "date_basis": "settlement_date",
                    "rounding": {"quantum": "1", "mode": "up"},
                    "tax_rounding": {"quantum": "1", "mode": "down"},
                    "schedule": [
                        dict(
                            row,
                            buy_fee_rate="0.00015",
                            sell_fee_rate="0.00015",
                            minimum_fee={"mode": "disabled", "amount_krw": None},
                        )
                        for row in tax_schedule()["periods"]
                    ],
                },
                "slippage": {
                    "version": "baseline_10bps_v1",
                    "method": "adverse_proportional",
                    "rate": "0.001",
                    "rounding": {"quantum": "1", "mode": "adverse"},
                },
                "sizing": {
                    "version": "baseline_v1",
                    "initial_capital_krw": "100000000",
                    "policy": ref("five_percent_equity_integer_cash_limited"),
                    "maximum_concurrent_positions": 20,
                    "cash_availability": "available_after_reservations",
                    "leverage": "1",
                    "short_permission": False,
                    "fractional_shares": False,
                },
                "liquidity": {
                    "version": "baseline_v1",
                    "minimum_volume": 1,
                    "minimum_trading_value_krw": None,
                    "maximum_participation_rate": "0.01",
                    "rejection": "unfilled_no_retry",
                    "assumption": "daily_bar_does_not_prove_fill",
                },
                "quality": {
                    "version": "baseline_v1",
                    "allowed_research_flags": list(self.allowed_research_flags),
                    "segment_validation": "existing_check_segment_required",
                    "corporate_actions": "reject_unusable_segment_no_adjustment",
                },
            }
        )


@dataclass(frozen=True)
class CandidateAccountContext:
    """Caller-verified account view, not a new portfolio or mutable account."""

    source_hash: str
    known_at: datetime
    held_tickers: frozenset[str]
    pending_entry_tickers: frozenset[str]

    def __post_init__(self) -> None:
        sha(self.source_hash)
        object.__setattr__(self, "known_at", utc(self.known_at))
        for tickers in (self.held_tickers, self.pending_entry_tickers):
            require(type(tickers) is frozenset, "immutable_ticker_set_required")
            for ticker in tickers:
                validate_ticker(ticker)
        require(not self.held_tickers & self.pending_entry_tickers, "pyramiding_context")


@dataclass(frozen=True)
class BaselineCandidate:
    intent: TradeIntent
    raw_up_score: Decimal
    barrier_width: Decimal
    account_hash: str

    def __post_init__(self) -> None:
        require(type(self.intent) is TradeIntent, "trade_intent_required")
        require(self.intent.purpose == IntentPurpose.ENTRY, "entry_candidate_required")
        score = Decimal(money(self.raw_up_score))
        width = Decimal(money(self.barrier_width))
        require(Decimal("0.5") <= score <= 1, "ineligible_candidate_score")
        require(Decimal("0.02") <= width < 1, "invalid_baseline_barrier")
        object.__setattr__(self, "raw_up_score", score)
        object.__setattr__(self, "barrier_width", width)
        sha(self.account_hash)

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "intent": self.intent.fingerprint,
                "raw_up_score": money(self.raw_up_score),
                "barrier_width": money(self.barrier_width),
                "account_hash": self.account_hash,
            }
        )


@dataclass(frozen=True)
class CandidateAssessment:
    reason: str
    candidate: BaselineCandidate | None


def barrier_width(anchor_volatility_10: Decimal) -> Decimal:
    volatility = Decimal(money(anchor_volatility_10))
    with localcontext(Context(prec=80)):
        width = max(Decimal("0.02"), 2 * volatility)
        require(width < 1, "nonpositive_stop_barrier")
        return width


def exit_levels(entry_execution_price: Decimal, width: Decimal) -> tuple[Decimal, Decimal]:
    """Exact target/stop references, before exit-side slippage; no fill inference."""
    price = Decimal(money(entry_execution_price, positive=True))
    width = Decimal(money(width, positive=True))
    require(Decimal("0.02") <= width < 1, "invalid_baseline_barrier")
    with localcontext(Context(prec=80)):
        return price * (1 + width), price * (1 - width)


def assess_candidate(
    policy: BaselineBacktestPolicy,
    contract: BacktestContract,
    signal: SignalCandidate | None,
    *,
    up_artifact: HGBArtifact,
    values: tuple[float, ...],
    window: EntryWindow,
    account: CandidateAccountContext,
) -> CandidateAssessment:
    """Consume trusted integration outputs; no inference, future prices or fills."""
    if policy.costs is None:
        return CandidateAssessment("BLOCKED_ON_COST_POLICY", None)
    p = contract.to_dict()
    require(
        contract == policy.contract(date.fromisoformat(p["start"]), date.fromisoformat(p["end"])),
        "baseline_contract_mismatch",
    )
    if signal is None:
        return CandidateAssessment("signal_unavailable", None)
    if signal.rejected_quality_flags or not set(signal.context.quality_flags) <= set(
        policy.allowed_research_flags
    ):
        return CandidateAssessment("quality_blocked", None)
    if signal.context.population != "prior_decline" or signal.up.status != "scored":
        return CandidateAssessment("up_context_unavailable", None)
    if signal.up.raw_score is None or signal.up.raw_score < 0.5:
        return CandidateAssessment("below_threshold_or_null", None)
    require(type(up_artifact) is HGBArtifact and up_artifact.direction == "up", "up_HGB_required")
    up_artifact.__post_init__()
    require(up_artifact.fingerprint == signal.up.model_fingerprint, "model_provenance_mismatch")
    validate_features(FEATURES, values)
    require(
        digest([list(FEATURES), list(values)]) == signal.context.feature_hash, "feature_mismatch"
    )
    require(values[2] < 0, "up_population_mismatch")
    chronology = SignalChronology.from_signal(
        signal, window.chronology.signal_anchor, window.chronology.decision_time
    )
    require(chronology == window.chronology, "chronology_mismatch")
    require(account.known_at <= chronology.decision_time, "future_account")
    ticker = signal.context.ticker
    if ticker in account.held_tickers:
        return CandidateAssessment("held_signal_ignored_no_pyramiding", None)
    if ticker in account.pending_entry_tickers:
        return CandidateAssessment("pending_entry", None)
    if len(account.held_tickers | account.pending_entry_tickers) >= 20:
        return CandidateAssessment("position_limit", None)
    next_session = window.eligible_execution_session
    if next_session is None:
        return CandidateAssessment("missing_next_session", None)
    if chronology.decision_time >= next_session.open_at:
        return CandidateAssessment("decision_not_before_next_open", None)
    if (
        not p["start"]
        <= chronology.signal_anchor.trading_date.isoformat()
        <= (next_session.trading_date.isoformat())
        <= p["end"]
    ):
        return CandidateAssessment("outside_contract_period", None)
    intent = TradeIntent(
        signal.fingerprint,
        policy.fingerprint,
        contract.fingerprint,
        chronology,
        IntentPurpose.ENTRY,
        OrderSide.BUY,
        PositionSide.LONG,
    )
    return CandidateAssessment(
        "eligible_candidate_not_order",
        BaselineCandidate(
            intent,
            Decimal(str(signal.up.raw_score)),
            barrier_width(Decimal(str(values[4]))),
            account.source_hash,
        ),
    )


def order_candidates(candidates: tuple[BaselineCandidate, ...]) -> tuple[BaselineCandidate, ...]:
    """Only compare simultaneous candidates; never rank across dates or directions."""
    require(type(candidates) is tuple, "immutable_candidates_required")
    for candidate in candidates:
        require(type(candidate) is BaselineCandidate, "baseline_candidate_required")
        candidate.__post_init__()
    tickers = [c.intent.chronology.signal_anchor.ticker for c in candidates]
    require(len(set(tickers)) == len(tickers), "duplicate_ticker_candidate")
    require(
        len({c.intent.chronology.decision_time for c in candidates}) <= 1, "mixed_decision_times"
    )
    require(
        len({c.intent.chronology.signal_anchor.trading_date for c in candidates}) <= 1,
        "mixed_anchor_sessions",
    )
    require(len({c.intent.strategy_policy_id for c in candidates}) <= 1, "mixed_strategy_policies")
    require(len({c.account_hash for c in candidates}) <= 1, "mixed_account_views")
    require(len({c.intent.contract_id for c in candidates}) <= 1, "mixed_backtest_contracts")
    return tuple(
        sorted(
            candidates,
            key=lambda c: (c.raw_up_score.copy_negate(), c.intent.chronology.signal_anchor.ticker),
        )
    )


def desired_quantity(
    policy: BaselineBacktestPolicy,
    *,
    current_equity: Decimal,
    available_cash: Decimal,
    entry_execution_price: Decimal,
) -> int:
    """Entry-side arithmetic from already known inputs. No daily volume argument.

    Caller must provide causally valued current equity and cash after reservations.
    Zero means rejected below one share. No portfolio state is changed.
    """
    costs = policy.require_costs()
    equity = Decimal(money(current_equity, positive=True))
    cash = Decimal(money(available_cash))
    price = Decimal(money(entry_execution_price, positive=True))
    with localcontext(Context(prec=80)):
        upper = int((equity * Decimal("0.05") / price).to_integral_value(rounding=ROUND_FLOOR))
        upper = min(upper, int(cash // price))
        low, high = 0, upper
        # Monotone fee-aware cash bound; no linear search or quantity default.
        while low < high:
            mid = (low + high + 1) // 2
            if price * mid + costs.commission(price * mid) <= cash:
                low = mid
            else:
                high = mid - 1
        return integer(low)


def participation_quantity(
    desired: int,
    *,
    session_volume: int,
    bar_available_at: datetime,
    observed_at: datetime,
) -> int:
    """Execution-only retrospective cap; never call with volume at the session open.

    No retry, partial fill, order or economic effect is produced. Zero is rejection.
    """
    integer(desired)
    integer(session_volume)
    require(utc(observed_at) >= utc(bar_available_at), "future_session_volume")
    return min(desired, session_volume // 100)
