"""Authenticated, paginated Research Lab views over an immutable publication."""

from collections.abc import Callable
from enum import IntEnum
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from donghak_stock_vision.ai_model_routes import Principal
from donghak_stock_vision.direction_shadow_routes import research_read_guard
from donghak_stock_vision.storage.research_lab_views import (
    Code,
    Comparison,
    Decision,
    Evaluation,
    Experiment,
    Exposure,
    Feedback,
    Id,
    Operations,
    PredictionItem,
    Prospective,
    RowState,
    Run,
    Stage,
    Ticker,
    Visibility,
)


class HorizonFilter(IntEnum):
    H1 = 1
    H5 = 5
    H20 = 20


class Scope(BaseModel):
    stage: Stage | None = None
    runId: Id | None = None
    experimentId: Id | None = None
    modelId: Id | None = None
    horizon: HorizonFilter | None = None


class PageParams(BaseModel):
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=25, ge=1, le=100)
    sort: Literal["asc", "desc"] = "asc"


class Overview(BaseModel):
    schemaVersion: Literal["research_lab_overview_v1"] = "research_lab_overview_v1"
    publishedAt: str
    lastEndedAt: str | None
    snapshotOnly: Literal[True] = True
    executionStatus: Literal["IDLE", "RUNNING"]
    registeredRuns: int
    completedRuns: int
    stages: dict[Stage, str]
    fits: int
    exactCache: int
    calibrationFits: int
    evaluatedModelRows: int
    historicalReplay: int
    prospectivePredictions: int
    prospectiveEvaluations: int
    qualifiedAnchors: int
    qualifyingImprovements: int
    retainedLegacy: int
    failures: int
    stops: int
    latestTradingDate: str
    performanceApproved: Literal[False] = False
    independentPerformanceEstablished: Literal[False] = False
    countingScope: Literal["r1_r6_retained_runs_model_rows_may_overlap"] = (
        "r1_r6_retained_runs_model_rows_may_overlap"
    )


class Page[T](BaseModel):
    schemaVersion: Literal["research_lab_visibility_read_v1"] = "research_lab_visibility_read_v1"
    publishedAt: str
    total: int
    offset: int
    limit: int
    nextOffset: int | None
    items: list[T]


def _matches(row: Any, scope: Scope) -> bool:
    return all(
        getattr(row, key, None) == value
        for key, value in scope.model_dump(exclude_none=True).items()
    )


def router(
    data: Visibility,
    authorize: Callable[[str], Principal | None],
) -> APIRouter:
    guard = research_read_guard(authorize)
    routes = APIRouter(
        prefix="/api/v1/research/lab",
        tags=["Research Lab evidence"],
        dependencies=[Depends(guard)],
        responses={
            401: {"description": "Authentication required"},
            403: {"description": "Read scope and loopback required"},
            404: {"description": "Original item unavailable in this publication"},
            413: {"description": "Request too large"},
        },
    )
    runs = sorted(
        data.runs, key=lambda x: (x.stage, x.startedAt.isoformat() if x.startedAt else "", x.runId)
    )
    evaluations = {e.evaluationId: e for e in data.evaluations}
    predictions = sorted(
        data.predictions, key=lambda r: (r.inputDate, r.ticker, r.evaluationId, r.predictionId)
    )
    published = data.publishedAt.isoformat()
    orders = {r.runId: i for i, r in enumerate(runs)}
    newest_end = max((r.endedAt for r in runs if r.endedAt), default=None)

    def order(row: Any) -> tuple[int, int, str]:
        return (
            orders[row.runId],
            row.order if row.order is not None else 10000,
            getattr(
                row, "evaluationId", getattr(row, "decisionId", getattr(row, "experimentId", ""))
            ),
        )

    def page[T](rows: list[T], query: PageParams) -> Page[T]:
        total = len(rows)
        if query.sort == "desc":
            rows = rows[::-1]
        end = query.offset + query.limit
        return Page(
            publishedAt=published,
            total=total,
            offset=query.offset,
            limit=query.limit,
            nextOffset=end if end < total else None,
            items=rows[query.offset : end],
        )

    @routes.get("/overview")
    def overview() -> Overview:
        return Overview(
            publishedAt=published,
            lastEndedAt=newest_end.isoformat() if newest_end else None,
            executionStatus="RUNNING" if any(r.status == "RUNNING" for r in runs) else "IDLE",
            registeredRuns=len(runs),
            completedRuns=sum(r.status == "COMPLETED" for r in runs),
            stages={r.stage: "PARTIAL" for r in runs} | {"R5": data.prospective.status},
            fits=sum(r.fits for r in runs),
            exactCache=sum(r.exactCache or 0 for r in runs),
            calibrationFits=sum(r.calibrationFits for r in runs),
            evaluatedModelRows=sum(r.evaluatedRows for r in data.evaluations),
            historicalReplay=data.prospective.historicalReplay,
            prospectivePredictions=sum(m.recorded for m in data.prospective.models),
            prospectiveEvaluations=sum(m.evaluated for m in data.prospective.models),
            qualifiedAnchors=sum(m.qualifiedAnchors for m in data.prospective.models),
            qualifyingImprovements=sum(r.qualifyingImprovements or 0 for r in runs),
            retainedLegacy=sum(r.retainedLegacy or 0 for r in runs),
            failures=sum(r.failures or 0 for r in runs),
            stops=sum(r.stopReason in {"checkpoint_pause", "operator_stop"} for r in runs),
            latestTradingDate=data.operations.latestTradingDate.isoformat(),
        )

    @routes.get("/runs")
    def research_runs(
        query: Annotated[PageParams, Depends()], stage: Stage | None = None
    ) -> Page[Run]:
        return page([r for r in runs if stage is None or r.stage == stage], query)

    @routes.get("/runs/{run_id}")
    def research_run(run_id: Id) -> Run:
        for r in runs:
            if r.runId == run_id:
                return r
        raise HTTPException(404, detail={"code": "research_lab_item_not_found"})

    @routes.get("/experiments")
    def research_experiments(
        query: Annotated[PageParams, Depends()],
        stage: Stage | None = None,
        runId: Id | None = None,
        experimentId: Id | None = None,
        modelId: Id | None = None,
        horizon: HorizonFilter | None = None,
    ) -> Page[Experiment]:
        scope = Scope(
            stage=stage, runId=runId, experimentId=experimentId, modelId=modelId, horizon=horizon
        )
        return page(sorted((x for x in data.experiments if _matches(x, scope)), key=order), query)

    @routes.get("/evaluations")
    def research_evaluations(
        query: Annotated[PageParams, Depends()],
        stage: Stage | None = None,
        runId: Id | None = None,
        experimentId: Id | None = None,
        modelId: Id | None = None,
        horizon: HorizonFilter | None = None,
        exposure: Exposure | None = None,
        evidenceStatus: Code | None = None,
        populationId: Id | None = None,
    ) -> Page[Evaluation]:
        scope = Scope(
            stage=stage, runId=runId, experimentId=experimentId, modelId=modelId, horizon=horizon
        )
        return page(
            sorted(
                (
                    x
                    for x in data.evaluations
                    if _matches(x, scope)
                    and (exposure is None or x.exposure == exposure)
                    and (evidenceStatus is None or x.evidenceStatus == evidenceStatus)
                    and (populationId is None or x.populationId == populationId)
                ),
                key=order,
            ),
            query,
        )

    @routes.get("/prediction-rows")
    def research_predictions(
        query: Annotated[PageParams, Depends()],
        stage: Stage | None = None,
        runId: Id | None = None,
        experimentId: Id | None = None,
        modelId: Id | None = None,
        horizon: HorizonFilter | None = None,
        exposure: Exposure | None = None,
        ticker: Ticker | None = None,
        correct: bool | None = None,
        state: RowState | None = None,
        evaluationId: Id | None = None,
    ) -> Page[PredictionItem]:
        scope = Scope(
            stage=stage, runId=runId, experimentId=experimentId, modelId=modelId, horizon=horizon
        )
        available = {
            e.evaluationId
            for e in data.evaluations
            if _matches(e, scope)
            and (exposure is None or exposure == e.exposure)
            and (evaluationId is None or evaluationId == e.evaluationId)
        }
        rows = [
            r
            for r in predictions
            if r.evaluationId in available
            and (ticker is None or ticker == r.ticker)
            and (state is None or state == r.state)
            and (correct is None or correct is r.correct)
        ]
        selected = page(rows, query)
        output = []
        for row in selected.items:
            e = evaluations[row.evaluationId]
            output.append(
                PredictionItem(
                    **row.model_dump(),
                    runId=e.runId,
                    stage=e.stage,
                    horizon=e.horizon,
                    experimentId=e.experimentId,
                    modelId=e.modelId,
                    populationId=e.populationId,
                    exposure=e.exposure,
                )
            )
        return Page[PredictionItem](
            publishedAt=published,
            total=selected.total,
            offset=selected.offset,
            limit=selected.limit,
            nextOffset=selected.nextOffset,
            items=output,
        )

    @routes.get("/evaluation-comparisons")
    def research_comparisons(
        query: Annotated[PageParams, Depends()],
        stage: Stage | None = None,
        runId: Id | None = None,
        evaluationId: Id | None = None,
        horizon: HorizonFilter | None = None,
        populationId: Id | None = None,
    ) -> Page[Comparison]:
        scope = Scope(stage=stage, runId=runId, horizon=horizon)
        return page(
            sorted(
                (
                    c
                    for c in data.comparisons
                    if _matches(evaluations[c.evaluationId], scope)
                    and (evaluationId is None or c.evaluationId == evaluationId)
                    and (populationId is None or c.populationId == populationId)
                ),
                key=lambda c: (*order(evaluations[c.evaluationId]), c.baseline),
            ),
            query,
        )

    @routes.get("/decisions")
    def research_decisions(
        query: Annotated[PageParams, Depends()],
        stage: Stage | None = None,
        runId: Id | None = None,
        horizon: HorizonFilter | None = None,
        feedbackId: Id | None = None,
    ) -> Page[Decision]:
        scope = Scope(stage=stage, runId=runId, horizon=horizon)
        return page(
            sorted(
                (
                    row
                    for row in data.decisions
                    if _matches(row, scope) and (feedbackId is None or row.feedbackId == feedbackId)
                ),
                key=order,
            ),
            query,
        )

    @routes.get("/failure-feedback")
    def research_feedback(
        query: Annotated[PageParams, Depends()], feedbackId: Id | None = None
    ) -> Page[Feedback]:
        return page(
            sorted(
                (
                    row
                    for row in data.feedback
                    if feedbackId is None or row.feedbackId == feedbackId
                ),
                key=lambda row: (row.feedbackId, row.originExperimentId),
            ),
            query,
        )

    @routes.get("/prospective")
    def research_prospective() -> Prospective:
        return data.prospective

    @routes.get("/krx-status")
    def krx_status() -> Operations:
        return data.operations

    return routes
