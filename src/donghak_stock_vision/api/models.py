"""Typed read-only API payloads. These models contain no research logic."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from donghak_stock_vision.storage.daily_research_public_models import (
    DailyResearchReaderError as DailyResearchReaderError,
    PublicModel as PublicModel,
    ResearchPeriod as ResearchPeriod,
    ResearchSourceReference as ResearchSourceReference,
    DailyResearchRecord as DailyResearchRecord,
    DailyResearchSeries as DailyResearchSeries,
)

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
