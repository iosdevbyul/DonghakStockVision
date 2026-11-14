"""Offline fixtures; original validators run, never real-forward evidence."""

import calendar
import hashlib
import json
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_real_market_dataset import request_data as request_data
from tests.integration.test_real_model_evaluation import evaluation_request as evaluation_request

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.official_evidence import KIND_URL, normalize_month
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage import forward_signal_journal as journal_owner
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, history
from donghak_stock_vision.storage.operational_market import (
    DB,
    OperationalMarketStore,
    StorageLimits,
)
from donghak_stock_vision.storage.shadow_prediction import (
    _horizon,
    correct,
    evaluation_readiness,
    events,
    record,
    recover,
    resolve,
    status,
    verify,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


@pytest.fixture(scope="module")
def shadow_fixture(
    evaluation_request: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> tuple[dict[str, Any], datetime, datetime]:
    root = tmp_path_factory.mktemp("shadow-fixture")
    with closing(ReadOnlyAnalysisStore(Path(evaluation_request["model_database"]))) as store:
        created = store.created_at("model", evaluation_request["model_version"])
    anchor = (created + timedelta(days=90)).astimezone(SEOUL).date()
    while anchor.weekday() != 4:
        anchor += timedelta(days=1)
    at = datetime.combine(anchor + timedelta(days=1), time(20), SEOUL).astimezone(UTC)
    target = anchor + timedelta(days=3)
    all_days: list[date] = []
    day = target - timedelta(days=40)
    while len([x for x in all_days if x >= target]) < 5:
        if day.weekday() < 5:
            all_days.append(day)
        day += timedelta(days=1)
    prior = [d for d in all_days if d < target][-12:]
    future = [d for d in all_days if d >= target][:5]
    op = root / "operational"
    OperationalMarketStore.initialize(op, StorageLimits(min_free_bytes=0))
    tickers = [f"{i:06d}" for i in range(1, 6)]
    prices = dict.fromkeys(tickers, 10000)
    evidence = []
    with closing(OperationalMarketStore(op, writable=True)) as store:
        for i, day in enumerate(prior + future):
            rows = []
            for n, ticker in enumerate(tickers):
                price = (
                    10000 + i * 13 + (i % 3) * 3
                    if i < 12
                    else round(prices[ticker] * (0.99 if n % 2 else 1.01))
                )
                prices[ticker] = price
                rows.append(
                    dict(
                        ISU_CD=ticker,
                        BAS_DD=day.strftime("%Y%m%d"),
                        MKT_NM="KOSPI",
                        TDD_OPNPRC=str(price),
                        TDD_HGPRC=str(price + 3),
                        TDD_LWPRC=str(price - 3),
                        TDD_CLSPRC=str(price),
                        ACC_TRDVOL=str(10000 + i * 19),
                        ACC_TRDVAL=str((10000 + i * 19) * price),
                    )
                )
            page = RawPage(
                json.dumps({"OutBlock_1": rows}).encode(),
                datetime.combine(day + timedelta(days=1), time(), SEOUL),
                day,
            )
            b = store.ingest("fixture-" + str(i), "KOSPI", page, source_kind="synthetic_fixture")
            evidence.append(
                dict(
                    kind="offline_fixture_batch",
                    batch_id="fixture-" + str(i),
                    batch_pin=b.identifier,
                )
            )
    calendars = []
    for year, month in sorted({(d.year, d.month) for d in all_days}):
        raw = '<table id="calBig">'
        for n in range(1, calendar.monthrange(year, month)[1] + 1):
            weekday = date(year, month, n).weekday()
            css = "sat" if weekday == 5 else "sun" if weekday == 6 else ""
            raw += f'<tr><td class="{css}">{n}</td></tr>'
        raw += "</table>"
        body = raw.encode()
        path = root / f"{year}-{month}.html"
        path.write_bytes(body)
        source = dict(
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
        metadata = root / f"{year}-{month}.json"
        metadata.write_text(json.dumps(source))
        calendars.append(
            dict(
                raw=str(path),
                source=str(metadata),
                normalized_pin=normalize_month(body, source).identifier,
            )
        )
    d = dict(
        version="research_shadow_request_v1",
        mode="research_forward_candidate",
        source_kind="offline_fixture",
        target_trading_date=target.isoformat(),
        tickers=tickers,
        model=dict(
            database=evaluation_request["model_database"],
            pin=evaluation_request["model_database_pin"],
            model_version=evaluation_request["model_version"],
            source_pins_root=evaluation_request["source_pins_root"],
        ),
        operational=dict(root=str(op), pin=sha(op / DB), evidence=evidence),
        calendars=calendars,
    )
    return d, at, datetime.combine(future[-1] + timedelta(days=1), time(20), SEOUL).astimezone(UTC)


def test_full_lifecycle_and_no_future_body_replay(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, at, mature = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    intents = tmp_path / "intents"
    pins = tmp_path / "pins"
    before = sha(Path(d["operational"]["root"]) / DB)
    calls = []
    original = OperationalMarketStore._verify_batch

    def spy(self: OperationalMarketStore, batch: str, raw_id: int) -> FrozenJSON:
        value = original(self, batch, raw_id)
        calls.append(value.to_dict()["request"]["source_date"])
        return value

    with patch.object(OperationalMarketStore, "_verify_batch", spy):
        r = record(d, journal, GENESIS, intents, pins, clock=lambda: at).to_dict()
    assert r["recorded"] == 5 and r["blocked"] == 0
    assert all(day < d["target_trading_date"] for day in calls)
    repeat = record(
        d, journal, r["head_pin"], intents, pins, clock=lambda: at + timedelta(seconds=1)
    ).to_dict()
    assert repeat["head_pin"] == r["head_pin"] and all(x["duplicate"] for x in repeat["results"])
    pending = resolve(
        d, journal, r["head_pin"], intents, pins, clock=lambda: at + timedelta(seconds=2)
    ).to_dict()
    assert pending["groups"]["offline_fixture_rehearsal"]["pending"] == 5
    final = resolve(d, journal, pending["head_pin"], intents, pins, clock=lambda: mature).to_dict()
    assert final["groups"]["offline_fixture_rehearsal"]["evaluated"] == 5
    assert final["groups"]["research_forward_candidate"]["predictions"] == 0
    assert (
        resolve(
            d,
            journal,
            final["head_pin"],
            intents,
            pins,
            clock=lambda: mature + timedelta(seconds=1),
        ).to_dict()
        == final
    )
    assert verify(d, journal, final["head_pin"]) == verify(d, journal, final["head_pin"])
    assert not final["pit_qualified"] and not final["performance_approved"]
    assert sha(Path(d["operational"]["root"]) / DB) == before


def test_missing_future_input_and_no_fixture_promotion(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, at, _ = shadow_fixture
    early = record(
        d,
        tmp_path / "early.sqlite3",
        GENESIS,
        tmp_path / "e-i",
        tmp_path / "e-p",
        clock=lambda: at - timedelta(days=2),
    ).to_dict()
    assert early["recorded"] == 0 and early["blocked"] == 5
    missing = json.loads(json.dumps(d))
    missing["operational"]["evidence"] = missing["operational"]["evidence"][1:]
    r = record(
        missing,
        tmp_path / "missing.sqlite3",
        GENESIS,
        tmp_path / "m-i",
        tmp_path / "m-p",
        clock=lambda: at,
    ).to_dict()
    assert r["recorded"] == 0
    promoted = {**d, "source_kind": "local_provider_capture"}
    with pytest.raises(ValueError, match="fixture_is_not_real"):
        record(
            promoted,
            tmp_path / "bad.sqlite3",
            GENESIS,
            tmp_path / "b-i",
            tmp_path / "b-p",
            clock=lambda: at,
        )


def test_replay_correction_never_rewrites_original(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, _, mature = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    i = tmp_path / "i"
    p = tmp_path / "p"
    r = record(
        {**d, "mode": "historical_replay"}, journal, GENESIS, i, p, clock=lambda: mature
    ).to_dict()
    original = events(journal, r["head_pin"])[0]["payload"]
    assert original["mode"] == "historical_replay" and original["actual_outcome"] is None
    c = correct(
        journal,
        r["head_pin"],
        original["prediction_id"],
        "operator_withdrawal",
        i,
        p,
        clock=lambda: mature,
    ).to_dict()
    assert status(journal, c["head_pin"]).to_dict()["withdrawn"] == 1
    assert events(journal, c["head_pin"])[0]["payload"] == original


def test_interruption_rollback_and_exact_intent_recovery(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, _ = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    i = tmp_path / "i"
    p = tmp_path / "p"
    commit = journal_owner._commit

    def stop(conn: sqlite3.Connection) -> None:
        raise OSError("before commit")

    monkeypatch.setattr(journal_owner, "_commit", stop)
    with pytest.raises(OSError):
        record(d, journal, GENESIS, i, p, clock=lambda: at)
    assert history(journal, GENESIS).to_dict()["total"] == 0

    def lost(conn: sqlite3.Connection) -> None:
        commit(conn)
        raise OSError("lost ack")

    monkeypatch.setattr(journal_owner, "_commit", lost)
    with pytest.raises(OSError):
        record(d, journal, GENESIS, i, p, clock=lambda: at)
    intent = next(i.glob("*.json"))
    value = FrozenJSON(intent.read_text()).to_dict()
    recovered = recover(d, journal, GENESIS, intent, p / (value["event_id"] + ".pin")).to_dict()
    monkeypatch.setattr(journal_owner, "_commit", commit)
    r = record(d, journal, recovered["head_pin"], i, p, clock=lambda: at).to_dict()
    assert status(journal, r["head_pin"]).to_dict()["prediction_count"] == 5


def test_wrong_model_head_and_late_candidate(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, at, mature = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    i = tmp_path / "i"
    p = tmp_path / "p"
    r = record(d, journal, GENESIS, i, p, clock=lambda: at).to_dict()
    with closing(sqlite3.connect(journal)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM signal_entries")
    with pytest.raises(ValueError):
        history(journal, "0" * 64)
    changed = json.loads(json.dumps(d))
    changed["model"]["pin"] = "0" * 64
    with pytest.raises(ValueError):
        record(changed, journal, r["head_pin"], i, p, clock=lambda: at)
    late = record(
        d,
        tmp_path / "late.sqlite3",
        GENESIS,
        tmp_path / "li",
        tmp_path / "lp",
        clock=lambda: mature,
    ).to_dict()
    assert late["recorded"] == 0


def test_final_test_window_rejected_before_source_read() -> None:
    from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange

    split = ExplicitResearchSplit(
        ResearchDateRange(date(2022, 1, 1), date(2023, 12, 31)),
        ResearchDateRange(date(2024, 1, 1), date(2024, 6, 30)),
        ResearchDateRange(date(2024, 7, 1), date(2025, 6, 30)),
    )
    days = {}
    day = date(2024, 6, 1)
    while day <= date(2024, 7, 15):
        days[day.isoformat()] = "session" if day.weekday() < 5 else "weekend"
        day += timedelta(days=1)
    with pytest.raises(ValueError, match="protected_final_test"):
        _horizon({"target_trading_date": "2024-07-01"}, {"access_split": split.to_dict()}, days)


def test_cli_no_credentials_and_wrong_request_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("dotenv forbidden")

    monkeypatch.setattr("donghak_stock_vision.cli.load_dotenv", deny)
    h = tmp_path / "head.pin"
    h.write_text(GENESIS)
    h.chmod(0o600)
    r = tmp_path / "request.json"
    r.write_text("{}")
    p = tmp_path / "request.pin"
    p.write_text("0" * 64)
    p.chmod(0o600)
    assert (
        main(
            [
                "shadow-prediction",
                "record",
                "--request",
                str(r),
                "--request-pin-file",
                str(p),
                "--journal",
                str(tmp_path / "journal.sqlite3"),
                "--head-pin-file",
                str(h),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "journal.sqlite3").exists()


def test_pinned_intent_can_resume_without_rewriting_time(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, _ = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    i = tmp_path / "i"
    p = tmp_path / "p"
    commit = journal_owner._commit

    def stop(conn: sqlite3.Connection) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(journal_owner, "_commit", stop)
    with pytest.raises(OSError):
        record(d, journal, GENESIS, i, p, clock=lambda: at)
    frozen = FrozenJSON(next(i.glob("*.json")).read_text()).to_dict()
    monkeypatch.setattr(journal_owner, "_commit", commit)
    result = record(d, journal, GENESIS, i, p, clock=lambda: at + timedelta(seconds=1)).to_dict()
    assert result["recorded"] == 5
    assert events(journal, result["head_pin"])[0] == frozen


def test_semantic_replay_rejects_forged_vectors_and_horizon(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    from donghak_stock_vision.storage.shadow_prediction import _model, _rows, _verify_prediction

    d, at, _ = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    result = record(d, journal, GENESIS, tmp_path / "i", tmp_path / "p", clock=lambda: at).to_dict()
    p = events(journal, result["head_pin"])[0]["payload"]
    model, info = _model(d)
    rows, proofs = _rows(d, p["inputs"]["bars"][0]["trading_date"], p["input_latest_trading_date"])
    forged = json.loads(json.dumps(p))
    forged["score"] = 0.999
    with pytest.raises(ValueError, match="score_mismatch"):
        _verify_prediction(forged, model, info, rows, proofs)
    forged = json.loads(json.dumps(p))
    forged["features"][0] += 1
    with pytest.raises(ValueError, match="feature_mismatch"):
        _verify_prediction(forged, model, info, rows, proofs)


def test_clock_regression_does_not_record_prediction(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
) -> None:
    d, at, _ = shadow_fixture
    calls = iter((at, at, at, at - timedelta(seconds=1)))

    def clock() -> datetime:
        return next(calls, at)

    r = record(
        d, tmp_path / "journal.sqlite3", GENESIS, tmp_path / "i", tmp_path / "p", clock=clock
    ).to_dict()
    assert r["results"][0]["status"] == "blocked"
    assert r["results"][0]["reason"] == "shadow_execution_clock_order"
    first = events(tmp_path / "journal.sqlite3", r["head_pin"])[0]
    assert first["kind"] == "failure"


def test_read_only_pending_time_guard_and_existing_result_owner(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, at, mature = shadow_fixture
    journal, intents, pins = tmp_path / "journal.sqlite3", tmp_path / "intents", tmp_path / "pins"
    first = record(d, journal, GENESIS, intents, pins, clock=lambda: at).to_dict()
    head = first["head_pin"]
    before = (
        sha(journal),
        sha(Path(d["operational"]["root"]) / DB),
        sha(Path(d["model"]["database"])),
    )
    calls = []
    original = OperationalMarketStore._verify_batch

    def spy(self: OperationalMarketStore, batch: str, raw_id: int) -> FrozenJSON:
        result = original(self, batch, raw_id)
        calls.append(result.to_dict()["request"]["source_date"])
        return result

    monkeypatch.setattr(OperationalMarketStore, "_verify_batch", spy)
    pending = evaluation_readiness(d, journal, head, clock=lambda: at).to_dict()
    assert pending["pending"] == 5 and pending["resolve_time_eligible"] == 0
    assert all(day < d["target_trading_date"] for day in calls)
    assert all(r["reason"] == "horizon_not_elapsed" for r in pending["results"])
    calls.clear()
    elapsed = evaluation_readiness(d, journal, head, clock=lambda: mature).to_dict()
    assert elapsed["pending"] == 5 and elapsed["resolve_time_eligible"] == 5
    assert all(not r["pending_result_sources_checked"] for r in elapsed["results"])
    assert all(day < d["target_trading_date"] for day in calls)
    assert before == (
        sha(journal),
        sha(Path(d["operational"]["root"]) / DB),
        sha(Path(d["model"]["database"])),
    )
    final = resolve(d, journal, head, intents, pins, clock=lambda: mature).to_dict()
    finished = evaluation_readiness(d, journal, final["head_pin"], clock=lambda: mature).to_dict()
    assert finished["evaluated"] == 5 and finished["pending"] == 0
    assert all(r["terminal_outcome_replayed"] for r in finished["results"])
    assert finished["performance_metrics"] is None and not finished["performance_approved"]
    assert (
        resolve(d, journal, final["head_pin"], intents, pins, clock=lambda: mature).to_dict()
        == final
    )


def test_readiness_clock_and_head_rejection(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, at, _ = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    first = record(d, journal, GENESIS, tmp_path / "i", tmp_path / "p", clock=lambda: at).to_dict()
    head = first["head_pin"]
    before = sha(journal)
    with pytest.raises(ValueError, match="before_recorded_event"):
        evaluation_readiness(d, journal, head, clock=lambda: at - timedelta(seconds=1))
    times = iter([at + timedelta(seconds=2), at + timedelta(seconds=1)])
    with pytest.raises(ValueError, match="clock_order"):
        evaluation_readiness(d, journal, head, clock=lambda: next(times))
    with pytest.raises(ValueError):
        evaluation_readiness(d, journal, "0" * 64, clock=lambda: at)
    assert sha(journal) == before


@pytest.mark.parametrize("part", ["inputs", "outcome"])
def test_rehashed_read_ranges_rejected_before_any_source_replay(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    part: str,
) -> None:
    from donghak_stock_vision.data.learning import digest, timestamp
    from donghak_stock_vision.storage.shadow_prediction import _event, _model

    d, at, mature = shadow_fixture
    journal = tmp_path / "original.sqlite3"
    first = record(d, journal, GENESIS, tmp_path / "i", tmp_path / "p", clock=lambda: at).to_dict()
    head = first["head_pin"]
    if part == "outcome":
        head = resolve(
            d, journal, head, tmp_path / "i", tmp_path / "p", clock=lambda: mature
        ).to_dict()["head_pin"]
    source = events(journal, head)
    prediction = next(e for e in source if e["kind"] == "prediction")
    _, info = _model(d)
    sealed = date.fromisoformat(info["access_split"]["final_test"]["start"])
    forged = tmp_path / "forged.sqlite3"
    if part == "inputs":
        bad = json.loads(json.dumps(prediction))
        p = bad["payload"]
        for i, b in enumerate(p["inputs"]["bars"]):
            b["trading_date"] = (sealed + timedelta(days=i)).isoformat()
        p["input_latest_trading_date"] = p["inputs"]["bars"][-1]["trading_date"]
        p["input_source_pin"] = digest(p["inputs"])
        result = journal_owner.append_shadow_event(forged, FrozenJSON.freeze(bad), GENESIS)
        reason = "shadow_original_horizon_mismatch"
    else:
        original = next(
            e
            for e in source
            if e["kind"] == "outcome" and e["payload"]["prediction_id"] == prediction["event_id"]
        )
        payload = json.loads(json.dumps(original["payload"]))
        payload["future"]["bars"][0]["trading_date"] = sealed.isoformat()
        payload["future_source_pin"] = digest(payload["future"])
        previous = journal_owner.append_shadow_event(forged, FrozenJSON.freeze(prediction), GENESIS)
        result = journal_owner.append_shadow_event(
            forged,
            _event("outcome", payload, timestamp(original["recorded_at"])),
            previous.to_dict()["head_pin"],
        )
        reason = "shadow_original_outcome_horizon_mismatch"
    before = sha(journal)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("forged source range must not be read")

    monkeypatch.setattr("donghak_stock_vision.storage.shadow_prediction._rows", forbidden)
    with pytest.raises(ValueError, match=reason):
        verify(d, forged, result.to_dict()["head_pin"])
    assert sha(journal) == before


def test_evaluation_readiness_cli_uses_real_verifiers_and_no_credentials(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from functools import partial

    from donghak_stock_vision.data.learning import digest

    d, at, _ = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    first = record(d, journal, GENESIS, tmp_path / "i", tmp_path / "p", clock=lambda: at).to_dict()
    request, pin, head = tmp_path / "request.json", tmp_path / "request.pin", tmp_path / "head.pin"
    request.write_text(json.dumps(d))
    pin.write_text(digest(d))
    pin.chmod(0o600)
    head.write_text(first["head_pin"])
    head.chmod(0o600)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("credentials forbidden")

    monkeypatch.setattr("donghak_stock_vision.cli.load_dotenv", forbidden)
    # Inject only the deterministic fixture clock; all verification remains real.
    monkeypatch.setattr(
        "donghak_stock_vision.shadow_prediction_cli.evaluation_readiness",
        partial(evaluation_readiness, clock=lambda: at),
    )
    before = sha(journal)
    report = tmp_path / "readiness.json"
    args = [
        "shadow-prediction",
        "evaluation-readiness",
        "--request",
        str(request),
        "--request-pin-file",
        str(pin),
        "--journal",
        str(journal),
        "--head-pin-file",
        str(head),
        "--report",
        str(report),
    ]
    assert main(args) == 0
    result = json.loads(report.read_text())
    assert result["pending"] == 5 and result["resolve_time_eligible"] == 0
    assert sha(journal) == before
    assert json.loads(capsys.readouterr().out)["pending"] == 5
    args[-1] = str(tmp_path / "wrong-action.json")
    assert main(args + ["--explain-timing"]) == 2
    assert sha(journal) == before
