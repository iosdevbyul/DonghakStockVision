"""Retained-artifact projection fixtures are not genuine provider acquisitions."""

import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.daily_research import DailyResearchReader, DailyResearchReaderError
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan, FullMarketScope
from donghak_stock_vision.data.learning import FEATURES, digest
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.daily_research import JOB, RESULT

URL = "/api/v1/stocks/005930/research-analysis"
DAYS = [date(2025, 7, n) for n in (7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 21, 22, 23)]
SECRET = "/PRIVATE/DO_NOT_EXPOSE/policy-and-key.env"


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def job(index: int = 10, *, real: bool = False) -> dict[str, Any]:
    scope = FullMarketScope(
        datetime(2025, 7, 5, tzinfo=UTC), datetime(2025, 7, 31, tzinfo=UTC), tuple(DAYS)
    )
    policy_pin, terms_pin = digest("private-policy"), digest("private-terms")
    origin = "real_provider_https_local_capture" if real else "authorized_provider_mock_fixture"
    sources = []
    for day in DAYS[: index + 1]:
        plan = ForwardCapturePlan(
            "KOSPI",
            "",
            day,
            "forward_market_observation",
            None,
            None,
            1,
            2097152,
            30,
            1,
            1,
            "authorized_provider",
            policy_pin,
            terms_pin,
            full_market_scope=scope,
        )
        batch_pin = digest(["private-batch", day.isoformat()])
        sources.append(
            {
                "completion": {
                    "day": day.isoformat(),
                    "at": datetime.combine(
                        day + timedelta(days=1), datetime.min.time(), UTC
                    ).isoformat(),
                    "observation_count": 940,
                    "origin": origin,
                    "plan": plan.document.to_dict(),
                    "plan_pin": plan.document.identifier,
                    "attempt": "contract-fixture-not-live",
                    "capture_pin": digest(["private-capture", str(day)]),
                    "batch_pin": batch_pin,
                    "authorization": {"root": SECRET, "grant_pin": policy_pin},
                },
                "proof": {
                    "version": "verified_operational_completion_v1",
                    "day": day.isoformat(),
                    "observation_count": 940,
                    "origin": origin,
                    "batch_id": digest(["batch", str(day)]),
                    "batch_pin": batch_pin,
                    "network_requests": 0,
                },
            }
        )
    day = DAYS[index]
    return {
        "version": "daily_research_v1",
        "day": day.isoformat(),
        "tickers": ["000660", "005930"],
        "model_version": digest("fixture-model"),
        "config_pin": digest("private-config"),
        "implementation_pin": digest("implementation"),
        "lookback_days": 60,
        "source": {
            "sources": sources,
            "expected_observation_dates": [d.isoformat() for d in DAYS[: index + 1]],
            "start": (day - timedelta(days=59)).isoformat(),
            "day": day.isoformat(),
            "input_fingerprint": digest(["fixture-input-not-actual-prices", str(day)]),
            "data_origin": "real" if real else "synthetic_fixture",
        },
    }


def result(
    job_id: str,
    owner: dict[str, Any],
    *,
    state: str = "completed",
    attempt: int = 1,
    reason: str = "insufficient_history",
) -> dict[str, Any]:
    row = {
        "job_id": job_id,
        "ticker": "005930",
        "attempt": attempt,
        "checked_at": (datetime.fromisoformat(owner["day"]) + timedelta(days=1))
        .replace(tzinfo=UTC)
        .isoformat(),
        "status": state,
        "research_score": None,
    }
    if state == "completed":
        row.update(
            research_score=0.7,
            context="up",
            features=dict(
                zip(FEATURES, (0.01, 0.02, -0.01, 0.03, 0.01, 0.02, 0.04, 0.05), strict=True)
            ),
        )
    else:
        row["reason"] = reason
    return row


def archive(path: Path, *, real: bool = False) -> Path:
    with closing(AnalysisStore(path)) as store:
        for index, state in ((10, "completed"), (11, "blocked"), (12, "pending")):
            owner = job(index, real=real)
            key = store.put(JOB, owner)
            if state != "pending":
                store.put(RESULT, result(key, owner, state=state))
    return path


def client(path: Path) -> TestClient:
    return TestClient(
        create_app(
            daily_research_reader=DailyResearchReader(path, expected_sha256=fingerprint(path))
        )
    )


@pytest.mark.parametrize("real", [False, True])
def test_projection_origins_sources_scores_and_last_success(tmp_path: Path, real: bool) -> None:
    path = archive(tmp_path / "daily.sqlite3", real=real)
    before = path.read_bytes()
    api = client(path)
    body = api.get(URL).json()
    assert body["schemaVersion"] == "daily_research_series_v1"
    assert body["totalRecords"] == 3
    pending, blocked, completed = body["records"]
    assert pending["analysisId"] is None and pending["analyzedAt"] is None
    assert pending["status"] == "pending" and pending["researchScore"] is None
    assert blocked["reasonCode"] == "insufficient_history" and blocked["observedPeriod"] is None
    assert completed["researchScore"] == 0.7 and completed["researchSignal"] == "up"
    assert completed["researchContext"] == "prior_decline"
    assert completed["dataOrigin"] == ("real" if real else "synthetic_fixture")
    assert completed["usageRestriction"] == ("research_only" if real else "synthetic_test_only")
    assert completed["modelVersion"] == job()["model_version"]
    assert completed["observedPeriod"] == {"start": "2025-07-07", "end": "2025-07-21"}
    assert len(completed["sources"]) == 11
    assert body["lastSuccessfulAnalyzedAt"] == completed["analyzedAt"]
    assert body["lastSuccessfulTradingDate"] == completed["tradingDate"]
    assert body["lastSuccessfulDataOrigin"] == completed["dataOrigin"]
    assert not completed["forwardPredictionEligible"] and not completed["performanceApproved"]
    assert completed["performanceMetrics"] is None
    assert path.read_bytes() == before
    # Existing web publication route retains its independent review boundary.
    assert api.get("/api/v1/stocks/005930/analysis").status_code == 503


def test_filters_pagination_unknown_ticker_and_query_validation(tmp_path: Path) -> None:
    api = client(archive(tmp_path / "daily.sqlite3"))
    body = api.get(URL, params={"tradingDate": "2025-07-22"}).json()
    assert body["totalRecords"] == 1 and body["records"][0]["status"] == "blocked"
    assert body["lastSuccessfulAnalyzedAt"] is not None  # all retained ticker history
    assert (
        api.get(URL, params={"fromDate": "2025-07-21", "toDate": "2025-07-22"}).json()[
            "totalRecords"
        ]
        == 2
    )
    page = api.get(URL, params={"limit": 1, "offset": 1}).json()
    assert len(page["records"]) == 1 and page["totalRecords"] == 3
    assert api.get(URL.replace("005930", "999999")).json()["records"] == []
    assert api.get(URL.replace("005930", "000660")).json()["records"][0]["status"] == "pending"
    assert api.get(URL.replace("005930", "invalid")).status_code == 422
    for params in (
        {"fromDate": "2025-07-23", "toDate": "2025-07-20"},
        {"tradingDate": "2025-07-21", "fromDate": "2025-07-21"},
    ):
        assert api.get(URL, params=params).status_code == 400
    for invalid_params in ({"limit": 201}, {"offset": -1}, {"tradingDate": "not-a-date"}):
        assert api.get(URL, params=invalid_params).status_code == 422
    assert api.post(URL).status_code == 405


def test_retry_latest_result_and_private_information_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "daily.sqlite3"
    owner = job()
    with closing(AnalysisStore(path)) as store:
        key = store.put(JOB, owner)
        store.put(RESULT, result(key, owner, state="failed", reason=SECRET + " AUTH_KEY=private"))
        second = result(key, owner, state="blocked", attempt=2, reason=SECRET)
        store.put(RESULT, second)
    body = client(path).get(URL).json()
    row = body["records"][0]
    assert row["status"] == "blocked" and row["reasonCode"] == "analysis_blocked"
    text = json.dumps(body)
    private_values = [
        SECRET,
        owner["config_pin"],
        owner["source"]["sources"][0]["completion"]["capture_pin"],
        owner["source"]["sources"][0]["completion"]["batch_pin"],
        digest("private-policy"),
    ]
    assert all(value not in text for value in private_values)
    assert "features" not in text and "authorization" not in text
    with closing(AnalysisStore(path)) as store:
        key = store.all(JOB)[0][0]
        final_id = store.put(RESULT, result(key, owner, attempt=3))
    assert client(path).get(URL).json()["records"][0]["analysisId"] == final_id


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "model",
        "origin",
        "response",
        "batch",
        "score",
        "context",
        "orphan",
        "attempt",
        "time",
        "stale_completed_time",
    ],
)
def test_rehashed_semantic_substitution_fails_closed(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "invalid.sqlite3"
    owner = deepcopy(job())
    if damage == "version":
        owner["version"] = "unknown_v99"
    if damage == "model":
        owner["model_version"] = "not-an-identifier"
    if damage == "origin":
        owner["source"]["data_origin"] = "real"
    if damage == "response":
        owner["source"]["sources"][0]["proof"]["day"] = "2025-07-08"
    if damage == "batch":
        owner["source"]["sources"][0]["proof"]["batch_pin"] = "0" * 64
    with closing(AnalysisStore(path)) as store:
        key = store.put(JOB, owner)
        row = result(key, owner)
        if damage == "score":
            row["research_score"] = 1.5
        if damage == "context":
            row["context"] = "down"
        if damage == "orphan":
            row["job_id"] = "0" * 64
        if damage == "attempt":
            row["attempt"] = 2
        if damage == "time":
            row["checked_at"] = "2025-07-20T00:00:00+00:00"
        if damage == "stale_completed_time":
            row["checked_at"] = "2025-07-29T00:00:00+00:00"
        store.put(RESULT, row)
    with pytest.raises(DailyResearchReaderError, match="daily_research_snapshot_invalid"):
        DailyResearchReader(path, expected_sha256=fingerprint(path))


def test_snapshot_pin_artifact_digest_and_sidecars(tmp_path: Path) -> None:
    path = archive(tmp_path / "daily.sqlite3")
    with pytest.raises(DailyResearchReaderError):
        DailyResearchReader(path, expected_sha256="0" * 64)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TRIGGER immutable_artifact_update")
        conn.execute("UPDATE analysis_artifacts SET payload='{}' WHERE kind=?", (RESULT,))
        conn.commit()
    with pytest.raises(DailyResearchReaderError):
        DailyResearchReader(path, expected_sha256=fingerprint(path))
    path = archive(tmp_path / "sidecar.sqlite3")
    Path(str(path) + "-wal").touch()
    with pytest.raises(DailyResearchReaderError):
        DailyResearchReader(path, expected_sha256=fingerprint(path))


def test_unconfigured_empty_and_static_snapshot_behavior(tmp_path: Path) -> None:
    assert TestClient(create_app()).get(URL).status_code == 503
    path = tmp_path / "empty.sqlite3"
    with closing(AnalysisStore(path)):
        pass
    assert client(path).get(URL).json()["availability"] == "no_recorded_analysis"
    path = archive(tmp_path / "daily.sqlite3")
    reader = DailyResearchReader(path, expected_sha256=fingerprint(path))
    before = reader.series("005930")
    # Serving is an immutable startup view, never automatic trust of new disk contents.
    path.write_bytes(b"changed after startup")
    assert reader.series("005930") == before


def test_cli_explicit_snapshot_and_loopback_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.api_cli import main
    from tests.api.helpers import write_snapshot

    snapshot = write_snapshot(tmp_path / "research.json")
    daily = archive(tmp_path / "daily.sqlite3")
    apps: list[FastAPI] = []

    def serve(app: FastAPI, **kwargs: Any) -> None:
        assert kwargs["proxy_headers"] is False
        apps.append(app)

    monkeypatch.setattr("uvicorn.run", serve)
    args = [
        "--snapshot",
        str(snapshot),
        "--daily-research-snapshot",
        str(daily),
        "--daily-research-snapshot-sha256",
        fingerprint(daily),
    ]
    main(args)
    assert TestClient(apps[0]).get(URL).json()["totalRecords"] == 3
    for invalid in (args[:-2], [*args, "--host", "0.0.0.0"]):
        with pytest.raises(SystemExit):
            main(invalid)
    assert len(apps) == 1


@pytest.mark.parametrize(
    "direction,score,signal",
    [
        ("up", 0.7, "up"),
        ("down", 0.7, "down"),
        ("up", 0.2, "neutral"),
        (None, None, "neutral"),
    ],
)
def test_research_signal_projection_does_not_claim_calibrated_probability(
    tmp_path: Path,
    direction: str | None,
    score: float | None,
    signal: str,
) -> None:
    path = tmp_path / "daily.sqlite3"
    owner = job()
    with closing(AnalysisStore(path)) as store:
        key = store.put(JOB, owner)
        row = result(key, owner)
        row["context"], row["research_score"] = direction, score
        row["features"]["return_5"] = (
            -0.01 if direction == "up" else 0.01 if direction == "down" else 0
        )
        store.put(RESULT, row)
    record = client(path).get(URL).json()["records"][0]
    assert record["researchSignal"] == signal and record["researchScore"] == score
    assert record["scoreMeaning"] == "uncalibrated_conditional_research_score"
    assert not record["performanceApproved"] and not record["executionAuthorized"]


@pytest.mark.parametrize(
    "state,reason", [("failed", "research_analysis_failed"), ("blocked", "invalid_model")]
)
def test_retained_failure_and_incompatible_model_are_not_success(
    tmp_path: Path,
    state: str,
    reason: str,
) -> None:
    path = tmp_path / "daily.sqlite3"
    owner = job()
    with closing(AnalysisStore(path)) as store:
        key = store.put(JOB, owner)
        store.put(RESULT, result(key, owner, state=state, reason=reason))
    body = client(path).get(URL).json()
    record = body["records"][0]
    assert record["status"] == state and record["reasonCode"] == reason
    assert record["researchScore"] is None and record["researchSignal"] == "unavailable"
    assert body["lastSuccessfulAnalyzedAt"] is None


def test_last_success_keeps_fixture_classification_in_mixed_history(tmp_path: Path) -> None:
    path = tmp_path / "mixed.sqlite3"
    with closing(AnalysisStore(path)) as store:
        for index, real in ((10, True), (11, False)):
            owner = job(index, real=real)
            key = store.put(JOB, owner)
            store.put(RESULT, result(key, owner))
    body = client(path).get(URL, params={"tradingDate": "2025-07-21"}).json()
    assert body["records"][0]["dataOrigin"] == "real"
    assert body["lastSuccessfulTradingDate"] == "2025-07-22"
    assert body["lastSuccessfulDataOrigin"] == "synthetic_fixture"
