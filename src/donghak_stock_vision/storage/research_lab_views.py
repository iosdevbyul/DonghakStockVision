"""Sanitized, typed R1–R6 sidecar for an approved Research Lab index."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Code = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]
Ticker = Annotated[str, Field(pattern=r"^[0-9]{6}$")]
Stage = Literal["R1", "R2", "R3", "R4", "R5", "R6"]
Horizon = Literal[1, 5, 20]
Exposure = Literal["historical_readout", "development", "exposed_fixed", "prospective"]
RowState = Literal["evaluated", "neutral", "excluded", "missing", "pending", "unavailable"]
Count = Annotated[int, Field(ge=0)]
Unit = Annotated[float, Field(ge=0, le=1)]


class View(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Metrics(View):
    n: Count | None = None
    accuracy: Unit | None = None
    balanced_accuracy: Unit | None = None
    pr_auc_ap: Unit | None = None
    brier_score: Unit | None = None
    precision: Unit | None = None
    recall: Unit | None = None
    down_precision: Unit | None = None
    down_recall: Unit | None = None
    fpr: Unit | None = None
    fdr: Unit | None = None
    confusion_matrix: dict[Literal["tn", "fp", "fn", "tp"], Count] | None = None
    correct: Count | None = None
    incorrect: Count | None = None
    positive: Count | None = None
    negative: Count | None = None


class Resources(View):
    wallSeconds: float | None = None
    cpuSeconds: float | None = None
    peakRssKiB: Count | None = None
    checkpointBytes: Count | None = None
    storageBytes: Count | None = None
    remainingSeconds: float | None = None


class StrategyArm(View):
    horizon: Horizon
    arm: Code
    selections: Count
    actualFits: Count
    cacheUses: Count
    selectedExperimentId: Id | None = None
    reason: Code


class Run(View):
    runId: Id
    stage: Stage
    role: Code
    status: Code
    evidenceStatus: Code
    startedAt: AwareDatetime | None = None
    endedAt: AwareDatetime | None = None
    horizons: list[Horizon]
    trainingStart: date | None = None
    trainingEnd: date | None = None
    developmentStart: date | None = None
    developmentEnd: date | None = None
    trainingTickers: Count | None = None
    evaluationTickers: Count | None = None
    fits: Count
    fitsRecovered: Count = 0
    evaluations: Count | None = None
    exactCache: Count | None = None
    calibrationFits: Count = 0
    calibrationFitsRecovered: Count = 0
    failures: Count | None = None
    retainedLegacy: Count | None = None
    qualifyingImprovements: Count | None = None
    stopReason: Code | None = None
    resources: Resources | None = None
    strategyArms: list[StrategyArm] = Field(default_factory=list)
    gemmaStatus: Code | None = None
    gemmaReason: Code | None = None


class Experiment(View):
    runId: Id
    stage: Stage
    experimentId: Id
    modelId: Id | None = None
    parentExperimentId: Id | None = None
    horizon: Horizon
    candidateId: Id | None = None
    candidate: Code | None = None
    family: Code | None = None
    mask: Code | None = None
    parameters: dict[Code, bool | int | float | Code | None] = Field(default_factory=dict)
    candidateValue: int | float | None = None
    trainingPopulationId: Id | None = None
    trainingRows: Count | None = None
    trainingTickers: Count | None = None
    fitSeconds: float | None = None
    trainingPrior: Unit | None = None
    status: Code
    referenceOnly: bool = False
    order: Count | None = None
    metrics: Metrics | None = None
    evidenceStatus: Code | None = None


class Evaluation(View):
    evaluationId: Id
    runId: Id
    stage: Stage
    experimentId: Id
    modelId: Id
    horizon: Horizon
    exposure: Exposure
    populationId: Id
    role: Code
    contract: Code
    order: Count | None = None
    transformId: Id | None = None
    sourceEvaluationId: Id | None = None
    blockAnchors: dict[Code, Count] = Field(default_factory=dict)
    blockBaDelta: dict[Code, float | None] = Field(default_factory=dict)
    metrics: Metrics
    baselines: dict[Code, Metrics] = Field(default_factory=dict)
    candidateRows: Count | None = None
    evaluatedRows: Count
    neutralRows: Count | None = None
    excludedRows: Count | None = None
    missingRows: Count | None = None
    pendingRows: Count | None = None
    tickers: Count
    tradingDates: Count
    anchors: Count | None = None
    evidenceStatus: Code
    selection: Code | None = None
    independentEligible: Literal[False] = False


class Prediction(View):
    evaluationId: Id
    predictionId: Id
    ticker: Ticker
    inputDate: date
    baseDate: date | None = None
    targetDate: date | None = None
    baseClose: int | None = None
    targetClose: int | None = None
    actualReturn: float | None = None
    probability: Unit | None = None
    predictedClass: Literal[0, 1] | None = None
    actualClass: Literal[0, 1] | None = None
    correct: bool | None = None
    state: RowState
    reason: Code | None = None
    generatedAt: None = None

    @model_validator(mode="after")
    def verify_row(self) -> "Prediction":
        if self.state == "evaluated":
            if (
                self.probability is None
                or self.actualClass is None
                or self.predictedClass != int(self.probability >= 0.5)
                or self.correct is not (self.predictedClass == self.actualClass)
                or self.baseDate is None
                or self.targetDate is None
                or not self.inputDate < self.baseDate < self.targetDate
                or self.baseClose is None
                or self.targetClose is None
                or min(self.baseClose, self.targetClose) <= 0
            ):
                raise ValueError("prediction_evidence_binding")
        elif any(v is not None for v in (self.probability, self.actualClass, self.correct)):
            raise ValueError("prediction_exclusion_binding")
        return self


class PredictionItem(Prediction):
    runId: Id
    experimentId: Id
    modelId: Id
    stage: Stage
    horizon: Horizon
    populationId: Id
    exposure: Exposure
    independentEligible: Literal[False] = False


class Comparison(View):
    evaluationId: Id
    baseline: Code
    baselineExperimentId: Id | None = None
    populationId: Id
    comparable: bool
    status: Code
    baselineMetrics: Metrics | None = None
    delta: dict[Code, float | None]
    transitions: dict[Code, Count] | None = None


class Decision(View):
    decisionId: Id
    runId: Id
    stage: Stage
    horizon: Horizon
    order: Count
    arm: Code | None = None
    candidateId: Id
    parentExperimentId: Id | None = None
    experimentId: Id | None = None
    feedbackId: Id | None = None
    sourceExperimentIds: list[Id] = Field(default_factory=list)
    hypothesis: Code | None = None
    withoutLatestCandidateId: Id | None = None
    changedFromLatest: bool | None = None
    result: Code | None = None
    reason: Code | None = None


class Feedback(View):
    feedbackId: Id
    originExperimentId: Id
    originModelId: Id
    cohort: Code
    failureRows: Count
    tickers: list[Ticker]
    start: date | None = None
    end: date | None = None
    metrics: Metrics
    followupExperimentIds: list[Id]
    hypotheses: dict[Code, Code]


class ProspectiveModel(View):
    experimentId: Id
    modelId: Id
    horizon: Horizon
    descriptiveOnly: bool
    recorded: Count
    pending: Count
    evaluated: Count
    qualifiedAnchors: Count
    missingAnchors: Count
    baseDate: date | None = None
    targetDate: date | None = None
    cutoff: AwareDatetime | None = None
    blockers: list[Code]
    performanceApproved: Literal[False] = False


class Prospective(View):
    checkedAt: AwareDatetime
    inputDate: date
    inputCurrentAtSnapshot: bool
    status: Code
    observations: Count
    requiredObservations: Count
    legacyPending: Count
    legacyPriceWindowsComplete: Count
    historicalReplay: Count
    historicalReplayEvaluated: Count
    models: list[ProspectiveModel]


class Operations(View):
    checkedAt: AwareDatetime
    lastCaptureTradingDate: date
    latestTradingDate: date
    requestedAt: AwareDatetime
    httpStatus: Count
    capturedRows: Count
    totalRows: Count
    sessions: Count
    distinctTickers: Count
    duplicateRows: Count
    calendarEnd: date
    missingTradingDates: list[date]
    scheduledTimeKst: str = Field(pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    agentState: Literal["running", "not_running", "unknown"]
    enabled: bool
    policyValid: bool
    blockers: list[Code]
    localUsed: Count | None
    localRemaining: Count | None
    providerRemaining: None = None


class Visibility(View):
    version: Literal["research_lab_visibility_v1"]
    publishedAt: AwareDatetime
    runs: list[Run] = Field(max_length=80)
    experiments: list[Experiment] = Field(max_length=500)
    evaluations: list[Evaluation] = Field(max_length=500)
    predictions: list[Prediction] = Field(max_length=100000)
    comparisons: list[Comparison] = Field(max_length=2000)
    decisions: list[Decision] = Field(max_length=500)
    feedback: list[Feedback] = Field(max_length=50)
    prospective: Prospective
    operations: Operations

    @model_validator(mode="after")
    def bindings(self) -> "Visibility":
        runs = {row.runId: row for row in self.runs}
        evaluations = {row.evaluationId: row for row in self.evaluations}
        if len(runs) != len(self.runs) or len(evaluations) != len(self.evaluations):
            raise ValueError("duplicate_research_evidence")
        linked_rows: list[Experiment | Evaluation | Decision] = [
            *self.experiments,
            *self.evaluations,
            *self.decisions,
        ]
        if any(row.runId not in runs or row.stage != runs[row.runId].stage for row in linked_rows):
            raise ValueError("research_run_binding")
        models = {(row.runId, row.experimentId): row.modelId for row in self.experiments}
        if len(models) != len(self.experiments) or any(
            models.get((row.runId, row.experimentId)) != row.modelId for row in self.evaluations
        ):
            raise ValueError("research_experiment_binding")
        counts: dict[str, int] = {}
        correct: dict[str, int] = {}
        keys = set()
        for row in self.predictions:
            evaluation = evaluations.get(row.evaluationId)
            if evaluation is None or evaluation.exposure == "prospective":
                raise ValueError("research_prediction_binding")
            key = (row.evaluationId, row.predictionId)
            if key in keys:
                raise ValueError("duplicate_research_prediction")
            keys.add(key)
            if row.state == "evaluated":
                counts[row.evaluationId] = counts.get(row.evaluationId, 0) + 1
                correct[row.evaluationId] = correct.get(row.evaluationId, 0) + int(
                    row.correct is True
                )
            for day in (row.inputDate, row.baseDate, row.targetDate):
                if day is not None and (
                    date(2024, 7, 1) <= day <= date(2025, 6, 30)
                    or date(2026, 7, 1) <= day <= date(2026, 9, 30)
                ):
                    raise ValueError("protected_research_date")
        if any(
            counts.get(row.evaluationId, 0) != row.evaluatedRows
            or row.metrics.n != row.evaluatedRows
            or (
                row.metrics.correct is not None
                and row.metrics.correct != correct.get(row.evaluationId, 0)
            )
            for row in self.evaluations
        ):
            raise ValueError("research_prediction_denominator")
        if any(
            row.evaluationId not in evaluations
            or row.populationId != evaluations[row.evaluationId].populationId
            or (
                row.transitions is not None
                and sum(row.transitions.values()) != evaluations[row.evaluationId].evaluatedRows
            )
            for row in self.comparisons
        ):
            raise ValueError("research_comparison_binding")
        return self
