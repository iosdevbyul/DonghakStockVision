"""Immutable public response schema for retained daily research results."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from donghak_stock_vision.data.learning import THRESHOLD


class DailyResearchReaderError(ValueError):
    """Fixed public error codes; never copy underlying SQL/path/owner exceptions."""


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchPeriod(PublicModel):
    start: date
    end: date


class ResearchSourceReference(PublicModel):
    referenceId: str
    tradingDate: date
    origin: Literal["real_provider_https_local_capture", "authorized_provider_mock_fixture"]


class DailyResearchRecord(PublicModel):
    analysisId: str | None
    jobReference: str
    ticker: str
    tradingDate: date
    analyzedAt: datetime | None
    modelId: str
    modelVersion: str
    status: Literal["completed", "blocked", "failed", "pending"]
    reasonCode: str | None
    attempt: int | None
    dataOrigin: Literal["real", "synthetic_fixture"]
    usageRestriction: Literal["research_only", "synthetic_test_only"]
    requestedPeriod: ResearchPeriod
    observedPeriod: ResearchPeriod | None
    inputFingerprint: str
    inputFingerprintScope: Literal["selected_job_universe"] = "selected_job_universe"
    sources: tuple[ResearchSourceReference, ...]
    researchScore: float | None
    researchContext: Literal["prior_decline", "prior_rise", "flat"] | None
    researchSignal: Literal["up", "down", "neutral", "unavailable"]
    displayThreshold: float = THRESHOLD
    scoreMeaning: Literal["uncalibrated_conditional_research_score"] = (
        "uncalibrated_conditional_research_score"
    )
    verification: Literal["retained_artifact_integrity_not_fresh_source_replay"] = (
        "retained_artifact_integrity_not_fresh_source_replay"
    )
    independentlyAuthenticatedChronology: Literal[False] = False
    independentlyAuthenticatedCustody: Literal[False] = False
    forwardPredictionEligible: Literal[False] = False
    pitQualified: Literal[False] = False
    performanceApproved: Literal[False] = False
    executionAuthorized: Literal[False] = False
    performanceMetrics: None = None


class DailyResearchSeries(PublicModel):
    schemaVersion: Literal["daily_research_series_v1"] = "daily_research_series_v1"
    ticker: str
    availability: Literal["recorded", "no_recorded_analysis"]
    records: tuple[DailyResearchRecord, ...]
    totalRecords: int
    offset: int
    limit: int
    lastSuccessfulAnalyzedAt: datetime | None
    lastSuccessfulTradingDate: date | None
    lastSuccessfulDataOrigin: Literal["real", "synthetic_fixture"] | None
    timestampAuthority: Literal["local_operational_clock_not_authenticated"] = (
        "local_operational_clock_not_authenticated"
    )
