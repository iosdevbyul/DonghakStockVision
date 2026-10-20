"""Versioned read schemas; nested source fields retain validated report names."""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator

from donghak_stock_vision.api.models import FrozenModel


class PortfolioEnvelope(FrozenModel):
    schemaVersion: Literal["published_historical_portfolio_v1"]
    researchOnly: Literal[True]
    operationalEligible: Literal[False]
    executable: Literal[False]
    pitVerified: Literal[False]
    oosVerified: Literal[False]
    investmentSuitabilityVerified: Literal[False]
    brokerageEligible: Literal[False]


class PortfolioSummary(PortfolioEnvelope):
    reportId: str
    publishedAt: str
    resultSchemaVersion: Literal["five_ticker_historical_result_v1"]
    reportSchemaVersion: Literal["five_ticker_historical_report_v1"]
    modelFamily: Literal["linear", "hgb"]
    modelId: str
    modelReferences: dict[str, Any]
    tickers: tuple[str, ...]
    period: dict[str, str]
    initialVirtualCash: str
    finalTotalCash: str
    availableCash: str
    reservedCash: str
    finalPortfolioEquity: str | None
    realizedNetPnl: str
    unrealizedPnl: str | None
    portfolioReturn: str | None
    decisionCount: int
    acceptedOrderCount: int
    fillCount: int
    completedPositionCount: int
    pendingOrderCount: int
    valuationCoverage: dict[str, Any]
    portfolioValuationAvailable: bool
    finalValuation: dict[str, Any]
    costs: dict[str, Any]
    provenance: dict[str, Any]
    archiveTransportHash: str
    resultHash: str
    reportHash: str
    runHash: str
    performanceHash: str
    evidenceScope: str
    dataOrigin: str
    limitations: tuple[str, ...]
    unavailable: dict[str, str]
    archiveVerification: Literal["integrity_verified"]
    reportVerification: Literal["offline_authoritative_replay_passed"]


class PortfolioPage(PortfolioEnvelope):
    items: tuple[dict[str, Any], ...]
    total: int
    offset: int
    limit: int


class PortfolioTrades(PortfolioPage):
    reportId: str


class PortfolioTicker(PortfolioEnvelope):
    reportId: str
    ticker: str
    modelId: str
    modelReferences: dict[str, Any]
    positions: tuple[dict[str, Any], ...]
    realizedNetPnl: str | None
    valuation: dict[str, Any] | None
    unavailable: dict[str, str]
    timeline: tuple[dict[str, Any], ...]
    total: int
    offset: int
    limit: int


class PortfolioComparisonRequest(FrozenModel):
    reportIds: tuple[
        Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")], ...
    ] = Field(min_length=2, max_length=5)

    @field_validator("reportIds")
    @classmethod
    def unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate_report_ids")
        return value


class ComparisonValuationBasis(FrozenModel):
    timestamp: str | None
    stage: str | None
    observedMarkFields: tuple[str, ...] | None
    policy: None
    unavailable: dict[str, str]


class PortfolioComparisonItem(PortfolioSummary):
    tickerCount: int
    signalEvidenceScopes: tuple[str, ...]
    valuationBasis: ComparisonValuationBasis


class PortfolioComparability(FrozenModel):
    periodsDiffer: bool
    valuationTimestampsDiffer: bool | None
    observedMarkFieldsDiffer: bool | None
    valuationPolicyComparison: Literal["unavailable"]
    unavailable: dict[str, str]
    notices: tuple[str, ...]


class PortfolioComparison(FrozenModel):
    schemaVersion: Literal["historical_portfolio_comparison_v1"]
    researchOnly: Literal[True]
    operationalEligible: Literal[False]
    executable: Literal[False]
    pitVerified: Literal[False]
    oosVerified: Literal[False]
    investmentSuitabilityVerified: Literal[False]
    brokerageEligible: Literal[False]
    items: tuple[PortfolioComparisonItem, ...]
    comparability: PortfolioComparability
