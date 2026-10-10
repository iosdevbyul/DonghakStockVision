"""Opt-in, validated publication of existing per-security inference records.

Never converts global model performance metrics into ticker predictions.
"""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublishedAnalysisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    ticker: str = Field(pattern=r"^[0-9A-Z]{6}$")
    analyzedAt: datetime
    dataThrough: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    modelId: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    direction: Literal["up", "down", "neutral"]
    score: float | None
    validation: Literal["verified", "blocked", "pending"]
    explanation: str | None

    @model_validator(mode="after")
    def verify_dates(self) -> "PublishedAnalysisRecord":
        date.fromisoformat(self.dataThrough)
        if self.analyzedAt.tzinfo is None or self.analyzedAt.utcoffset() is None:
            raise ValueError("analysis timestamp must have timezone")
        if self.score is not None and not 0 <= self.score <= 1:
            raise ValueError("score outside [0,1]")
        if self.validation != "verified" and self.score is not None:
            raise ValueError("only reviewed, verified records may publish numeric scores")
        return self


class PublishedStockAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ticker: str = Field(pattern=r"^[0-9A-Z]{6}$")
    records: tuple[PublishedAnalysisRecord, ...] = ()

    @model_validator(mode="after")
    def verify_membership(self) -> "PublishedStockAnalysis":
        if len(self.records) > 500:
            raise ValueError("too many records")
        ids = set()
        for record in self.records:
            if record.ticker != self.ticker or record.id in ids:
                raise ValueError("record ticker or ID mismatch")
            ids.add(record.id)
        return self


class AnalysisPublication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schemaVersion: Literal["published_stock_analysis_v1"]
    sources: tuple[PublishedStockAnalysis, ...]

    @model_validator(mode="after")
    def verify_sources(self) -> "AnalysisPublication":
        tickers = [source.ticker for source in self.sources]
        if len(set(tickers)) != len(tickers):
            raise ValueError("duplicate ticker publication")
        return self


class AnalysisPublicationReader:
    """Startup-loaded immutable publication; only explicitly published results."""

    def __init__(self, publication: AnalysisPublication) -> None:
        self._by_ticker = {source.ticker: source for source in publication.sources}

    @classmethod
    def from_path(cls, path: Path) -> "AnalysisPublicationReader":
        # Validation errors intentionally prevent startup, never substitute mock records.
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(AnalysisPublication.model_validate(payload))

    def series(self, ticker: str) -> PublishedStockAnalysis:
        return self._by_ticker.get(ticker, PublishedStockAnalysis(ticker=ticker, records=()))
