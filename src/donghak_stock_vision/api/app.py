"""FastAPI transport for immutable research snapshots."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware

from donghak_stock_vision.api.daily_research import (
    DailyResearchReader,
    DailyResearchReaderError,
    DailyResearchSeries,
)
from donghak_stock_vision.api.historical_portfolio_routes import portfolio_router
from donghak_stock_vision.api.historical_portfolios import PortfolioPublicationReader
from donghak_stock_vision.api.models import (
    BaselineBacktestSnapshot,
    CoverageSummary,
    EvidenceDetail,
    EvidenceLayerId,
    EvidenceLayerSummary,
    HealthPayload,
    ModelDetail,
    ModelDirection,
    ModelSummary,
    ProjectStatus,
    StockDetail,
    StockSummary,
)
from donghak_stock_vision.api.ohlcv import (
    OhlcvMarketReader,
    OhlcvSeries,
    OperationalCatalog,
    OperationalSnapshotSummary,
    PinnedOperationalReader,
)
from donghak_stock_vision.api.operations import (
    AlertsPayload,
    FreshnessPayload,
    OperationsReader,
    RunsPayload,
)
from donghak_stock_vision.api.screener import ScreenerSnapshot, published_screener
from donghak_stock_vision.api.security_metadata import (
    MetadataEnvelope,
    SecurityMetadataDetail,
    SecurityMetadataReader,
)
from donghak_stock_vision.api.service import ResearchAPIReader
from donghak_stock_vision.api.stock_analysis import (
    AnalysisPublicationReader,
    PublishedStockAnalysis,
)
from donghak_stock_vision.storage.krx_operations_diagnostics import summarize


def create_app(
    reader: ResearchAPIReader | None = None,
    ohlcv_reader: OhlcvMarketReader | None = None,
    operational_reader: PinnedOperationalReader | None = None,
    *,
    analysis_reader: AnalysisPublicationReader | None = None,
    daily_research_reader: DailyResearchReader | None = None,
    operations_reader: OperationsReader | None = None,
    krx_diagnostics_reports: (
        Callable[[], tuple[Mapping[str, Any], Mapping[str, Any]]] | None
    ) = None,
    daily_research_report: Callable[[], Mapping[str, Any]] | None = None,
    security_metadata_reader: SecurityMetadataReader | None = None,
    portfolio_reader: PortfolioPublicationReader | None = None,
    allowed_origins: Sequence[str] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ),
) -> FastAPI:
    if security_metadata_reader is not None:
        security_metadata_reader.validate_research_reader(reader)
    app = FastAPI(
        title="DonghakStockVision Research API",
        version="1.0.0",
        description="Read-only transport for reviewed research snapshots.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type"],
    )

    def require_reader() -> ResearchAPIReader:
        if reader is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="research snapshot reader is not configured",
            )
        return reader

    @app.get("/api/v1/health", response_model=HealthPayload)
    def health() -> HealthPayload:
        return HealthPayload(status="ok", service="DonghakStockVision", mode="read_only")

    @app.get("/api/v1/project/status", response_model=ProjectStatus)
    def project_status(
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> ProjectStatus:
        return source.project_status()

    @app.get("/api/v1/research/coverage", response_model=CoverageSummary)
    def coverage(
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> CoverageSummary:
        return source.coverage_summary()

    @app.get("/api/v1/models", response_model=list[ModelSummary])
    def models(
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> tuple[ModelSummary, ...]:
        return source.model_summaries()

    @app.get("/api/v1/models/{direction}", response_model=ModelDetail)
    def model_detail(
        direction: ModelDirection,
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> ModelDetail:
        model = source.model_detail(direction)
        if model is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model not found")
        return model

    @app.get("/api/v1/evidence", response_model=list[EvidenceLayerSummary])
    def evidence(
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> tuple[EvidenceLayerSummary, ...]:
        return source.evidence_layers()

    @app.get("/api/v1/evidence/{layer}", response_model=EvidenceDetail)
    def evidence_detail(
        layer: EvidenceLayerId,
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> EvidenceDetail:
        item = source.evidence_detail(layer)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="evidence layer not found",
            )
        return item

    @app.get("/api/v1/backtests/baseline", response_model=BaselineBacktestSnapshot)
    def baseline(
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> BaselineBacktestSnapshot:
        return source.baseline_backtest()

    @app.get("/api/v1/stocks", response_model=list[StockSummary])
    def stocks(
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> tuple[StockSummary, ...]:
        return source.stocks()

    def require_metadata() -> SecurityMetadataReader:
        if security_metadata_reader is None:
            raise HTTPException(status_code=503, detail="security metadata is not configured")
        return security_metadata_reader

    @app.get("/api/v1/security-metadata", response_model=MetadataEnvelope)
    def security_metadata(
        source: Annotated[SecurityMetadataReader, Depends(require_metadata)],
    ) -> MetadataEnvelope:
        return source.envelope

    @app.get("/api/v1/security-metadata/{ticker}", response_model=SecurityMetadataDetail)
    def security_metadata_detail(
        ticker: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")],
        source: Annotated[SecurityMetadataReader, Depends(require_metadata)],
    ) -> SecurityMetadataDetail:
        item = source.detail(ticker)
        if item is None:
            raise HTTPException(status_code=404, detail="security metadata not found")
        return item

    @app.get("/api/v1/analysis/screener", response_model=ScreenerSnapshot)
    def screener() -> ScreenerSnapshot:
        if analysis_reader is None:
            raise HTTPException(
                status_code=503,
                detail="reviewed analysis publication not configured",
            )
        return published_screener(analysis_reader, reader)

    @app.get("/api/v1/stocks/{ticker}/ohlcv", response_model=OhlcvSeries)
    def ohlcv(ticker: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")]) -> OhlcvSeries:
        if ohlcv_reader is None:
            raise HTTPException(status_code=503, detail="verified market reader is not configured")
        series = ohlcv_reader.series(ticker)
        if series is None:
            raise HTTPException(status_code=404, detail="stock bars not found")
        return series

    @app.get("/api/v1/operational/summary", response_model=OperationalSnapshotSummary)
    def operational_summary() -> OperationalSnapshotSummary:
        if operational_reader is None:
            raise HTTPException(
                status_code=503, detail="pinned operational snapshot not configured"
            )
        return operational_reader.summary()

    @app.get("/api/v1/operational/stocks", response_model=OperationalCatalog)
    def operational_stocks(
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> OperationalCatalog:
        if operational_reader is None:
            raise HTTPException(
                status_code=503, detail="pinned operational snapshot not configured"
            )
        return operational_reader.catalog(offset, limit)

    @app.get("/api/v1/operational/stocks/{ticker}/ohlcv", response_model=OhlcvSeries)
    def operational_ohlcv(ticker: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")]) -> OhlcvSeries:
        if operational_reader is None:
            raise HTTPException(
                status_code=503, detail="pinned operational snapshot not configured"
            )
        series = operational_reader.series(ticker)
        if series is None:
            raise HTTPException(status_code=404, detail="operational stock bars not found")
        return series

    @app.get("/api/v1/stocks/{ticker}/research-analysis", response_model=DailyResearchSeries)
    def stock_research_analysis(
        ticker: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")],
        trading_date: Annotated[date | None, Query(alias="tradingDate")] = None,
        start: Annotated[date | None, Query(alias="fromDate")] = None,
        end: Annotated[date | None, Query(alias="toDate")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> DailyResearchSeries:
        if daily_research_reader is None:
            raise HTTPException(status_code=503, detail="daily research snapshot not configured")
        try:
            return daily_research_reader.series(
                ticker,
                trading_date=trading_date,
                start=start,
                end=end,
                limit=limit,
                offset=offset,
            )
        except DailyResearchReaderError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    @app.get("/api/v1/stocks/{ticker}/analysis", response_model=PublishedStockAnalysis)
    def stock_analysis(
        ticker: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")],
    ) -> PublishedStockAnalysis:
        if analysis_reader is None:
            raise HTTPException(
                status_code=503,
                detail="reviewed analysis publication not configured",
            )
        return analysis_reader.series(ticker)

    @app.get("/api/v1/stocks/{ticker}", response_model=StockDetail)
    def stock_detail(
        ticker: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")],
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> StockDetail:
        stock = source.stock_detail(ticker)
        if stock is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="stock not found")
        return stock

    operations = operations_reader or OperationsReader(research=reader, market=ohlcv_reader)

    @app.get("/api/v1/operations/krx-scheduler/diagnostics")
    def krx_scheduler_diagnostics() -> dict[str, Any]:
        """Expose only explicitly supplied read-only scheduler diagnostic reports."""
        if krx_diagnostics_reports is None:
            raise HTTPException(status_code=503, detail="KRX diagnostics not configured")
        backlog_report, preflight_report = krx_diagnostics_reports()
        return summarize(
            backlog_report,
            preflight_report,
            daily_research_report() if daily_research_report is not None else None,
        )

    @app.get("/api/v1/operations/runs", response_model=RunsPayload)
    def operation_runs(
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> RunsPayload:
        return operations.runs(offset, limit)

    @app.get("/api/v1/operations/freshness", response_model=FreshnessPayload)
    def operation_freshness() -> FreshnessPayload:
        return operations.freshness()

    @app.get("/api/v1/operations/alerts", response_model=AlertsPayload)
    def operation_alerts(
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> AlertsPayload:
        return operations.alerts(offset, limit)

    app.include_router(portfolio_router(portfolio_reader))
    return app
