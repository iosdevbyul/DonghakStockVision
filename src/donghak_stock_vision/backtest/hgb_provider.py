"""Bounded public HGB acquisition into a separate immutable run-output store."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from donghak_stock_vision.data.learning import canonical, digest, timestamp
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.models.artifact_resolver import TrustedHGBResolver
from donghak_stock_vision.signals.domain import SignalPolicy
from donghak_stock_vision.signals.hgb_trading import VERSION, adapt
from donghak_stock_vision.signals.integration import HGBResearchSignalService, ResearchSignalRequest
from donghak_stock_vision.storage.base import MarketDataStore


@dataclass(frozen=True)
class HGBHistoricalProvider:
    resolver: TrustedHGBResolver
    signal_policy: SignalPolicy
    observed_at: datetime

    def __post_init__(self) -> None:
        timestamp(self.observed_at)

    @property
    def model_id(self) -> str:
        # A model-pair reference, explicitly not a fabricated linear model ID.
        return digest(dict(zip(("up", "down"), self.resolver.pins, strict=True)))

    def capture(
        self,
        market: MarketDataStore,
        *,
        ticker: str,
        as_of: datetime,
        history_start: date,
        source_cutoff: datetime,
        quality: dict[str, Any] | None,
        calendar: ResearchCalendarPolicy | None,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        if self.observed_at < source_cutoff:
            raise ValueError("hgb_observation_before_source_cutoff")
        result = HGBResearchSignalService(market, self.resolver).research(
            ResearchSignalRequest(
                ticker,
                as_of,
                history_start,
                source_cutoff,
                True,
                synthetic,
                calendar,
                canonical(quality),
            ),
            self.signal_policy,
        )
        signal = adapt(result)
        return {
            "contract": VERSION,
            "model_id": signal["model_version"],
            "snapshot_id": signal["snapshot_id"],
            "analysis_id": digest(signal),
            "signal": signal,
            "snapshot": result.to_dict()["snapshot"],
            "hgb_evidence": result.to_dict(),
            "created_at": {"model": None, "signal": self.observed_at.isoformat()},
        }
