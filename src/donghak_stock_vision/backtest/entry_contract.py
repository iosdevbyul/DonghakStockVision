"""Pure chronology and entry preflight checks; never creates an order or fill."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.research_contract import (
    BacktestContract,
    OrderSide,
    PositionSide,
)
from donghak_stock_vision.backtest.validation import integer, require, sha, utc
from donghak_stock_vision.data.learning import AnalysisError, digest
from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_ticker
from donghak_stock_vision.data.snapshot import covered, validate_quality
from donghak_stock_vision.features.engine import check_segment
from donghak_stock_vision.signals.domain import SignalCandidate


@dataclass(frozen=True)
class ObservedSession:
    """Price-free session metadata supplied by a frozen, quality-aware input calendar."""

    ticker: str
    trading_date: date
    sequence: int
    open_at: datetime
    close_at: datetime
    bar_available_at: datetime

    def __post_init__(self) -> None:
        validate_ticker(self.ticker)
        require(type(self.trading_date) is date, "session_date_required")
        require(self.trading_date.weekday() < 5, "weekend_session_unsupported")
        integer(self.sequence)
        for name in ("open_at", "close_at", "bar_available_at"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        require(self.open_at < self.close_at <= self.bar_available_at, "invalid_session_times")
        require(
            self.open_at.astimezone(SEOUL).date() == self.trading_date
            and self.close_at.astimezone(SEOUL).date() == self.trading_date,
            "session_date_mismatch",
        )
        midnight = datetime.combine(self.trading_date + timedelta(days=1), time.min, SEOUL)
        require(self.bar_available_at >= midnight, "complete_bar_publication_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "trading_date": self.trading_date.isoformat(),
            "sequence": self.sequence,
            "open_at": self.open_at.isoformat(),
            "close_at": self.close_at.isoformat(),
            "bar_available_at": self.bar_available_at.isoformat(),
        }


def validate_sessions(sessions: tuple[ObservedSession, ...]) -> None:
    require(type(sessions) is tuple and bool(sessions), "session_sequence_required")
    for s in sessions:
        require(type(s) is ObservedSession, "invalid_session")
    for previous, current in zip(sessions, sessions[1:], strict=False):
        require(previous.ticker == current.ticker, "mixed_ticker_sessions")
        require(
            previous.trading_date < current.trading_date
            and previous.sequence < current.sequence
            and previous.close_at < current.open_at,
            "nonincreasing_sessions",
        )


@dataclass(frozen=True)
class SignalChronology:
    signal_anchor: ObservedSession
    feature_cutoff: datetime
    signal_time: datetime
    decision_time: datetime

    def __post_init__(self) -> None:
        require(type(self.signal_anchor) is ObservedSession, "anchor_required")
        for name in ("feature_cutoff", "signal_time", "decision_time"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        require(
            self.signal_anchor.bar_available_at
            <= self.feature_cutoff
            <= self.signal_time
            <= self.decision_time,
            "future_signal_input_or_incomplete_bar",
        )

    @classmethod
    def from_signal(
        cls, signal: SignalCandidate, anchor: ObservedSession, decision_time: datetime
    ) -> "SignalChronology":
        require(signal.context.ticker == anchor.ticker, "signal_ticker_mismatch")
        require(signal.context.data_cutoff == anchor.bar_available_at, "signal_anchor_mismatch")
        require(not signal.rejected_quality_flags, "signal_quality_blocked")
        return cls(anchor, signal.context.data_cutoff, signal.context.as_of, decision_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_anchor": self.signal_anchor.to_dict(),
            "feature_cutoff": self.feature_cutoff.isoformat(),
            "signal_time": self.signal_time.isoformat(),
            "decision_time": self.decision_time.isoformat(),
        }


@dataclass(frozen=True)
class EntryWindow:
    chronology: SignalChronology
    sessions: tuple[ObservedSession, ...]

    def __post_init__(self) -> None:
        validate_sessions(self.sessions)
        require(self.chronology.signal_anchor in self.sessions, "anchor_not_in_calendar")

    @property
    def eligible_execution_session(self) -> ObservedSession | None:
        index = self.sessions.index(self.chronology.signal_anchor) + 1
        return self.sessions[index] if index < len(self.sessions) else None


class IntentPurpose(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"


@dataclass(frozen=True)
class TradeIntent:
    """An explicitly authored strategy output, not a conversion from model direction."""

    signal_id: str
    strategy_policy_id: str
    contract_id: str
    chronology: SignalChronology
    purpose: IntentPurpose
    order_side: OrderSide
    position_side: PositionSide

    def __post_init__(self) -> None:
        for value in (self.signal_id, self.strategy_policy_id, self.contract_id):
            sha(value)
        require(type(self.chronology) is SignalChronology, "chronology_required")
        require(
            type(self.purpose) is IntentPurpose
            and type(self.order_side) is OrderSide
            and type(self.position_side) is PositionSide,
            "invalid_intent_enum",
        )
        require(self.position_side == PositionSide.LONG, "cash_equity_long_only")
        require(
            self.order_side
            == (OrderSide.BUY if self.purpose == IntentPurpose.ENTRY else OrderSide.SELL),
            "intent_side_mismatch",
        )

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "signal_id": self.signal_id,
                "strategy_policy_id": self.strategy_policy_id,
                "contract_id": self.contract_id,
                "chronology": self.chronology.to_dict(),
                "purpose": self.purpose,
                "order_side": self.order_side,
                "position_side": self.position_side,
            }
        )


class EntryStatus(StrEnum):
    ELIGIBLE = "eligible_not_filled"
    UNFILLED = "unfilled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EntryAssessment:
    status: EntryStatus
    reason: str
    eligible_execution_session: date | None


def assess_entry(
    contract: BacktestContract,
    window: EntryWindow,
    *,
    attempted_session: ObservedSession | None,
    previous: DailyBar,
    candidate: DailyBar | None,
    quality_snapshot: FrozenJSON,
    observed_at: datetime,
) -> EntryAssessment:
    """Execution-side preflight only. The caller supplies already revealed bars.

    No price selection, amount calculation, order creation, retries or future reads.
    Passing preflight is not evidence of actual liquidity and never creates a Fill.
    """
    eligible = window.eligible_execution_session
    target = eligible.trading_date if eligible else None

    def outcome(status: EntryStatus, reason: str) -> EntryAssessment:
        return EntryAssessment(status, reason, target)

    anchor = window.chronology.signal_anchor
    previous.__post_init__()
    require(
        previous.ticker == anchor.ticker and previous.trading_date == anchor.trading_date,
        "anchor_bar_mismatch",
    )
    if attempted_session and attempted_session.trading_date <= anchor.trading_date:
        return outcome(EntryStatus.REJECTED, "same_or_prior_session_execution")
    if eligible is None:
        require(attempted_session is None and candidate is None, "no_eligible_session")
        return outcome(EntryStatus.UNFILLED, "missing_next_session")
    if attempted_session != eligible:
        return outcome(EntryStatus.REJECTED, "entry_retry_forbidden")
    if window.chronology.decision_time >= eligible.open_at:
        return outcome(EntryStatus.REJECTED, "decision_not_before_next_open")
    p = contract.to_dict()
    if not (
        p["start"]
        <= anchor.trading_date.isoformat()
        <= eligible.trading_date.isoformat()
        <= p["end"]
    ):
        return outcome(EntryStatus.UNFILLED, "outside_contract_period")
    require(utc(observed_at) >= eligible.bar_available_at, "future_bar_access")
    if candidate is None:
        return outcome(EntryStatus.UNFILLED, "next_bar_unavailable")
    require(
        candidate.ticker == eligible.ticker and candidate.trading_date == eligible.trading_date,
        "execution_bar_mismatch",
    )
    try:
        candidate.__post_init__()
    except ValueError:
        return outcome(EntryStatus.UNFILLED, "invalid_next_bar")
    if candidate.volume == 0 or candidate.trading_value == 0:
        return outcome(EntryStatus.UNFILLED, "no_trade_bar")
    if candidate.open <= 0:
        return outcome(EntryStatus.UNFILLED, "invalid_next_open")
    if (
        (previous.provider, previous.market, previous.adjustment)
        != (candidate.provider, candidate.market, candidate.adjustment)
        or candidate.adjustment != "unadjusted"
        or candidate.market != "KOSPI"
    ):
        return outcome(EntryStatus.UNFILLED, "unsupported_or_mixed_price_basis")
    quality = quality_snapshot.to_dict()
    require(
        {"quality", "session_basis", "unavailable_dates"} <= set(quality),
        "quality_context_required",
    )
    require(
        quality["session_basis"] in {"verified_sessions", "observed_bars_unverified"},
        "invalid_session_basis",
    )
    manifest = validate_quality(quality["quality"])
    if quality["session_basis"] == "verified_sessions":
        require(
            covered(manifest, candidate.ticker, previous.trading_date, candidate.trading_date),
            "quality_coverage_missing",
        )
    require(type(quality.get("quality_flags")) is list, "quality_flags_required")
    if not set(quality["quality_flags"]) <= set(p["quality"]["allowed_research_flags"]):
        return outcome(EntryStatus.UNFILLED, "quality_blocked")
    # Hard feature-engine failures cannot be waived by a quality flag allowlist.
    try:
        check_segment([previous, candidate], quality)
    except AnalysisError as error:
        return outcome(EntryStatus.UNFILLED, str(error))
    liquidity = p["liquidity"]
    if candidate.volume < liquidity["minimum_volume"] or candidate.trading_value < Decimal(
        liquidity["minimum_trading_value_krw"]
    ):
        return outcome(EntryStatus.UNFILLED, "liquidity_minimum_not_met")
    return outcome(EntryStatus.ELIGIBLE, "preflight_only_no_liquidity_guarantee")
