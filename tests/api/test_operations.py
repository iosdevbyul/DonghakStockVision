"""Synthetic offline Operations evidence; never production acquisition approval."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.operations import OperationsReader, utc_stamp
from donghak_stock_vision.api.snapshot import SnapshotReader
from donghak_stock_vision.backtest import dart_document_batch as batch
from donghak_stock_vision.backtest.dart_batch_orchestrator import prepare_next
from tests.api.helpers import snapshot_payload, write_snapshot
from tests.backtest.test_dart_batch_orchestrator import files
from tests.backtest.test_dart_batch_orchestrator import root as root

PREFIX = "/api/v1/operations/"


def client_for(root: Path, **kwargs: Any) -> TestClient:
    return TestClient(create_app(operations_reader=OperationsReader(root, **kwargs)))


def test_empty_registry_and_missing_optional_sources(tmp_path: Path) -> None:
    client = TestClient(create_app())
    runs = client.get(PREFIX + "runs").json()
    assert runs["items"] == [] and runs["evidenceStatus"] == "unavailable"
    assert client.get(PREFIX + "freshness").json()["items"][0]["freshnessStatus"] == "unknown"
    alerts = client.get(PREFIX + "alerts").json()["items"]
    for identifier in ("corporate-actions", "runner-tail", "run-input-freeze"):
        row = next(a for a in alerts if a["id"] == identifier)
        assert row["category"] == "missing_evidence" and row["verifiedCount"] is None
    assert list(tmp_path.iterdir()) == []


def test_completed_run_has_no_invented_timestamp_or_semantic_approval(root: Path) -> None:
    payload = client_for(root).get(PREFIX + "runs").json()
    assert payload["schemaVersion"] == "operations_v1"
    assert payload["total"] == 1
    row = payload["items"][0]
    assert row["id"] == "batch-0001"
    assert row["executionStatus"] == "completed"
    assert row["evidenceStatus"] == "validated"
    assert row["recordCount"] == 25
    assert row["startedAt"] is None and row["completedAt"] is None
    assert row["semanticApproval"] is False
    fresh = client_for(root).get(PREFIX + "freshness").json()["items"][1]
    assert fresh["recordCount"] == 26
    assert fresh["latestSuccessfulIngestionAt"].endswith("Z")
    assert fresh["latestVerifiedDataAt"] is None
    assert fresh["freshnessStatus"] == "unknown"


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "offset=100001"])
def test_pagination_bounds(query: str) -> None:
    client = TestClient(create_app())
    for endpoint in ("runs", "alerts"):
        assert client.get(PREFIX + endpoint + "?" + query).status_code == 422


def test_pagination_end_and_stable_schema(root: Path) -> None:
    client = client_for(root)
    assert client.get(PREFIX + "runs?offset=1&limit=1").json()["items"] == []
    schema = client.get("/openapi.json").json()
    assert "OperationRun" in schema["components"]["schemas"]
    assert client.get(PREFIX + "runs").json() == client.get(PREFIX + "runs").json()


def test_draft_is_not_an_execution(root: Path) -> None:
    prepare_next(root)
    assert client_for(root).get(PREFIX + "runs").json()["total"] == 1


def test_incomplete_run(root: Path) -> None:
    (root / batch.DIRECTORY / "runs/batch-0001/events/complete.json").unlink()
    client = client_for(root)
    assert client.get(PREFIX + "runs").json()["items"][0]["executionStatus"] == "incomplete"
    assert "execution_interruption" in {
        a["category"] for a in client.get(PREFIX + "alerts").json()["items"]
    }


@pytest.mark.parametrize("kind", ["body", "compressed", "receipt", "completion", "binding"])
def test_corrupt_or_missing_evidence_fail_closed(root: Path, kind: str) -> None:
    unit = root / batch.DIRECTORY / "runs/batch-0001"
    if kind == "body":
        next((unit / "sources").glob("*.gz")).write_bytes(b"invalid secret /private/key")
    elif kind == "compressed":
        next((unit / "sources").glob("*.gz")).write_bytes(
            b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\xff" * 20
        )
    elif kind == "receipt":
        next((unit / "sources").glob("*.json")).unlink()
    elif kind == "completion":
        (unit / "events/complete.json").write_text('{"password":"sensitive"}')
    else:
        from donghak_stock_vision.backtest.dart_multi_batch_session import prepare

        prepare(root, max_requests=25)
        (root / ".data/dart-multi-batch-sessions/session-0001/binding.json").write_text("{}")
    client = client_for(root)
    payload = client.get(PREFIX + "runs").json()
    assert payload["evidenceStatus"] == "invalid" and payload["items"] == []
    assert client.get(PREFIX + "freshness").json()["items"][1]["freshnessStatus"] == "blocked"
    text = client.get(PREFIX + "alerts").text
    assert str(root) not in text and "password" not in text and "sensitive" not in text


@pytest.mark.parametrize("failed", [False, True])
def test_reservations_and_recorded_failures(root: Path, failed: bool) -> None:
    unit = root / batch.DIRECTORY / "runs/batch-0001"
    (unit / "events/complete.json").unlink()
    receipt = next((unit / "sources").glob("*.json"))
    receipt.with_suffix(".gz").unlink()
    number = json.loads(receipt.read_text())["params"]["rcept_no"]
    receipt.unlink()
    event = unit / "events" / f"{number}.json"
    if failed:
        record = json.loads(event.read_text())
        record["state"] = "Failed"
        record["exception"] = "secret /private/path"
        event.write_text(json.dumps(record))
    else:
        event.unlink()
    client = client_for(root)
    row = client.get(PREFIX + "runs").json()["items"][0]
    assert row["recordCount"] == 24
    categories = {a["category"] for a in client.get(PREFIX + "alerts").json()["items"]}
    assert ("acquisition_failure" if failed else "unresolved_reservation") in categories
    assert "secret" not in client.get(PREFIX + "alerts").text


def test_stale_acquisition_requires_explicit_policy(root: Path) -> None:
    for path in root.rglob("*.json"):
        value = json.loads(path.read_text())
        if "retrieved_at" in value:
            value["retrieved_at"] = "2000-01-01T00:00:00+00:00"
            path.write_text(json.dumps(value))
    client = client_for(root, acquisition_threshold_seconds=60)
    row = client.get(PREFIX + "freshness").json()["items"][1]
    assert row["freshnessStatus"] == "stale"
    assert row["freshnessBasis"] == "acquisition_age"
    assert row["thresholdSeconds"] == 60
    assert row["latestSuccessfulIngestionAt"] == "2000-01-01T00:00:00Z"
    assert "freshness_stale" in {
        a["category"] for a in client.get(PREFIX + "alerts").json()["items"]
    }


def test_missing_ingestion_timestamps_remain_unknown(root: Path) -> None:
    for path in root.rglob("*.json"):
        value = json.loads(path.read_text())
        if "retrieved_at" in value:
            del value["retrieved_at"]
            path.write_text(json.dumps(value))
    row = (
        client_for(root, acquisition_threshold_seconds=60)
        .get(PREFIX + "freshness")
        .json()["items"][1]
    )
    assert row["latestSuccessfulIngestionAt"] is None
    assert row["freshnessStatus"] == "unknown"


def test_snapshot_is_immutable_and_blocking_states_are_evidence_based(tmp_path: Path) -> None:
    payload = snapshot_payload()
    payload["baselineBacktest"]["readiness"] = [
        {"label": label, "state": "blocked", "detail": "synthetic"}
        for label in ("Corporate actions", "Runner tail integration", "Run input freeze")
    ]
    path = write_snapshot(tmp_path / "snapshot.json", payload)
    reader = SnapshotReader.from_path(path)
    path.write_text("corrupt replacement")
    client = TestClient(create_app(reader))
    alerts = client.get(PREFIX + "alerts").json()["items"]
    assert sum(row["category"] == "pipeline_blocked" for row in alerts) == 3
    row = client.get(PREFIX + "freshness").json()["items"][0]
    assert row["latestDataDate"] == "2026-10-01"
    assert row["latestVerifiedDataAt"] is None
    assert row["recordCount"] == 1099255


def test_gets_never_write_or_dispatch_or_load_credentials(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        pytest.fail("GET attempted a side effect")

    before = files(root)
    monkeypatch.setattr(batch, "execute", forbidden)
    monkeypatch.setattr(batch, "write_once", forbidden)
    monkeypatch.setattr(batch, "dotenv_values", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    client = client_for(root)
    for endpoint in ("runs", "freshness", "alerts"):
        response = client.get(PREFIX + endpoint)
        assert response.status_code == 200
        assert str(root) not in response.text
        assert "s" * 40 not in response.text
        assert "approval.json" not in response.text
    assert files(root) == before


@pytest.mark.parametrize(
    "stamp, expected",
    [
        (None, None),
        ("bad", None),
        ("2026-10-01T12:00:00", None),
        ("2026-10-01T12:00:00+09:00", "2026-10-01T03:00:00Z"),
    ],
)
def test_utc_timestamp_contract(stamp: object, expected: str | None) -> None:
    assert utc_stamp(stamp) == expected


def test_configured_empty_root_is_unavailable(tmp_path: Path) -> None:
    assert client_for(tmp_path).get(PREFIX + "runs").json()["evidenceStatus"] == "unavailable"
    assert list(tmp_path.iterdir()) == []


def test_active_writer_blocks_consistent_snapshot(root: Path) -> None:
    import fcntl

    with (root / batch.DIRECTORY / "execution.lock").open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = client_for(root).get(PREFIX + "runs")
        assert response.json()["evidenceStatus"] == "unavailable"
        assert response.json()["items"] == []

        assert response.json()["diagnostic"] == "dart_evidence_busy"


def test_collection_audit_is_partial_pinned_and_read_only(tmp_path: Path) -> None:
    import hashlib
    import sqlite3

    from donghak_stock_vision.api.ohlcv import OhlcvMarketReader
    from tests.api.test_ohlcv import make_database

    path = tmp_path / "market.sqlite3"
    make_database(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE collection_runs (id INTEGER, finished_at TEXT, status TEXT, rows INTEGER)"
        )
        db.execute(
            "INSERT INTO collection_runs VALUES (1, '2025-07-01T12:00:00+09:00', 'success', 1)"
        )
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    market = OhlcvMarketReader(path, expected_sha256=sha)
    before = files(tmp_path)
    client = client_for(tmp_path, market=market)
    row = client.get(PREFIX + "runs").json()["items"][0]
    assert row["operationType"] == "market_ingestion"
    assert row["completedAt"] == "2025-07-01T03:00:00Z"
    assert row["evidenceStatus"] == "partial"
    for endpoint in ("runs", "freshness", "alerts"):
        assert client.get(PREFIX + endpoint).status_code == 200
    assert files(tmp_path) == before
    path.write_bytes(path.read_bytes() + b"corruption")
    result = client.get(PREFIX + "runs").json()
    assert result["evidenceStatus"] == "invalid" and result["items"] == []


def test_corporate_counts_only_come_from_snapshot_evidence(tmp_path: Path) -> None:
    payload = snapshot_payload()
    payload["baselineBacktest"]["readiness"] = [
        {"label": "Corporate actions", "state": "blocked", "detail": "synthetic"}
    ]
    payload["evidence"] = [
        {
            "id": "corporate-actions",
            "title": "synthetic",
            "state": "blocked",
            "summary": "synthetic",
            "sourceLabel": "synthetic",
            "metrics": [{"label": "Complete coverage", "value": "0 / 993", "detail": "synthetic"}],
            "findings": [],
            "blocker": None,
            "fingerprint": None,
            "sourceScope": "synthetic",
        }
    ]
    client = TestClient(
        create_app(SnapshotReader.from_path(write_snapshot(tmp_path / "snapshot.json", payload)))
    )
    alert = next(
        a for a in client.get(PREFIX + "alerts").json()["items"] if a["id"] == "corporate-actions"
    )
    assert (alert["verifiedCount"], alert["requiredCount"]) == (0, 993)


def test_unsafe_source_date_and_exception_are_not_echoed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = snapshot_payload()
    payload["coverage"]["endDate"] = "/private/secret"
    reader = SnapshotReader.from_path(write_snapshot(tmp_path / "snapshot.json", payload))
    client = TestClient(create_app(reader))
    assert "/private/secret" not in client.get(PREFIX + "freshness").text

    def broken(*args: object, **kwargs: object) -> Any:
        raise ValueError("API_KEY=secret /private/credentials")

    (tmp_path / batch.DIRECTORY).mkdir(parents=True)
    monkeypatch.setattr(batch, "plan_for", broken)
    result = client_for(tmp_path).get(PREFIX + "runs")
    assert result.json()["evidenceStatus"] == "invalid"
    assert "secret" not in result.text and str(tmp_path) not in result.text


@pytest.mark.parametrize("malformed", [False, True])
def test_missing_or_malformed_optional_database_audit_never_returns_500(
    tmp_path: Path, malformed: bool
) -> None:
    import hashlib
    import sqlite3

    from donghak_stock_vision.api.ohlcv import OhlcvMarketReader
    from tests.api.test_ohlcv import make_database

    path = tmp_path / "market.sqlite3"
    make_database(path)
    if malformed:
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE collection_runs "
                "(id INTEGER, finished_at TEXT, status TEXT, rows INTEGER)"
            )
            db.execute("INSERT INTO collection_runs VALUES (1, NULL, 'success', NULL)")
    market = OhlcvMarketReader(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    client = client_for(tmp_path, market=market)
    response = client.get(PREFIX + "runs")
    assert response.status_code == 200
    assert response.json()["evidenceStatus"] == "invalid"
    assert response.json()["items"] == []
    assert "market-audit" in {a["id"] for a in client.get(PREFIX + "alerts").json()["items"]}


def test_optional_root_lookup_error_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.exists

    def denied(path: Path) -> bool:
        if path == tmp_path / batch.DIRECTORY:
            raise PermissionError("secret /private/credentials")
        return original(path)

    monkeypatch.setattr(Path, "exists", denied)
    response = client_for(tmp_path).get(PREFIX + "runs")
    assert response.status_code == 200
    assert response.json()["evidenceStatus"] == "invalid"
    assert "secret" not in response.text


def test_busy_source_never_fabricates_validation_incident(root: Path) -> None:
    import fcntl

    with (root / batch.DIRECTORY / "execution.lock").open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        alerts = client_for(root).get(PREFIX + "alerts").json()["items"]
        assert not any(a["category"] == "validation_failure" for a in alerts)
