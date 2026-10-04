"""Frozen session evidence and a prefix-only adapter to existing HGB integration."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.entry_contract import ObservedSession
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.data.learning import AnalysisError, canonical, digest
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.data.snapshot import validate_quality
from donghak_stock_vision.models.artifact_resolver import TrustedHGBResolver
from donghak_stock_vision.models.inference import HGBArtifact
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.signals.domain import SignalCandidate, SignalPolicy
from donghak_stock_vision.signals.integration import (
    HGBResearchSignalService,
    ResearchSignalRequest,
    resolve_snapshot,
)


@dataclass(frozen=True)
class BaselineSession:
    session: ObservedSession
    bar: DailyBar | None
    quality: FrozenJSON
    settlement_date: date | None
    settlement_evidence_id: str | None

    def __post_init__(self) -> None:
        require(type(self.session) is ObservedSession, "session_required")
        q = self.quality.to_dict()
        require(
            {"quality", "quality_flags", "session_basis", "unavailable_dates"} <= set(q),
            "quality_context_required",
        )
        validate_quality(q["quality"])
        require(
            type(q["quality_flags"]) is list
            and all(type(flag) is str for flag in q["quality_flags"]),
            "quality_flags_required",
        )
        require(
            q["session_basis"] in {"verified_sessions", "observed_bars_unverified"},
            "invalid_session_basis",
        )
        if self.bar is not None:
            self.bar.__post_init__()
            require(
                self.bar.ticker == self.session.ticker
                and self.bar.trading_date == self.session.trading_date,
                "session_bar_mismatch",
            )
        if self.settlement_date is not None:
            require(type(self.settlement_date) is date, "settlement_date_required")
            require(self.settlement_date >= self.session.trading_date, "settlement_before_session")
        if self.settlement_evidence_id is not None:
            sha(self.settlement_evidence_id)
        require(
            (self.settlement_date is None) == (self.settlement_evidence_id is None),
            "incomplete_settlement_evidence",
        )

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "session": self.session.to_dict(),
                "bar": self.bar.to_dict() if self.bar else None,
                "quality": self.quality.to_dict(),
                "settlement_date": self.settlement_date.isoformat()
                if self.settlement_date
                else None,
                "settlement_evidence": self.settlement_evidence_id,
            }
        )


@dataclass(frozen=True)
class BaselineSignalFrame:
    signal: SignalCandidate
    values: tuple[float, ...]


class BaselineSignalSource(Protocol):
    """The runner supplies only the published ticker prefix, never the complete tape."""

    @property
    def up_artifact(self) -> HGBArtifact: ...

    @property
    def policy(self) -> SignalPolicy: ...

    @property
    def fingerprint(self) -> str: ...

    def research(
        self, prefix: tuple[BaselineSession, ...], as_of: datetime
    ) -> BaselineSignalFrame: ...


@dataclass(frozen=True)
class PrefixMarketStore:
    bars: tuple[DailyBar, ...]

    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        return [b for b in self.bars if b.ticker == ticker and start <= b.trading_date <= end]

    def latest(self, ticker: str, start: date, end: date) -> date | None:
        bars = self.read(ticker, start, end)
        return bars[-1].trading_date if bars else None

    def archive(self, run_id: str, provider: str, ticker: str, page: RawPage) -> int:
        raise ValueError("read_only_prefix")

    def save(self, bars: Sequence[DailyBar], raw_ids: Sequence[int]) -> int:
        raise ValueError("read_only_prefix")

    def record_run(
        self,
        run_id: str,
        ticker: str,
        start: date,
        end: date,
        status: str,
        rows: int,
        error: str | None,
    ) -> None:
        raise ValueError("read_only_prefix")

    def check_integrity(self) -> list[str]:
        return []


class HGBBaselineSignals:
    """Public trusted HGB service, isolated from live DBs by an immutable prefix store."""

    def __init__(
        self,
        resolver: TrustedHGBResolver,
        policy: SignalPolicy,
        source_cutoff: datetime,
        calendar: ResearchCalendarPolicy | None,
    ) -> None:
        self.resolver, self.policy = resolver, policy
        self.source_cutoff, self.calendar = utc(source_cutoff), calendar
        up, down = resolver.verify()  # Integrity only; no prediction or fitting here.
        self.up_artifact = up.artifact
        self.down_artifact = down.artifact

    @property
    def fingerprint(self) -> str:
        return digest(
            [
                self.up_artifact.fingerprint,
                self.down_artifact.fingerprint,
                self.policy.fingerprint,
                self.source_cutoff.isoformat(),
                self.calendar.to_dict() if self.calendar else None,
            ]
        )

    def research(self, prefix: tuple[BaselineSession, ...], as_of: datetime) -> BaselineSignalFrame:
        require(bool(prefix), "empty_signal_prefix")
        require(all(r.session.bar_available_at <= as_of for r in prefix), "future_signal_prefix")
        if any(r.bar is None for r in prefix[-12:]):
            raise AnalysisError("missing_warmup_session")
        bars = tuple(r.bar for r in prefix if r.bar is not None)
        require(bool(bars), "missing_signal_bars")
        store = PrefixMarketStore(bars)
        request = ResearchSignalRequest(
            prefix[-1].session.ticker,
            as_of,
            prefix[0].session.trading_date,
            self.source_cutoff,
            True,
            bars[-1].provider == "fake",
            self.calendar,
            quality_json=canonical(prefix[-1].quality.to_dict()["quality"]),
        )
        resolved = resolve_snapshot(store, request)
        result = HGBResearchSignalService(store, self.resolver).research(request, self.policy)
        require(
            result.up_artifact == self.up_artifact and result.down_artifact == self.down_artifact,
            "artifact_changed_during_run",
        )
        return BaselineSignalFrame(result.candidate, resolved.values)
