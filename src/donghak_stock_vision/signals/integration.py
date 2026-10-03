"""Explicit historical HGB integration; no persistence, fitting or trading."""

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from donghak_stock_vision.data.learning import (
    FEATURE_VERSION,
    FEATURES,
    AnalysisError,
    canonical,
    digest,
    timestamp,
)
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.schema import SEOUL, validate_range, validate_ticker
from donghak_stock_vision.data.snapshot import capture, snapshot_bars
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.artifact_resolver import TrustedHGBResolver
from donghak_stock_vision.models.inference import HGBArtifact
from donghak_stock_vision.signals.domain import (
    SignalCandidate,
    SignalContext,
    SignalPolicy,
    build_signal,
)
from donghak_stock_vision.signals.research import generate_signal
from donghak_stock_vision.storage.base import MarketDataStore


@dataclass(frozen=True)
class ResearchSignalRequest:
    ticker: str
    as_of: datetime
    history_start: date
    source_cutoff: datetime
    acknowledge_research_limitations: bool
    synthetic: bool
    research_calendar: ResearchCalendarPolicy | None
    quality_json: str = "null"

    def __post_init__(self) -> None:
        try:
            validate_ticker(self.ticker)
            object.__setattr__(self, "as_of", timestamp(self.as_of))
            object.__setattr__(self, "source_cutoff", timestamp(self.source_cutoff))
            validate_range(self.history_start, self.anchor_date)
            if (
                type(self.synthetic) is not bool
                or type(self.acknowledge_research_limitations) is not bool
            ):
                raise ValueError
            if (
                self.research_calendar is not None
                and type(self.research_calendar) is not ResearchCalendarPolicy
            ):
                raise ValueError
            quality = json.loads(self.quality_json)
            if quality is not None and not isinstance(quality, dict):
                raise ValueError
            if not self.acknowledge_research_limitations:
                raise AnalysisError("research_limitations_acknowledgement_required")
        except (ValueError, TypeError, AttributeError) as error:
            if isinstance(error, AnalysisError):
                raise
            raise AnalysisError("invalid_research_signal_request") from error

    @property
    def anchor_date(self) -> date:
        # Complete daily bar only after next Seoul midnight; never same-day OHLC.
        return self.as_of.astimezone(SEOUL).date() - timedelta(days=1)


@dataclass(frozen=True)
class ResolvedSignalInput:
    context: SignalContext
    values: tuple[float, ...]
    snapshot_json: str


def resolve_snapshot(store: MarketDataStore, request: ResearchSignalRequest) -> ResolvedSignalInput:
    if request.research_calendar and request.research_calendar.excludes(request.anchor_date):
        raise AnalysisError("known_incomplete_trading_date")
    snapshot = capture(
        store,
        [request.ticker],
        request.history_start,
        request.anchor_date,
        request.source_cutoff,
        "historical_research",
        quality=json.loads(request.quality_json),
        acknowledge=request.acknowledge_research_limitations,
        synthetic=request.synthetic,
        research_calendar=request.research_calendar,
    )
    bars = snapshot_bars(snapshot, request.ticker)
    if not bars or bars[-1].trading_date != request.anchor_date:
        raise AnalysisError("missing_anchor_bar")
    # Preserve the previous-edge check: 11 feature bars plus one quality edge.
    bars = bars[-12:]
    snapshot["rows"] = {request.ticker: [b.to_dict() for b in bars]}
    snapshot["read_start"] = request.history_start.isoformat()
    snapshot["start"] = bars[0].trading_date.isoformat()
    values = feature_vector(bars, snapshot)
    snapshot_id = digest(snapshot)
    context = SignalContext(
        ticker=request.ticker,
        as_of=request.as_of,
        data_cutoff=datetime.combine(request.anchor_date + timedelta(days=1), time.min, SEOUL),
        dataset_id=digest({"snapshot_id": snapshot_id, "feature_version": FEATURE_VERSION}),
        snapshot_id=snapshot_id,
        input_hash=digest([b.to_dict() for b in bars]),
        feature_hash=digest([list(FEATURES), list(values)]),
        population="prior_decline" if values[2] < 0 else "prior_rise" if values[2] > 0 else "flat",
        quality_flags=tuple(snapshot["quality_flags"]),
        mode="historical_research",
        data_origin=snapshot["data_origin"],
    )
    return ResolvedSignalInput(context, values, canonical(snapshot))


@dataclass(frozen=True)
class IntegratedResearchSignal:
    candidate: SignalCandidate
    up_artifact: HGBArtifact
    down_artifact: HGBArtifact
    snapshot_json: str
    anchor_date: date

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "signal_fingerprint": self.candidate.fingerprint,
            "models": {"up": asdict(self.up_artifact), "down": asdict(self.down_artifact)},
            "snapshot": json.loads(self.snapshot_json),
            "anchor_date": self.anchor_date.isoformat(),
            "feature_version": FEATURE_VERSION,
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())


class HGBResearchSignalService:
    def __init__(self, market: MarketDataStore, artifacts: TrustedHGBResolver) -> None:
        self.market = market
        self.artifacts = artifacts

    def research(
        self, request: ResearchSignalRequest, policy: SignalPolicy
    ) -> IntegratedResearchSignal:
        resolved = resolve_snapshot(self.market, request)
        up, down = self.artifacts.verify()
        calendar = canonical(
            request.research_calendar.to_dict() if request.research_calendar else None
        )
        if up.calendar_json != calendar or down.calendar_json != calendar:
            raise AnalysisError("research_calendar_mismatch")
        up_id, down_id = up.artifact.fingerprint, down.artifact.fingerprint
        if policy.rejected_flags(resolved.context):
            candidate = build_signal(
                resolved.context, policy, up_model=up_id, down_model=down_id, up=None, down=None
            )
        else:
            candidate = generate_signal(
                resolved.context,
                policy,
                resolved.values,
                names=FEATURES,
                up_model=up_id,
                down_model=down_id,
                up=up.load(),
                down=down.load(),
            )
        return IntegratedResearchSignal(
            candidate, up.artifact, down.artifact, resolved.snapshot_json, request.anchor_date
        )
