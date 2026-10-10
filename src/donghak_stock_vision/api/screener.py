"""Aggregate only explicitly published per-stock analysis records for read-only screening."""
from pydantic import BaseModel, ConfigDict

from donghak_stock_vision.api.service import ResearchAPIReader
from donghak_stock_vision.api.stock_analysis import AnalysisPublicationReader


class ScreenerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker: str
    name: str | None
    analysisId: str
    modelId: str
    modelVersion: str
    direction: str
    score: float | None
    validation: str
    dataThrough: str
    analyzedAt: str


class ScreenerSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: tuple[ScreenerEntry, ...]


def published_screener(
    publication: AnalysisPublicationReader,
    research: ResearchAPIReader | None,
) -> ScreenerSnapshot:
    names = {stock.ticker: stock.name for stock in research.stocks()} if research else {}
    entries = []
    for series in publication.published_series():
        for record in series.records:
            entries.append(
                ScreenerEntry(
                    ticker=series.ticker,
                    name=names.get(series.ticker),
                    analysisId=record.id,
                    modelId=record.modelId,
                    modelVersion=record.modelVersion,
                    direction=record.direction,
                    score=record.score,
                    validation=record.validation,
                    dataThrough=record.dataThrough,
                    analyzedAt=record.analyzedAt.isoformat(),
                )
            )
            if len(entries) > 5000:
                raise ValueError("reviewed publication exceeds screener response limit")
    return ScreenerSnapshot(entries=tuple(entries))
