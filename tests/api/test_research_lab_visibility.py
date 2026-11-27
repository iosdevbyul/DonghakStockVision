"""R7 API behavior at the immutable publication boundary."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.ai_model_routes import Principal
from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.research_lab_export_cli import export
from donghak_stock_vision.storage.research_lab_publication import (
    ResearchLabError,
    ResearchLabPublication,
)
from donghak_stock_vision.storage.research_lab_views import Visibility

RUN = "1" * 64
EXPERIMENT = "2" * 64
MODEL = "3" * 64
EVAL = "4" * 64
POPULATION = "5" * 64
HEADERS = {"Authorization": "Bearer test-reader"}


def evidence() -> dict[str, Any]:
    at = "2026-11-28T00:00:00Z"
    row = dict(
        evaluationId=EVAL,
        predictionId="6" * 64,
        ticker="005930",
        inputDate="2024-01-02",
        baseDate="2024-01-03",
        targetDate="2024-01-04",
        baseClose=100,
        targetClose=110,
        actualReturn=0.10,
        probability=0.6,
        predictedClass=1,
        actualClass=1,
        correct=True,
        state="evaluated",
    )
    return dict(
        version="research_lab_visibility_v1",
        publishedAt=at,
        runs=[
            dict(
                runId=RUN,
                stage="R6",
                role="approved",
                status="COMPLETED",
                evidenceStatus="PARTIAL",
                horizons=[1],
                fits=1,
            )
        ],
        experiments=[
            dict(
                runId=RUN,
                stage="R6",
                experimentId=EXPERIMENT,
                modelId=MODEL,
                horizon=1,
                status="completed",
            )
        ],
        evaluations=[
            dict(
                evaluationId=EVAL,
                runId=RUN,
                stage="R6",
                experimentId=EXPERIMENT,
                modelId=MODEL,
                horizon=1,
                exposure="development",
                populationId=POPULATION,
                role="model",
                contract="delayed_close_direction_h1_v2",
                metrics=dict(n=1, correct=1, incorrect=0, accuracy=1.0),
                evaluatedRows=1,
                tickers=1,
                tradingDates=1,
                evidenceStatus="INSUFFICIENT_EVIDENCE",
            )
        ],
        predictions=[
            row,
            dict(
                evaluationId=EVAL,
                predictionId="7" * 64,
                ticker="000660",
                inputDate="2024-01-02",
                state="neutral",
                reason="research_neutral_cost_band",
            ),
        ],
        comparisons=[],
        decisions=[],
        feedback=[],
        prospective=dict(
            checkedAt=at,
            inputDate="2026-11-20",
            inputCurrentAtSnapshot=True,
            status="BLOCKED",
            observations=33,
            requiredObservations=35,
            legacyPending=8,
            legacyPriceWindowsComplete=4,
            historicalReplay=10,
            historicalReplayEvaluated=5,
            models=[],
        ),
        operations=dict(
            checkedAt=at,
            lastCaptureTradingDate="2026-11-20",
            latestTradingDate="2026-11-20",
            requestedAt=at,
            httpStatus=200,
            capturedRows=935,
            totalRows=30981,
            sessions=33,
            distinctTickers=942,
            duplicateRows=0,
            calendarEnd="2026-11-30",
            missingTradingDates=[],
            scheduledTimeKst="19:00",
            agentState="not_running",
            enabled=True,
            policyValid=False,
            localUsed=0,
            localRemaining=1,
            blockers=["provider_permission_expired"],
        ),
    )


def authority(token: str) -> Principal | None:
    if token == "test-reader":
        return Principal("reader", frozenset({"research:read"}))
    if token == "test-writer":
        return Principal("writer", frozenset({"ai:write"}))
    return None


def test_visibility_authentication_filters_and_read_only(tmp_path: Path) -> None:
    output = (tmp_path / "publication").resolve()
    original = evidence()
    sha = export([], output, visibility_data=json.dumps(original).encode())["indexSha256"]
    reader = ResearchLabPublication(output, sha)
    with pytest.raises(ValueError, match="authenticated_read_pair_required"):
        create_app(lab_reader=reader)
    client = TestClient(
        create_app(lab_reader=reader, lab_authorize=authority),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 6060),
    )
    prefix = "/api/v1/research/lab"
    path = prefix + "/prediction-rows"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer test-writer"}).status_code == 403
    assert (
        client.get(path, headers=HEADERS | {"Origin": "https://outside.example"}).status_code == 403
    )
    assert client.get(path, headers=HEADERS, params={"ticker": "0" * 4100}).status_code == 413
    assert client.get(path, headers=HEADERS, params={"limit": 101}).status_code == 422
    assert client.post(path, headers=HEADERS).status_code == 405
    assert client.get(prefix + "/runs/" + MODEL, headers=HEADERS).status_code == 404
    page = client.get(path, headers=HEADERS, params={"runId": RUN, "limit": 1}).json()
    assert page["total"] == 2 and page["nextOffset"] == 1
    assert page["items"][0]["state"] == "neutral"
    result = client.get(
        path,
        headers=HEADERS,
        params={
            "stage": "R6",
            "experimentId": EXPERIMENT,
            "modelId": MODEL,
            "horizon": 1,
            "ticker": "005930",
            "correct": True,
            "exposure": "development",
            "state": "evaluated",
        },
    ).json()
    assert result["total"] == 1 and result["nextOffset"] is None
    assert result["items"][0]["targetClose"] == 110
    assert (
        client.get(path, headers=HEADERS, params={"exposure": "prospective"}).json()["total"] == 0
    )
    assert client.get(prefix + "/studies").status_code == 200
    assert client.get("/openapi.json").json()["paths"][path]["get"]["security"]
    assert hashlib.sha256((output / "index.json").read_bytes()).hexdigest() == sha


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("targetDate", "2026-09-30", "protected_research_date"),
        ("targetDate", "2024-07-01", "protected_research_date"),
        ("baseClose", 0, "prediction_evidence_binding"),
        ("correct", False, "prediction_evidence_binding"),
        ("rawSource", "/private/unpublished.sqlite3", "extra_forbidden"),
    ],
)
def test_invalid_prediction_evidence_fails_closed(field: str, value: object, error: str) -> None:
    raw = evidence()
    raw["predictions"][0][field] = value
    with pytest.raises(ValueError, match=error):
        Visibility.model_validate_json(json.dumps(raw))


def test_approved_publication_rejects_changed_safe_projection(tmp_path: Path) -> None:
    output = (tmp_path / "publication").resolve()
    sha = export([], output, visibility_data=json.dumps(evidence()).encode())["indexSha256"]
    path = output / "visibility.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ResearchLabError, match="visibility_pin_mismatch"):
        ResearchLabPublication(output, sha)
