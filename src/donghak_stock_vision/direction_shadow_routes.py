"""Approved sanitized publication; authenticated clients never open a journal."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.ai_model_routes import Principal

Id = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Mode = Literal["research_forward_candidate", "historical_replay"]
Quality = Literal["local_provider_capture", "offline_fixture"]
Horizon = Literal[1, 5, 20]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Matrix(Strict):
    tn: int
    fp: int
    fn: int
    tp: int


class Metrics(Strict):
    n: int
    positive: int
    negative: int
    accuracy: float | None
    balanced_accuracy: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    roc_auc: float | None
    pr_auc_ap: float | None
    brier_score: float | None
    confusion_matrix: Matrix


class RetainedEvidence(Strict):
    baseTradingDate: str
    outcomeTradingDate: str
    baseClose: Annotated[int, Field(gt=0)] | None
    targetClose: Annotated[int, Field(gt=0)] | None
    actualReturn: float | None
    correct: bool | None
    momentumScore: Annotated[float, Field(ge=0, le=1)]
    outcomeReason: (
        Literal[
            "incomplete_sources_or_time_guard", "neutral_cost_band", "verified_original_outcome"
        ]
        | None
    )
    independentlyQualified: Literal[False]


class Prediction(Strict):
    predictionId: Id
    inputCohort: Annotated[int, Field(ge=0, le=1000)]
    modelId: Id
    modelSha256: Id
    predictionType: Literal["future_close_direction", "conditional_reversal_event"]
    horizon: Horizon
    ticker: Annotated[str, Field(pattern=r"^\d{6}$")]
    inputTradingDate: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    targetTradingDate: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    outcomeSessions: list[str]
    createdAt: str
    featureVersion: Literal["causal_daily_closed_observed_periods_v2", "ohlcv_value_v1"]
    labelContract: Literal[
        "future_delayed_direction_v3", "reversal_barrier_v1", "delayed_reversal_barrier_v2"
    ]
    evaluationPolicyId: Id
    score: Annotated[float, Field(ge=0, le=1)]
    baselineScore: Annotated[float, Field(ge=0, le=1)]
    calibratedProbability: None
    conditioningDirection: Literal["up", "down"] | None
    predictedClass: Literal[0, 1]
    threshold: Annotated[float, Field(ge=0.5, le=0.5)]
    state: Literal["pending", "evaluated", "unavailable"]
    actualLabel: Literal[0, 1] | None
    mode: Mode
    sourceQuality: Quality
    r5Evidence: RetainedEvidence | None = None

    @model_validator(mode="after")
    def binding(self) -> "Prediction":
        from datetime import date, datetime

        if (
            len(self.outcomeSessions) != self.horizon
            or self.outcomeSessions != sorted(set(self.outcomeSessions))
            or self.targetTradingDate != self.outcomeSessions[0]
            or (self.state == "evaluated") != (self.actualLabel is not None)
            or self.predictedClass != int(self.score >= self.threshold)
        ):
            raise ValueError("direction_public_prediction_binding")
        if self.predictionType == "conditional_reversal_event":
            if (
                self.horizon != 5
                or self.conditioningDirection not in ("up", "down")
                or self.featureVersion != "ohlcv_value_v1"
                or self.labelContract == "future_delayed_direction_v3"
            ):
                raise ValueError("legacy_contract_projection")
        elif (
            self.conditioningDirection is not None
            or self.labelContract != "future_delayed_direction_v3"
            or self.featureVersion != "causal_daily_closed_observed_periods_v2"
        ):
            raise ValueError("direction_contract_projection")
        if "r5Evidence" in self.model_fields_set:
            self._bind_retained()
        date.fromisoformat(self.inputTradingDate)
        for d in self.outcomeSessions:
            date.fromisoformat(d)
        if datetime.fromisoformat(self.createdAt).utcoffset() is None:
            raise ValueError("direction_public_clock")
        return self

    def _bind_retained(self) -> None:
        from datetime import date

        from donghak_stock_vision.models.research_v2 import direction_label

        extra = self.r5Evidence
        if (
            extra is None
            or self.predictionType != "future_close_direction"
            or not self.inputTradingDate < extra.baseTradingDate < self.outcomeSessions[0]
            or extra.outcomeTradingDate != self.outcomeSessions[-1]
        ):
            raise ValueError("direction_r5_prediction_binding")
        date.fromisoformat(extra.baseTradingDate)
        if self.state == "pending":
            if any(
                v is not None
                for v in (extra.baseClose, extra.targetClose, extra.actualReturn, extra.correct)
            ) or extra.outcomeReason not in (None, "incomplete_sources_or_time_guard"):
                raise ValueError("direction_r5_pending_binding")
            return
        base, target = extra.baseClose, extra.targetClose
        if base is None or target is None or extra.actualReturn != (target - base) / base:
            raise ValueError("direction_r5_price_binding")
        label = direction_label(base, target)
        if (
            self.actualLabel != label
            or extra.correct != (self.predictedClass == label if label is not None else None)
            or extra.outcomeReason
            != ("neutral_cost_band" if label is None else "verified_original_outcome")
        ):
            raise ValueError("direction_r5_result_binding")


class Relative(Strict):
    balanced_accuracy: float | None
    pr_auc_ap: float | None
    brier_score: float | None


class RetainedBaselines(Strict):
    training_majority: Metrics
    momentum: Metrics


class Model(Strict):
    featureVersion: Literal["causal_daily_closed_observed_periods_v2", "ohlcv_value_v1"]
    modelId: Id
    modelSha256: Id
    predictionType: Literal["future_close_direction", "conditional_reversal_event"]
    horizon: Horizon
    mode: Mode
    sourceQuality: Quality
    labelContract: Literal[
        "future_delayed_direction_v3", "reversal_barrier_v1", "delayed_reversal_barrier_v2"
    ]
    evaluationPolicyId: Id
    total: int
    pending: int
    evaluated: int
    duplicateEvaluationRows: int
    excluded: int
    metrics: Metrics
    baselineMetrics: Metrics
    baselineRelative: Relative
    independentAnchors: int
    missingAnchors: int
    missingPositive: int
    missingNegative: int
    qualifiedAnchors: Literal[0]
    blockers: list[
        Literal[
            "insufficient_calendar_anchors",
            "insufficient_positive_class",
            "insufficient_negative_class",
            "no_verified_prospective_outcomes",
            "historical_replay_excluded",
            "offline_fixture_excluded",
            "independent_chronology_unverified",
            "pit_unqualified",
        ]
    ]
    latestEvaluationDate: str | None
    evidenceStatus: Literal["INSUFFICIENT_EVIDENCE", "READY_FOR_EXISTING_EVALUATOR"]
    performanceApproved: Literal[False]
    pitQualified: Literal[False]
    independentPredictivePower: Literal[False]
    r5Baselines: RetainedBaselines | None = None


class Comparison(Strict):
    firstModel: Id
    secondModel: Id
    predictionType: Literal["future_close_direction", "conditional_reversal_event"]
    horizon: Horizon
    comparable: bool
    reason: Literal[
        "same_verified_population_diagnostic_only", "contract_source_or_population_mismatch"
    ]
    performanceApproved: Literal[False]


class Publication(Strict):
    version: Literal["prospective_direction_publication_v1"]
    models: list[Model]
    predictions: list[Prediction]
    comparisons: list[Comparison]
    pitQualified: Literal[False]
    performanceApproved: Literal[False]
    executionAuthorized: Literal[False]


class DirectionPublication:
    def __init__(self, path: Path, expected_sha256: str) -> None:
        self.path = path
        self.expected = expected_sha256
        self.read()

    def read(self) -> Publication:
        try:
            if (
                not self.path.is_absolute()
                or self.path.resolve() != self.path
                or self.path.is_symlink()
                or self.path.stat().st_nlink != 1
                or self.path.stat().st_mode & 0o077
                or not 0 < self.path.stat().st_size <= 10 * 1024**2
            ):
                raise ValueError("direction_publication_bound")
            body = self.path.read_bytes()
            if hashlib.sha256(body).hexdigest() != self.expected:
                raise ValueError("direction_publication_pin")
            value = Publication.model_validate_json(body)
            if (
                len(value.predictions) > 1000
                or len(value.models) > 60
                or len({p.predictionId for p in value.predictions}) != len(value.predictions)
            ):
                raise ValueError("direction_publication_count")
            from donghak_stock_vision.models.direction_evaluation import summarize

            keys = (
                "modelId",
                "modelSha256",
                "horizon",
                "mode",
                "sourceQuality",
                "labelContract",
                "evaluationPolicyId",
                "predictionType",
                "featureVersion",
            )
            rebuilt = summarize(
                [p.model_dump(exclude_unset=True) for p in value.predictions],
                [{k: m.model_dump()[k] for k in keys} for m in value.models],
            )
            if rebuilt != value.model_dump(exclude_unset=True):
                raise ValueError("direction_publication_reconstruction")
            return value
        except (ValueError, OSError, TypeError) as error:
            raise ValueError("direction_publication_unavailable") from error


def research_read_guard(authorize: Callable[[str], Principal | None]) -> Callable[..., None]:
    bearer = HTTPBearer(auto_error=False)

    def guard(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        principal = authorize(credentials.credentials) if credentials else None
        if principal is None:
            raise HTTPException(401, detail={"code": "research_authentication_required"})
        if "research:read" not in principal.scopes:
            raise HTTPException(403, detail={"code": "research_read_scope_required"})
        try:
            origin = urlsplit(request.headers.get("origin", "")).hostname
        except ValueError:
            origin = "INVALID"
        if (
            request.client is None
            or request.client.host not in ("127.0.0.1", "::1")
            or request.url.hostname not in ("localhost", "127.0.0.1", "::1")
            or (
                request.headers.get("origin") is not None
                and origin not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise HTTPException(403, detail={"code": "local_research_access_required"})
        if len(request.scope.get("path", "")) + len(request.scope.get("query_string", b"")) > 4096:
            raise HTTPException(413, detail={"code": "oversized_request"})

    return guard


def router(reader: DirectionPublication, authorize: Callable[[str], Principal | None]) -> APIRouter:
    routes = APIRouter(prefix="/api/v1/research/predictions")
    guard = research_read_guard(authorize)

    def read() -> Publication:
        try:
            return reader.read()
        except ValueError:
            raise HTTPException(503, detail={"code": "direction_evidence_unavailable"}) from None

    def page(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
        return dict(
            schemaVersion="prospective_direction_read_v1",
            items=items[offset : offset + limit],
            total=len(items),
            offset=offset,
            limit=limit,
            nextOffset=offset + limit if offset + limit < len(items) else None,
        )

    @routes.get("", dependencies=[Depends(guard)])
    def predictions(
        modelId: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
        horizon: Annotated[int | None, Query(ge=1, le=20)] = None,
        ticker: Annotated[str | None, Query(pattern=r"^\d{6}$")] = None,
        state: Literal["pending", "evaluated", "unavailable"] | None = None,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        if horizon is not None and horizon not in (1, 5, 20):
            raise HTTPException(422, detail={"code": "direction_horizon_unsupported"})
        rows = [
            p.model_dump(exclude_unset=True)
            for p in read().predictions
            if (modelId is None or p.modelId == modelId)
            and (horizon is None or p.horizon == horizon)
            and (ticker is None or p.ticker == ticker)
            and (state is None or p.state == state)
        ]
        return page(rows, offset, limit)

    @routes.get("/models", dependencies=[Depends(guard)])
    def models(
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        return page([m.model_dump(exclude_unset=True) for m in read().models], offset, limit)

    @routes.get("/comparisons", dependencies=[Depends(guard)])
    def comparisons(
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        return page([m.model_dump() for m in read().comparisons], offset, limit)

    return routes
