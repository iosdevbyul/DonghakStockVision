"""FastAPI transport for immutable research snapshots."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, status
from fastapi.middleware.cors import CORSMiddleware

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
from donghak_stock_vision.api.service import ResearchAPIReader
from donghak_stock_vision.api.stock_analysis import AnalysisPublicationReader, PublishedStockAnalysis


def create_app(
    reader: ResearchAPIReader | None = None,
    *,
    allowed_origins: Sequence[str] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ),
) -> FastAPI:
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

    @app.get("/api/v1/stocks/{ticker}", response_model=StockDetail)
    def stock_detail(
        ticker: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")],
        source: Annotated[ResearchAPIReader, Depends(require_reader)],
    ) -> StockDetail:
        stock = source.stock_detail(ticker)
        if stock is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="stock not found")
        return stock

    return app
