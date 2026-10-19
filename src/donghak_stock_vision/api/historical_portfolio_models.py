"""Versioned read schemas; nested source fields retain validated report names."""

from typing import Any, Literal

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
