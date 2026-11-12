"""Typed read-only API payloads. These models contain no research logic."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from donghak_stock_vision.data.learning import THRESHOLD

ProjectState = Literal["verified", "in_progress", "blocked", "not_started"]
ModelDirection = Literal["up", "down"]
ModelRole = Literal["baseline_executable", "research_only"]
EvidenceLayerId = Literal[
    "calendar",
    "settlement",
    "dart",
    "security-lifecycle",
    "corporate-actions",
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SummaryMetric(FrozenModel):
    label: str
    value: str
    detail: str


class ResearchCheckpoint(FrozenModel):
    label: str
    state: ProjectState
    detail: str


class ProjectStatus(FrozenModel):
    productName: str
    sourceLabel: str
    metrics: tuple[SummaryMetric, ...]
    evidence: tuple[ResearchCheckpoint, ...]
    baseline: tuple[ResearchCheckpoint, ...]


class CoverageSummary(FrozenModel):
    universe: int
    totalBars: int
    startDate: str
    endDate: str
    currentIdentityVerified: int
    historicalIdentityVerified: int
    unresolvedSecurities: int
    unexplainedTickerSessions: int


class ModelSummary(FrozenModel):
    id: ModelDirection
    direction: Literal["Up", "Down"]
    modelName: str
    role: ModelRole
    validationAveragePrecision: float
    oosAveragePrecision: float
    note: str


class ClassificationMetrics(FrozenModel):
    sampleCount: int
    precision: float
    recall: float
    f1: float
    averagePrecision: float
    brier: float


class ModelProvenance(FrozenModel):
    artifactSha256: str
    featureSet: str
    label: str
    horizonSessions: int
    threshold: float
    maxLeafNodes: int
    learningRate: float
    maxIterations: int
    minSamplesLeaf: int
    l2Regularization: float
    maxBins: int
    classWeight: str
    earlyStopping: bool
    randomSeed: int


class ModelDetail(ModelSummary):
    selectionMeanAveragePrecision: float
    selectionStdAveragePrecision: float
    validation: ClassificationMetrics
    oos: ClassificationMetrics
    provenance: ModelProvenance


class EvidenceLayerSummary(FrozenModel):
    id: EvidenceLayerId
    title: str
    state: ProjectState
    summary: str
    sourceLabel: str


class EvidenceMetric(FrozenModel):
    label: str
    value: str
    detail: str


class EvidenceDetail(EvidenceLayerSummary):
    metrics: tuple[EvidenceMetric, ...]
    findings: tuple[str, ...]
    blocker: str | None
    fingerprint: str | None
    sourceScope: str


class BacktestReadinessStep(FrozenModel):
    label: str
    state: ProjectState
    detail: str


class BacktestRule(FrozenModel):
    label: str
    value: str
    detail: str


class BacktestRuleGroup(FrozenModel):
    title: str
    rules: tuple[BacktestRule, ...]


class BacktestTimelineItem(FrozenModel):
    label: str
    value: str
    detail: str


class BacktestFingerprint(FrozenModel):
    label: str
    value: str


class BaselineBacktestSnapshot(FrozenModel):
    id: Literal["baseline"]
    title: str
    state: Literal["blocked"]
    summary: str
    readiness: tuple[BacktestReadinessStep, ...]
    timeline: tuple[BacktestTimelineItem, ...]
    policyGroups: tuple[BacktestRuleGroup, ...]
    fingerprints: tuple[BacktestFingerprint, ...]
    performanceAvailable: Literal[False]


class StockSummary(FrozenModel):
    ticker: str
    name: str | None
    market: Literal["KOSPI"]
    sector: str | None


class StockDetail(StockSummary):
    dataStatus: Literal["api"]
    availableFrom: str
    latestDataDate: str
    evidenceNote: str
    chartNote: str


class HealthPayload(FrozenModel):
    status: Literal["ok"]
    service: Literal["DonghakStockVision"]
    mode: Literal["read_only"]


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
