"""Synthetic rehearsals with original receipt, model, checkpoint and journal owners."""

import calendar
import copy
import hashlib
import json
import random
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_autonomous_research import offline as offline
from tests.integration.test_gemma_research import healthy
from tests.integration.test_real_market_dataset import request_data as request_data
from tests.integration.test_shadow_prediction import evaluation_request as evaluation_request
from tests.integration.test_shadow_prediction import (
    shadow_fixture as shadow_fixture,
)

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.official_evidence import KIND_URL, normalize_month
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.models.direction_evaluation import summarize
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage import direction_shadow as owner
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, prepare, terminals, worker
from donghak_stock_vision.storage.forward_signal_journal import GENESIS
from donghak_stock_vision.storage.krx_backfill import write_new
from donghak_stock_vision.storage.operational_market import DB as OP_DB
from donghak_stock_vision.storage.operational_market import OperationalMarketStore, StorageLimits


def _css(y: int, m: int, n: int) -> str:
    return "sat" if date(y, m, n).weekday() == 5 else "sun" if date(y, m, n).weekday() == 6 else ""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def direction_source(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[dict[str, Any], datetime, datetime]:
    original: dict[str, Any] = request.getfixturevalue("request_data")
    root = tmp_path_factory.mktemp("direction-models")
    study = root / "study"
    pins = root / "study-pins"
    req = {k: copy.deepcopy(original[k]) for k in ("research", "protocol", "tickers", "cutoff")}
    req.update(
        version="autonomous_direction_source_v2",
        dataset_version="offline-direction",
        horizons=[1, 5, 20],
    )
    planpin = prepare(req, study, pins).to_dict()["plan_id"]
    # Three existing workers fit one candidate for each horizon. No success mocks.
    from tests.integration.test_autonomous_research import attempt

    for _ in range(3):
        worker(study, planpin, attempt(study, planpin))
    with closing(ReadOnlyAnalysisStore(study / DB)) as store:
        done = terminals(store)
    assert len(done) == 3 and {r["config"]["goal"]["horizon"] for r in done.values()} == {1, 5, 20}
    reg_request = dict(
        version="direction_shadow_registration_request_v3",
        models=[
            dict(root=str(study), plan_pin_file=str(pins / "plan.pin"), experiment_id=k)
            for k in done
        ],
        tickers=["000001", "000002"],
        source_kind="offline_fixture",
    )
    start = date.today() + timedelta(days=40)
    start = start.replace(day=1)
    dates: list[date] = []
    day = start
    while len(dates) < 90:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    at = datetime.combine(dates[65] + timedelta(days=1), time(19), SEOUL).astimezone(UTC)
    mature = datetime.combine(dates[87] + timedelta(days=1), time(19), SEOUL).astimezone(UTC)
    reg = owner.register(reg_request, at=at - timedelta(hours=1))
    write_new(root / "registration.json", reg.payload_json)
    write_new(root / "registration.pin", reg.identifier)
    op = root / "operational"
    OperationalMarketStore.initialize(op, StorageLimits(min_free_bytes=0))
    evidence = []
    prices = {"000001": 10000, "000002": 15000}
    rng = random.Random(246)
    with closing(OperationalMarketStore(op, writable=True)) as store:
        for i, day in enumerate(dates):
            rows = []
            for t in prices:
                old = prices[t]
                price = max(500, round(old * (1 + rng.gauss(0, 0.014))))
                prices[t] = price
                rows.append(
                    dict(
                        ISU_CD=t,
                        BAS_DD=day.strftime("%Y%m%d"),
                        MKT_NM="KOSPI",
                        TDD_OPNPRC=str(old),
                        TDD_HGPRC=str(max(old, price) + 10),
                        TDD_LWPRC=str(min(old, price) - 10),
                        TDD_CLSPRC=str(price),
                        ACC_TRDVOL=str(10000 + i * 17),
                        ACC_TRDVAL=str((10000 + i * 17) * price),
                    )
                )
            page = RawPage(
                json.dumps({"OutBlock_1": rows}).encode(),
                datetime.combine(day + timedelta(days=1), time(8), SEOUL),
                day,
            )
            b = store.ingest("direction-" + str(i), "KOSPI", page, source_kind="synthetic_fixture")
            evidence.append(
                dict(
                    kind="offline_fixture_batch",
                    batch_id="direction-" + str(i),
                    batch_pin=b.identifier,
                )
            )
    calendars = []
    for year, month in sorted({(d.year, d.month) for d in dates}):
        body = (
            '<table id="calBig">'
            + "".join(
                f'<tr><td class="{_css(year, month, n)}">{n}</td></tr>'
                for n in range(1, calendar.monthrange(year, month)[1] + 1)
            )
            + "</table>"
        ).encode()
        meta = dict(
            url=KIND_URL,
            status=200,
            raw_sha256=hashlib.sha256(body).hexdigest(),
            params=dict(
                method="searchTraidingCalendar",
                ldMktTpCd="1",
                selYear=str(year),
                selMonth=str(month),
            ),
        )
        raw = root / f"{year}-{month}.html"
        raw.write_bytes(body)
        info = root / f"{year}-{month}.json"
        info.write_text(json.dumps(meta))
        calendars.append(
            dict(
                raw=str(raw),
                source=str(info),
                normalized_pin=normalize_month(body, meta).identifier,
            )
        )
    d = dict(
        version="direction_shadow_request_v3",
        registration=str(root / "registration.json"),
        registration_pin_file=str(root / "registration.pin"),
        research=original["research"],
        protocol=original["protocol"],
        operational=dict(root=str(op), pin=sha(op / OP_DB), evidence=evidence),
        context_window=dict(
            start=dates[0].isoformat(), end=(dates[0] - timedelta(days=1)).isoformat()
        ),
        input_trading_date=dates[65].isoformat(),
        calendars=calendars,
        mode="research_forward_candidate",
    )
    return d, at, mature


def paths(root: Path) -> tuple[Path, Path, Path]:
    root.mkdir(mode=0o700, exist_ok=True)
    return root / "journal.sqlite3", root / "intents", root / "pins"


def test_all_horizons_pending_resolve_replay_and_source_integrity(
    direction_source: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, mature = direction_source
    journal, intents, pins = paths(tmp_path)
    monkeypatch.setattr(owner, "probe", healthy)
    protected = [
        Path(d["research"]["database"]),
        Path(d["operational"]["root"]) / OP_DB,
        Path(d["registration"]),
    ]
    before = [sha(p) for p in protected]
    first = owner.record(d, journal, GENESIS, intents, pins, at=at)
    head = first["head_pin"]
    assert first["new_predictions"] == 6
    again = owner.record(d, journal, head, intents, pins, at=at)
    assert again["new_predictions"] == 0 and again["duplicates"] == 6 and again["head_pin"] == head
    pending = owner.resolve(d, journal, head, intents, pins, at=at)
    head = pending["head_pin"]
    assert pending["pending"] == 6 and not pending["evaluated_this_run"]
    summary = owner.report(d, journal, head)
    assert {m["horizon"] for m in summary["models"]} == {1, 5, 20}
    assert all(m["independentAnchors"] == 0 for m in summary["models"])
    done = owner.resolve(d, journal, head, intents, pins, at=mature)
    head = done["head_pin"]
    assert done["evaluated_this_run"] > 0
    repeat = owner.resolve(d, journal, head, intents, pins, at=mature)
    assert repeat["head_pin"] == head and repeat["evaluated_this_run"] == 0
    result = owner.report(d, journal, head)
    assert all(m["independentAnchors"] == 0 for m in result["models"])  # fixture never qualified.
    assert sum(m["evaluated"] + m["excluded"] for m in result["models"]) == 6
    assert [sha(p) for p in protected] == before
    altered = copy.deepcopy(d)
    altered["input_trading_date"] = "2024-07-01"
    with pytest.raises(ValueError):
        owner.record(altered, journal, head, intents, pins, at=at)
    with pytest.raises(ValueError):
        owner.record(d, journal, "f" * 64, intents, pins, at=at)


def test_deadline_missing_session_model_pin_and_cross_contract(
    direction_source: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, mature = direction_source
    journal, intents, pins = paths(tmp_path)
    monkeypatch.setattr(owner, "probe", healthy)
    with pytest.raises(ValueError, match="deadline"):
        owner.record(d, journal, GENESIS, intents, pins, at=mature)
    altered = copy.deepcopy(d)
    altered["registration_pin_file"] = str(tmp_path / "wrong.pin")
    write_new(Path(altered["registration_pin_file"]), "f" * 64)
    with pytest.raises(ValueError, match="registration_pin"):
        owner.record(altered, journal, GENESIS, intents, pins, at=at)
    altered = copy.deepcopy(d)
    altered["calendars"] = d["calendars"][:1]
    with pytest.raises(ValueError):
        owner.record(altered, journal, GENESIS, intents, pins, at=at)
    # Original fixture raw bytes remain untouched; damage only an isolated copy.
    import shutil

    copyroot = tmp_path / "damaged"
    shutil.copytree(d["operational"]["root"], copyroot)
    with sqlite3.connect(copyroot / OP_DB) as c:
        c.execute("DROP TRIGGER raw_pages_immutable_update")
        c.execute("UPDATE raw_pages SET body=x'00' WHERE id=1")
        c.commit()
    altered = copy.deepcopy(d)
    altered["operational"]["root"] = str(copyroot)
    altered["operational"]["pin"] = sha(copyroot / OP_DB)
    with pytest.raises(ValueError):
        owner.record(altered, journal, GENESIS, intents, pins, at=at)


def test_committed_interruption_recovery_and_invalid_source_do_not_rewrite(
    direction_source: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from donghak_stock_vision.storage import shadow_prediction as original_owner

    d, at, _ = direction_source
    journal, intents, pins = paths(tmp_path)
    monkeypatch.setattr(owner, "probe", healthy)
    from donghak_stock_vision.storage.forward_signal_journal import append_shadow_event

    append = append_shadow_event

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        append(*args, **kwargs)
        raise OSError("simulated_after_commit")

    monkeypatch.setattr(original_owner, "append_shadow_event", interrupted)
    with pytest.raises(OSError, match="after_commit"):
        owner.record(d, journal, GENESIS, intents, pins, at=at)
    monkeypatch.setattr(original_owner, "append_shadow_event", append)
    intent = next(intents.glob("*.json"))
    pin = pins / (intent.stem + ".pin")
    before = journal.read_bytes()
    recovered = owner.recover(d, journal, GENESIS, intent, pin)
    assert recovered["recovered"] and journal.read_bytes() == before
    result = owner.record(
        d, journal, recovered["head_pin"], intents, pins, at=at + timedelta(seconds=1)
    )
    assert result["new_predictions"] == 5 and result["duplicates"] == 1
    assert len(owner.report(d, journal, result["head_pin"])["predictions"]) == 6
    damaged = copy.deepcopy(d)
    damaged["operational"]["pin"] = "f" * 64
    before = journal.read_bytes()
    with pytest.raises(ValueError):
        owner.resolve(damaged, journal, result["head_pin"], intents, pins, at=at)
    assert journal.read_bytes() == before


def test_matching_population_comparison_and_duplicate_anchors() -> None:
    from donghak_stock_vision.data.direction_shadow import contract
    from donghak_stock_vision.data.learning import digest

    model = digest("model1")
    other = digest("model2")
    identity = dict(
        modelId=model,
        predictionType="future_close_direction",
        featureVersion="causal_daily_closed_observed_periods_v2",
        modelSha256=digest("artifact"),
        horizon=20,
        mode="research_forward_candidate",
        sourceQuality="local_provider_capture",
        labelContract=contract(20)["version"],
        evaluationPolicyId=digest(contract(20)["evaluation"]),
    )
    rows = [
        dict(
            **identity,
            predictionId=digest([model, t]),
            ticker=t,
            inputTradingDate="2026-01-01",
            targetTradingDate="2026-01-05",
            outcomeSessions=[
                (date(2026, 1, 5) + timedelta(days=n)).isoformat()
                for n in range(26)
                if (date(2026, 1, 5) + timedelta(days=n)).weekday() < 5
            ],
            inputCohort=i,
            actualLabel=i % 2,
            state="evaluated",
            score=0.6,
            baselineScore=0.5,
            createdAt="2026-01-01T00:00:00+00:00",
        )
        for i, t in enumerate(("000001", "000002"))
    ]
    # Pure bookkeeping rows need no event schema. Production validates exact H calendars first.
    second = [
        r
        | dict(
            modelId=other, modelSha256=digest("other"), predictionId=digest([other, r["ticker"]])
        )
        for r in rows
    ]
    report = summarize(
        rows + second, [identity, identity | dict(modelId=other, modelSha256=digest("other"))]
    )
    assert all(m["independentAnchors"] == 1 for m in report["models"])
    assert report["comparisons"][0]["comparable"]
    assert report["comparisons"][0]["predictionType"] == "future_close_direction"
    second[0]["actualLabel"] = 1 - second[0]["actualLabel"]
    assert not summarize(
        rows + second, [identity, identity | dict(modelId=other, modelSha256=digest("other"))]
    )["comparisons"][0]["comparable"]


def test_authenticated_api_snapshot_no_paths_and_tampered_metrics(
    direction_source: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from donghak_stock_vision.ai_model_routes import Principal, ScopedBearerAuthority
    from donghak_stock_vision.api.app import create_app
    from donghak_stock_vision.direction_shadow_routes import DirectionPublication

    d, at, _ = direction_source
    journal, intents, pins = paths(tmp_path)
    monkeypatch.setattr(owner, "probe", healthy)
    result = owner.record(d, journal, GENESIS, intents, pins, at=at)
    view = owner.report(d, journal, result["head_pin"])
    path = tmp_path / "publication.json"
    write_new(path, json.dumps(view))
    reader = DirectionPublication(path, sha(path))
    authority = ScopedBearerAuthority(
        [("offline-fixture-secret-123456789", Principal("test", frozenset({"research:read"})))]
    )
    with pytest.raises(ValueError, match="authenticated_read_pair"):
        create_app(direction_reader=reader)
    from donghak_stock_vision import api_cli

    captured: dict[str, Any] = {}

    def serve(application: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setenv("DIRECTION_FIXTURE_READ_TOKEN", "offline-fixture-secret-123456789")
    monkeypatch.setenv("DSV_API_SNAPSHOT_PATH", "/private/must-not-open-research-snapshot")
    monkeypatch.setattr(api_cli, "load_dotenv", lambda: None)
    monkeypatch.setattr("uvicorn.run", serve)
    api_cli.main(
        [
            "--direction-only",
            "--direction-publication",
            str(path),
            "--direction-publication-sha256",
            sha(path),
            "--direction-read-token-env",
            "DIRECTION_FIXTURE_READ_TOKEN",
        ]
    )
    assert captured["proxy_headers"] is False
    app = create_app(direction_reader=reader, direction_authorize=authority)
    with TestClient(app, client=("127.0.0.1", 1234), base_url="http://127.0.0.1") as client:
        route = "/api/v1/research/predictions"
        assert client.get(route).status_code == 401
        headers = {"Authorization": "Bearer offline-fixture-secret-123456789"}
        response = client.get(route + "?horizon=20&limit=1", headers=headers)
        assert (
            response.status_code == 200
            and response.json()["total"] == 2
            and len(response.json()["items"]) == 1
        )
        assert (
            "/private/" not in response.text
            and "input_source" not in response.text
            and "checkpoint" not in response.text
        )
        assert client.get(route + "/models", headers=headers).status_code == 200
        assert (
            client.get(route, headers=headers | {"Origin": "https://invalid.example"}).status_code
            == 403
        )
        assert client.get(route + "?horizon=2", headers=headers).status_code == 422
        assert client.get(route, headers=headers | {"Origin": "null"}).status_code == 403
        assert client.post(route, headers=headers, json={}).status_code == 405
        changed = copy.deepcopy(view)
        changed["models"][0]["metrics"]["accuracy"] = 0.99
        path.write_text(json.dumps(changed))
        path.chmod(0o600)
        assert client.get(route, headers=headers).status_code == 503
        with pytest.raises(ValueError):
            DirectionPublication(path, sha(path))
    secret = copy.deepcopy(view)
    secret["predictions"][0]["checkpoint"] = "/private/secret"
    path.write_text(json.dumps(secret))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        DirectionPublication(path, sha(path))


def test_shared_label_primitive_preserves_exact_neutral_band() -> None:
    from donghak_stock_vision.models.research_v2 import direction_label

    assert direction_label(10000, 10020) is None
    assert direction_label(10000, 9980) is None
    assert direction_label(10000, 10021) == 1
    assert direction_label(10000, 9979) == 0


def test_reregistration_and_rehashed_model_substitution_fail_closed(
    direction_source: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, _ = direction_source
    journal, intents, pins = paths(tmp_path)
    monkeypatch.setattr(owner, "probe", healthy)
    first = owner.record(d, journal, GENESIS, intents, pins, at=at)
    reg = owner.registration(Path(d["registration"]), Path(d["registration_pin_file"])).to_dict()
    reg["registered_at"] = (at - timedelta(minutes=10)).isoformat()
    path = tmp_path / "other-registration.json"
    pin = tmp_path / "other-registration.pin"
    value = FrozenJSON.freeze(reg)
    write_new(path, value.payload_json)
    write_new(pin, value.identifier)
    changed = d | dict(registration=str(path), registration_pin_file=str(pin))
    before = journal.read_bytes()
    with pytest.raises(ValueError, match="separate_contract"):
        owner.record(changed, journal, first["head_pin"], intents, pins, at=at)
    reg["models"]["1"]["config"]["goal"]["horizon"] = 20
    path = tmp_path / "forged-model.json"
    pin = tmp_path / "forged-model.pin"
    value = FrozenJSON.freeze(reg)
    write_new(path, value.payload_json)
    write_new(pin, value.identifier)
    with pytest.raises(ValueError, match="model_changed"):
        owner.registration(path, pin)
    assert journal.read_bytes() == before
    # Protected destinations are rejected before any append, even for direct calls.
    with pytest.raises(ValueError, match="protected_output"):
        owner.record(
            d, Path(d["operational"]["root"]) / "forbidden.sqlite3", GENESIS, intents, pins, at=at
        )


def test_legacy_projection_preserves_event_semantics_and_private_evidence(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    from donghak_stock_vision.direction_shadow_routes import DirectionPublication
    from donghak_stock_vision.storage import shadow_prediction as legacy

    d, at, _ = shadow_fixture
    journal, intents, pins = paths(tmp_path / "legacy")
    result = legacy.record(d, journal, GENESIS, intents, pins, clock=lambda: at).to_dict()
    request = tmp_path / "request.json"
    request_pin = tmp_path / "request.pin"
    head = tmp_path / "head.pin"
    write_new(request, json.dumps(d))
    write_new(request_pin, digest(d))
    write_new(head, result["head_pin"])
    before = journal.read_bytes()
    view = owner.publish_streams(
        dict(
            version="direction_shadow_collection_v1",
            streams=[
                dict(
                    kind="legacy_v1_v2",
                    request=str(request),
                    request_pin_file=str(request_pin),
                    journal=str(journal),
                    head_pin_file=str(head),
                )
            ],
        )
    )
    assert len(view["predictions"]) == 5
    assert all(
        p["predictionType"] == "conditional_reversal_event"
        and p["conditioningDirection"] in ("up", "down")
        and p["labelContract"] == "reversal_barrier_v1"
        for p in view["predictions"]
    )
    path = tmp_path / "public.json"
    write_new(path, json.dumps(view))
    reader = DirectionPublication(path, sha(path))
    assert len(reader.read().predictions) == 5 and journal.read_bytes() == before
    assert "/private/" not in json.dumps(view) and "input_source_pin" not in json.dumps(view)
