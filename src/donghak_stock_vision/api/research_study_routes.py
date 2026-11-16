"""Local-only HTTP surface for operator-pinned autonomous research evidence."""

import re
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from donghak_stock_vision.api.research_studies import PinnedStudyReader, StudyReadError


def research_study_router(reader: PinnedStudyReader | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/research/studies", tags=["research studies"])

    def local_reader(request: Request) -> PinnedStudyReader:
        if reader is None:
            raise HTTPException(503, detail={"code": "study_snapshot_not_configured"})
        origin = request.headers.get("origin")
        try:
            origin_host = urlsplit(origin).hostname if origin is not None else None
        except ValueError:
            raise HTTPException(403, detail={"code": "local_research_access_required"}) from None
        if (
            request.client is None
            or request.client.host not in {"127.0.0.1", "::1"}
            or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or (origin is not None and origin_host not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise HTTPException(403, detail={"code": "local_research_access_required"})
        if len(request.scope.get("path", "")) + len(request.scope.get("query_string", b"")) > 4096:
            raise HTTPException(413, detail={"code": "oversized_request"})
        return reader

    Source = Annotated[PinnedStudyReader, Depends(local_reader)]

    def lookup(callback: object, *args: object) -> dict[str, object]:
        try:
            result = callback(*args)  # type: ignore[operator]
            if result is None:
                raise HTTPException(404, detail={"code": "experiment_not_found"})
            return result  # type: ignore[no-any-return]
        except StudyReadError as exc:
            code = str(exc)
            if code in {"study_not_found", "invalid_study_id"}:
                raise HTTPException(404, detail={"code": "study_not_found"}) from None
            if code == "invalid_experiment_id":
                raise HTTPException(404, detail={"code": code}) from None
            raise HTTPException(503, detail={"code": "study_evidence_unavailable"}) from None

    @router.get("")
    def studies(source: Source) -> dict[str, object]:
        return lookup(source.collection)

    @router.get("/{study_id}/experiments")
    def experiments(
        study_id: str,
        source: Source,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, object]:
        if not re.fullmatch(r"[a-f0-9]{64}", study_id):
            raise HTTPException(404, detail={"code": "study_not_found"})
        return lookup(source.experiments, study_id, offset, limit)

    @router.get("/{study_id}/experiments/{experiment_id}")
    def experiment(study_id: str, experiment_id: str, source: Source) -> dict[str, object]:
        if not re.fullmatch(r"[a-f0-9]{64}", study_id):
            raise HTTPException(404, detail={"code": "study_not_found"})
        if not re.fullmatch(r"[a-f0-9]{64}", experiment_id):
            raise HTTPException(404, detail={"code": "experiment_not_found"})
        return lookup(source.experiment, study_id, experiment_id)

    return router
