"""Authenticated research-LLM controls; no stock-model approval authority."""

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from donghak_stock_vision.storage.ai_model_management import ModelManagement

T = TypeVar("T")


class BoundedManagementRequest(Request):
    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            payload = bytearray()
            async for chunk in self.stream():
                payload.extend(chunk)
                if len(payload) > 4096:
                    raise HTTPException(413, "ai_request_body_limit")
            self._body = bytes(payload)
        return bytes(self._body)


class ManagementRoute(APIRoute):
    def get_route_handler(self) -> Any:
        original = super().get_route_handler()

        async def bounded(request: Request) -> Any:
            return await original(BoundedManagementRequest(request.scope, request.receive))

        return bounded


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]


class ScopedBearerAuthority:
    """Small opt-in adapter where the legacy read-only API has no user auth.

    Deployments can instead inject their existing verified principal resolver.
    Plain secrets are never retained in public configuration or audit history.
    """

    def __init__(self, entries: list[tuple[str, Principal]]) -> None:
        if not entries or any(len(secret) < 24 for secret, _ in entries):
            raise ValueError("ai_strong_bearer_required")
        self.entries = [(hashlib.sha256(secret.encode()).digest(), p) for secret, p in entries]

    def __call__(self, token: str) -> Principal | None:
        value = hashlib.sha256(token.encode()).digest()
        return next(
            (p for expected, p in self.entries if hmac.compare_digest(value, expected)), None
        )


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    modelKey: str = Field(min_length=1, max_length=100)


class SelectionRequest(ValidationRequest):
    validationId: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectedSelectionVersion: int = Field(ge=0)
    approve: StrictBool


class ModelEntry(BaseModel):
    key: str | None
    providerId: str
    modelId: str
    displayName: str
    revision: str
    version: str | None = None
    location: str
    installed: bool | None = None
    configured: bool = False
    available: bool = False
    executable: bool = False
    reachable: bool | None = None
    selected: bool = False
    capabilities: dict[str, Any]
    generationSettings: dict[str, Any] | None = None
    resourceRequirement: dict[str, Any] | None = None
    configurationId: str | None = None
    validationStatus: str
    lastValidationTime: str | None = None
    canChange: bool = False
    changeBlockers: list[str]


class RegistryResponse(BaseModel):
    items: list[ModelEntry]
    role: str
    stockPredictionModelChanged: bool = False


class ValidationResponse(BaseModel):
    validationId: str
    modelKey: str
    valid: bool
    reason: str | None
    expiresAt: str
    unloaded: bool
    researchStarted: bool = False
    modelChanged: bool = False


class ValidationAccepted(BaseModel):
    jobId: str
    state: str
    statusUrl: str
    researchStarted: bool = False
    modelChanged: bool = False


class ActiveResponse(BaseModel):
    defaultModelKey: str
    selectedModelKey: str
    selectedModel: dict[str, Any]
    selectionVersion: int
    selectionId: str
    effectiveFrom: str
    lastApplied: dict[str, Any] | None
    researchRunning: bool
    role: str


def model_router(
    service: ModelManagement, authorize: Callable[[str], Principal | None]
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/ai/models", tags=["Research LLM Management"], route_class=ManagementRoute
    )

    bearer = HTTPBearer(auto_error=False)

    def principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Principal:
        if credentials is None or len(credentials.credentials) > 4096:
            raise HTTPException(401, "ai_authentication_required")
        p = authorize(credentials.credentials)
        if p is None:
            raise HTTPException(401, "ai_invalid_credentials")
        return p

    def reader(p: Annotated[Principal, Depends(principal)]) -> Principal:
        if "ai:read" not in p.scopes:
            raise HTTPException(403, "ai_read_permission_required")
        return p

    def manager(p: Annotated[Principal, Depends(reader)]) -> Principal:
        if "ai:manage" not in p.scopes:
            raise HTTPException(403, "ai_manage_permission_required")
        return p

    def invoke(fn: Callable[[], T]) -> T:
        try:
            return fn()
        except ValueError as error:
            code = str(error)
            status = (
                409
                if code in ("autonomous_runner_already_active", "ai_selection_conflict")
                else 422
            )
            raise HTTPException(
                status,
                code if code.startswith(("ai_", "llm_", "autonomous_")) else "ai_request_rejected",
            ) from None

    @router.get("", response_model=RegistryResponse)
    def models(p: Annotated[Principal, Depends(reader)]) -> dict[str, Any]:
        items = invoke(service.models)
        if "ai:manage" not in p.scopes:
            items = [
                r
                | dict(
                    canChange=False,
                    changeBlockers=r["changeBlockers"] + ["ai_manage_permission_required"],
                )
                for r in items
            ]
        return dict(items=items, role="research_hypothesis_llm", stockPredictionModelChanged=False)

    @router.get("/active", response_model=ActiveResponse)
    def active(p: Annotated[Principal, Depends(reader)]) -> dict[str, Any]:
        return invoke(service.active)

    @router.post(
        "/validate",
        responses={200: {"model": ValidationResponse}, 202: {"model": ValidationAccepted}},
    )
    def validate(
        body: ValidationRequest,
        tasks: BackgroundTasks,
        p: Annotated[Principal, Depends(manager)],
        wait: bool = False,
    ) -> Any:
        if wait:
            return invoke(lambda: service.validate(body.modelKey, p.subject))
        identifier = invoke(lambda: service.submit_validation(body.modelKey, p.subject))
        tasks.add_task(service.execute_validation_job, identifier)
        return JSONResponse(
            status_code=202,
            content=dict(
                jobId=identifier,
                state="queued",
                statusUrl="/api/v1/ai/models/validations/" + identifier,
                researchStarted=False,
                modelChanged=False,
            ),
        )

    @router.get("/validations/{identifier}")
    def validation_job(identifier: str, p: Annotated[Principal, Depends(reader)]) -> dict[str, Any]:
        if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
            raise HTTPException(422, "ai_validation_job_id")
        return invoke(lambda: service.validation_job(identifier))

    @router.put("/active", response_model=ActiveResponse)
    def select(body: SelectionRequest, p: Annotated[Principal, Depends(manager)]) -> dict[str, Any]:
        return invoke(
            lambda: service.select(
                body.modelKey,
                body.validationId,
                body.expectedSelectionVersion,
                p.subject,
                body.approve,
            )
        )

    @router.get("/comparisons")
    def comparisons(p: Annotated[Principal, Depends(reader)]) -> dict[str, Any]:
        return dict(
            items=invoke(service.comparisons),
            comparisonRule="compare_only_identical_comparisonContractId",
            independentPerformanceEstablished=False,
        )

    @router.get("/history")
    def history(p: Annotated[Principal, Depends(reader)]) -> dict[str, Any]:
        return dict(items=invoke(service.history))

    return router
