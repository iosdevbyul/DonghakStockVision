"""Loopback-only, opt-in read-only resources. No HTTP publication operations."""

import re
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from donghak_stock_vision.api.historical_portfolio_models import (
    PortfolioPage,
    PortfolioSummary,
    PortfolioTicker,
    PortfolioTrades,
)
from donghak_stock_vision.api.historical_portfolios import (
    PortfolioPublicationReader,
    PublicationError,
)


def portfolio_router(reader: PortfolioPublicationReader | None) -> APIRouter:
    def local_reader(request: Request) -> PortfolioPublicationReader:
        if reader is None:
            raise HTTPException(503, detail={"code": "archive_unavailable"})
        if len(request.scope.get("path", "")) + len(request.scope.get("query_string", b"")) > 4096:
            raise HTTPException(413, detail={"code": "oversized_request"})
        local = {"127.0.0.1", "::1", "localhost"}
        origin = request.headers.get("origin")
        try:
            origin_host = urlsplit(origin).hostname if origin is not None else None
        except ValueError:
            raise HTTPException(403, detail={"code": "local_research_access_required"}) from None
        if (
            request.client is None
            or request.client.host not in {"127.0.0.1", "::1"}
            or request.url.hostname not in local
            or (origin is not None and origin_host not in local)
        ):
            raise HTTPException(403, detail={"code": "local_research_access_required"})
        return reader

    router = APIRouter(
        prefix="/api/v1/research/historical-portfolios", tags=["historical research"]
    )
    Source = Annotated[PortfolioPublicationReader, Depends(local_reader)]

    def page(offset: str, limit: str) -> tuple[int, int]:
        if not (re.fullmatch(r"[0-9]{1,6}", offset) and re.fullmatch(r"[0-9]{1,2}", limit)):
            raise HTTPException(422, detail={"code": "invalid_pagination"})
        return int(offset), int(limit)

    def call(source: PortfolioPublicationReader, operation: str, *args: Any) -> dict[str, Any]:
        try:
            result: dict[str, Any] = getattr(source, operation)(*args)
            return result
        except PublicationError as error:
            raise HTTPException(error.status, detail={"code": error.code}) from None
        except (KeyError, TypeError, ValueError, RecursionError, OSError):
            raise HTTPException(503, detail={"code": "report_projection_incomplete"}) from None

    @router.get("", response_model=PortfolioPage)
    def collection(source: Source, offset: str = "0", limit: str = "10") -> dict[str, Any]:
        return call(source, "collection", *page(offset, limit))

    @router.get("/{report_id}", response_model=PortfolioSummary)
    def summary(report_id: str, source: Source) -> dict[str, Any]:
        return call(source, "summary", report_id)

    @router.get("/{report_id}/tickers/{ticker}", response_model=PortfolioTicker)
    def ticker_detail(
        report_id: str, ticker: str, source: Source, offset: str = "0", limit: str = "10"
    ) -> dict[str, Any]:
        return call(source, "ticker", report_id, ticker, *page(offset, limit))

    @router.get("/{report_id}/trades", response_model=PortfolioTrades)
    def trades(
        report_id: str,
        source: Source,
        ticker: str | None = None,
        offset: str = "0",
        limit: str = "10",
    ) -> dict[str, Any]:
        return call(source, "trades", report_id, ticker, *page(offset, limit))

    return router
