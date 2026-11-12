"""Complete mock HTTP -> original archive -> SQLite -> existing research model.

Every source/receipt/batch/model check is real; no private operational inputs.
"""

import json
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_forward_capture import Clock
from tests.integration.test_krx_forward_authorization import KEY
from tests.integration.test_krx_scheduler import config as config
from tests.integration.test_market_collection import row
from tests.learning.helpers import CUTOFF, bars, seed

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import FullMarketScope
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import TrainingService
from donghak_stock_vision.storage import daily_research as daily
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.forward_capture import DB as CAPTURE_DB
from donghak_stock_vision.storage.forward_capture import run_capture
from donghak_stock_vision.storage.krx_backlog import register_completion
from donghak_stock_vision.storage.krx_recurring import POLICY
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig, _daily_plan
from donghak_stock_vision.storage.operational_capture import ingest_capture
from donghak_stock_vision.storage.operational_market import DB as MARKET_DB
from donghak_stock_vision.storage.sqlite import SQLiteStore

DAY = date(2025, 7, 21)
AS_OF = datetime(2025, 7, 22, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("real socket forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("daily-model")
    path = root / "models.sqlite3"
    with closing(SQLiteStore(root / "fake-market.sqlite3")) as market:
        source = bars()
        seed(market, source)
        frozen = capture(
            market,
            ["000001", "000002"],
            source[0].trading_date,
            max(b.trading_date for b in source),
            CUTOFF,
            "historical_research",
            acknowledge=True,
            synthetic=True,
        )
    with closing(AnalysisStore(path)) as store:
        result = TrainingService(store).train(store.put("snapshot", frozen))
    assert result["status"] == "trained"
    return path, result["model_version"]


@pytest.fixture
def collected(config: SchedulerConfig) -> SchedulerConfig:
    root = config.path("policy_root")
    doc = FrozenJSON((root / "recurring-policy.json").read_text()).to_dict()
    days = tuple(date(2025, 7, d) for d in (7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 21))
    scope = FullMarketScope(
        datetime(2025, 7, 5, tzinfo=UTC), datetime(2025, 7, 31, tzinfo=UTC), days
    )
    doc.update(full_market_scope=scope.to_dict(), valid_until=scope.valid_until.isoformat())
    owner = FrozenJSON.freeze(doc)
    (root / "recurring-policy.json").write_text(owner.payload_json)
    config = SchedulerConfig(
        FrozenJSON.freeze(config.document.to_dict() | {"policy_pin": owner.identifier})
    )
    for index, day in enumerate(days):
        plan = _daily_plan(doc, day)
        at = datetime.combine(day + timedelta(days=1), time(8), UTC)
        clock = Clock(at)
        price = 100 + index * 2 + index % 3
        payload = {
            "OutBlock_1": [
                row(
                    ticker,
                    day,
                    TDD_OPNPRC=str(price),
                    TDD_HGPRC=str(price + 10),
                    TDD_LWPRC=str(price - 10),
                    TDD_CLSPRC=str(price),
                )
                for ticker in ("000001", "000002")
            ]
        }

        def monotonic(clock: Clock = clock) -> float:
            return clock.elapsed

        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request, payload=payload: httpx.Response(200, json=payload)
            ),
            trust_env=False,
        ) as client:
            original = run_capture(
                config.path("capture_root"),
                plan,
                "daily-fixture",
                client,
                api_key=KEY,
                allow_network=True,
                authorization=config.reference,
                authorization_policy=POLICY,
                operator_confirmed=True,
                clock=clock.now,
                monotonic=monotonic,
                sleep=clock.sleep,
            )
        batch = ingest_capture(
            config.path("operational_root"),
            config.path("capture_root"),
            plan,
            plan.document.identifier,
            "daily-fixture",
            original.identifier,
            authorization=config.reference,
            authorization_policy=POLICY,
        )
        register_completion(
            config,
            plan,
            plan.document.identifier,
            "daily-fixture",
            original.identifier,
            batch.to_dict()["batch_pin"],
            at=at,
        )
    return config


def execute(
    config: SchedulerConfig, model: tuple[Path, str], output: Path, **kwargs: Any
) -> dict[str, Any]:
    return daily.run_daily(
        config,
        output,
        model[0],
        model[1],
        DAY,
        kwargs.pop("tickers", ("000001", "000002")),
        kwargs.pop("as_of", AS_OF),
        allow_synthetic_fixture=kwargs.pop("allow_synthetic_fixture", True),
        **kwargs,
    ).to_dict()


def unchanged_sources(config: SchedulerConfig, model: tuple[Path, str]) -> dict[str, bytes]:
    paths = [
        config.path("capture_root") / CAPTURE_DB,
        config.path("operational_root") / MARKET_DB,
        model[0],
    ]
    return {str(p): p.read_bytes() for p in paths}


def test_collection_to_actual_model_score_idempotence_and_read_only_status(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
) -> None:
    before = unchanged_sources(collected, model)
    output = tmp_path / "daily.sqlite3"
    with patch(
        "donghak_stock_vision.storage.forward_signal.generate_signal",
        side_effect=AssertionError("prediction forbidden"),
    ):
        result = execute(collected, model, output)
        assert result["analysis_status"] == "completed"
        assert result["completed_tickers"] == 2
        assert all(r["research_score"] is not None for r in result["results"].values())
        saved = output.read_bytes()
        assert execute(collected, model, output) == result
        assert output.read_bytes() == saved
        assert daily.daily_status(output, DAY, AS_OF).to_dict() == result
    assert unchanged_sources(collected, model) == before
    assert not result["future_prediction_recording_available"]
    assert not result["pit_qualified"] and not result["performance_approved"]
    assert not result["execution_authorized"] and result["performance_metrics"] is None
    assert result["usage_restriction"] == "synthetic_test_only"
    assert result["last_successful_analysis_day"] == DAY.isoformat()
    assert result["network_requests"] == 0


@pytest.mark.parametrize(
    "case,reason",
    [
        ("short", "insufficient_history"),
        ("missing_ticker", "missing_anchor_bar"),
        ("stale", "stale_data"),
    ],
)
def test_unusable_inputs_block_without_scores(
    collected: SchedulerConfig, model: tuple[Path, str], tmp_path: Path, case: str, reason: str
) -> None:
    kwargs: dict[str, Any] = {}
    if case == "short":
        kwargs["lookback_days"] = 5
    if case == "missing_ticker":
        kwargs["tickers"] = ("999999",)
    if case == "stale":
        kwargs["as_of"] = AS_OF + timedelta(days=8)
    result = execute(collected, model, tmp_path / "daily.sqlite3", **kwargs)
    assert result["completed_tickers"] == 0 and result["blocked_tickers"] > 0
    assert all(
        r["reason"] == reason and r["research_score"] is None for r in result["results"].values()
    )
    assert result["last_successful_analysis_day"] is None


@pytest.mark.parametrize("interrupted", [False, True])
def test_safe_resume_preserves_completed_work(
    collected: SchedulerConfig, model: tuple[Path, str], tmp_path: Path, interrupted: bool
) -> None:
    output = tmp_path / "daily.sqlite3"
    original = daily._analyze
    calls = []

    def fail_second(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(args[1][0].ticker)
        if len(calls) == 2:
            if interrupted:
                raise KeyboardInterrupt
            raise RuntimeError("private error must not escape")
        return original(*args, **kwargs)

    with patch.object(daily, "_analyze", side_effect=fail_second):
        if interrupted:
            with pytest.raises(KeyboardInterrupt):
                execute(collected, model, output)
        else:
            failed = execute(collected, model, output)
            assert failed["failed_tickers"] == 1
    retained = daily.daily_status(output, DAY, AS_OF).to_dict()
    assert retained["completed_tickers"] == 1 and retained["analysis_status"] == "incomplete"
    first = retained["results"]["000001"]
    with patch.object(daily, "_analyze", wraps=original) as analyze:
        done = execute(collected, model, output)
        assert analyze.call_count == 1
    assert done["completed_tickers"] == 2 and done["results"]["000001"] == first
    assert done["results"]["000002"]["attempt"] == (1 if interrupted else 2)


@pytest.mark.parametrize(
    "damage", ["archive", "batch_pin", "missing_completion", "missing_session"]
)
def test_original_evidence_damage_fails_closed(
    collected: SchedulerConfig, model: tuple[Path, str], tmp_path: Path, damage: str
) -> None:
    registry = collected.path("runner_root") / "verified-completions.json"
    if damage == "archive":
        with closing(sqlite3.connect(collected.path("capture_root") / CAPTURE_DB)) as conn:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'"):
                conn.execute(f'DROP TRIGGER "{name}"')
            conn.execute("UPDATE raw_pages SET body=?", (b"{}",))
            conn.commit()
    else:
        doc = json.loads(registry.read_text())["document"]
        if damage == "batch_pin":
            doc["records"][-1]["batch_pin"] = "0" * 64
        elif damage == "missing_completion":
            doc["records"].pop()
        else:
            doc["records"].pop(3)
        registry.write_text(
            FrozenJSON.freeze(
                {"document": doc, "pin": FrozenJSON.freeze(doc).identifier}
            ).payload_json
        )
    output = tmp_path / "daily.sqlite3"
    if damage == "missing_session":
        result = execute(collected, model, output)
        assert all(
            r["reason"] == "missing_permitted_observation" for r in result["results"].values()
        )
    else:
        with pytest.raises(ValueError):
            execute(collected, model, output)
        assert not output.exists()


def test_conflict_origin_acknowledgement_and_output_ownership(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
) -> None:
    output = tmp_path / "daily.sqlite3"
    with pytest.raises(ValueError, match="explicit_synthetic"):
        execute(collected, model, output, allow_synthetic_fixture=False)
    execute(collected, model, output)
    before = output.read_bytes()
    with pytest.raises(ValueError, match="conflicting_frozen"):
        execute(collected, model, output, tickers=("000001",))
    assert output.read_bytes() == before
    for path in (model[0], collected.path("operational_root") / MARKET_DB):
        with pytest.raises(ValueError, match="separate"):
            execute(collected, model, path)


def test_cli_status_never_loads_credentials_and_uses_same_report(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "daily.sqlite3"
    execute(collected, model, output)
    with patch(
        "donghak_stock_vision.cli.load_dotenv", side_effect=AssertionError("credential access")
    ):
        assert (
            main(["daily-research", "status", "--output-db", str(output), "--day", DAY.isoformat()])
            == 0
        )
    result = json.loads(capsys.readouterr().out)
    assert result["completed_tickers"] == 2


def test_post_collection_hook_and_existing_api_projection(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient
    from tests.api.test_krx_scheduler_diagnostics import reports

    from donghak_stock_vision.api import create_app

    summary = FrozenJSON.freeze(
        {"version": "krx_scheduler_run_v1", "day": DAY.isoformat(), "outcome": "succeeded"}
    )
    output = tmp_path / "daily.sqlite3"
    wrapped = daily.after_collection(
        collected,
        summary,
        output,
        *model,
        ("000001", "000002"),
        AS_OF,
        allow_synthetic_fixture=True,
    ).to_dict()
    assert wrapped["collection"] == summary.to_dict()
    assert wrapped["research"][0]["completed_tickers"] == 2
    client = TestClient(
        create_app(
            krx_diagnostics_reports=reports,
            daily_research_report=lambda: daily.daily_status(output, DAY, AS_OF).to_dict(),
        )
    )
    response = client.get("/api/v1/operations/krx-scheduler/diagnostics")
    assert response.status_code == 200
    assert response.json()["dailyResearch"]["completed_tickers"] == 2
    assert response.json()["dailyResearch"]["future_prediction_recording_available"] is False
    # Wrong model never erases successful collection or authorizes a retry request.
    failure = daily.after_collection(
        collected,
        summary,
        tmp_path / "no-model.sqlite3",
        model[0],
        "0" * 64,
        ("000001",),
        AS_OF,
        allow_synthetic_fixture=True,
    ).to_dict()
    assert failure["collection"]["outcome"] == "succeeded"
    assert failure["research"][0]["analysis_status"] == "blocked"


@pytest.mark.parametrize("case", ["policy", "registry_version", "model_bytes", "hardlink"])
def test_substitution_and_unsupported_owners_rejected(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
    case: str,
) -> None:
    output = tmp_path / "daily.sqlite3"
    if case in {"policy", "registry_version"}:
        registry = collected.path("runner_root") / "verified-completions.json"
        doc = json.loads(registry.read_text())["document"]
        if case == "policy":
            doc["records"][-1]["authorization"]["grant_pin"] = "0" * 64
        else:
            doc["version"] = "unsupported_v999"
        registry.write_text(
            FrozenJSON.freeze(
                {"document": doc, "pin": FrozenJSON.freeze(doc).identifier}
            ).payload_json
        )
    elif case == "hardlink":
        output.hardlink_to(model[0])
    else:
        damaged = tmp_path / "damaged-model.sqlite3"
        damaged.write_bytes(model[0].read_bytes())
        with closing(sqlite3.connect(damaged)) as conn:
            conn.execute("DROP TRIGGER immutable_artifact_update")
            conn.execute("UPDATE analysis_artifacts SET payload='{}' WHERE kind='model'")
            conn.commit()
        model = damaged, model[1]
    with pytest.raises(ValueError):
        execute(collected, model, output)
    if case != "hardlink":
        assert not output.exists()


def test_cli_post_hook_options_fail_before_collection(
    collected: SchedulerConfig,
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.json"
    path.write_text(collected.document.payload_json)
    path.chmod(0o600)
    with patch(
        "donghak_stock_vision.krx_scheduler_cli.run_once",
        side_effect=AssertionError("collection called"),
    ):
        assert (
            main(
                [
                    "krx-scheduler",
                    "run-once",
                    "--config",
                    str(path),
                    "--config-pin",
                    collected.document.identifier,
                    "--research-output-db",
                    str(tmp_path / "daily.sqlite3"),
                ]
            )
            == 2
        )


def test_cli_run_is_offline_and_does_not_consult_key(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from donghak_stock_vision.data.learning import timestamp

    path = tmp_path / "scheduler.json"
    path.write_text(collected.document.payload_json)
    path.chmod(0o600)
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    with patch("donghak_stock_vision.cli.load_dotenv", side_effect=AssertionError("credentials")):
        code = main(
            [
                "daily-research",
                "run",
                "--config",
                str(path),
                "--config-pin",
                collected.document.identifier,
                "--model-db",
                str(model[0]),
                "--model-version",
                model[1],
                "--output-db",
                str(tmp_path / "daily.sqlite3"),
                "--day",
                DAY.isoformat(),
                "--tickers",
                "000001",
                "--allow-synthetic-fixture",
            ]
        )
    result = json.loads(capsys.readouterr().out)
    assert result["network_requests"] == 0
    # Real invocation clock, no backdated operational checks or stale scoring.
    if (timestamp(datetime.now(UTC)).date() - DAY).days > 7:
        assert code == 1 and result["results"]["000001"]["reason"] == "stale_data"
    else:
        assert code == 0


def test_real_source_never_uses_synthetic_model(model: tuple[Path, str]) -> None:
    from donghak_stock_vision.models.service import load_model
    from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore

    with closing(ReadOnlyAnalysisStore(model[0])) as store:
        artifact = load_model(store, model[1], "historical_research")
    # Unit check of the scorer's real-data boundary; not relabelled capture evidence.
    history = bars(11, 1, bulk=False)
    at = history[-1].collected_at + timedelta(days=1)
    source = {
        "day": history[-1].trading_date.isoformat(),
        "data_origin": "real",
        "expected_observation_dates": [b.trading_date.isoformat() for b in history],
    }
    with pytest.raises(ValueError, match="synthetic_model_on_real_data_forbidden"):
        daily._analyze(artifact, history, source, at)


def test_interrupted_result_transaction_rolls_back_and_replays(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
) -> None:
    output = tmp_path / "daily.sqlite3"
    original = daily._analyze
    calls = 0

    def interrupt_second(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    with patch.object(daily, "_analyze", side_effect=interrupt_second):
        with pytest.raises(KeyboardInterrupt):
            execute(collected, model, output)
    with closing(sqlite3.connect(output)) as conn:
        conn.execute(
            "CREATE TRIGGER interrupted_audit BEFORE INSERT ON analysis_executions "
            "WHEN NEW.kind='daily_research_result' BEGIN "
            "SELECT RAISE(ABORT, 'simulated interrupted transaction'); END"
        )
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="interrupted transaction"):
        execute(collected, model, output)
    retained = daily.daily_status(output, DAY, AS_OF).to_dict()
    assert retained["completed_tickers"] == 1 and retained["pending_tickers"] == 1
    with closing(sqlite3.connect(output)) as conn:
        conn.execute("DROP TRIGGER interrupted_audit")
        conn.commit()
    assert execute(collected, model, output)["completed_tickers"] == 2


def test_modified_research_result_is_rejected_not_silently_repaired(
    collected: SchedulerConfig,
    model: tuple[Path, str],
    tmp_path: Path,
) -> None:
    output = tmp_path / "daily.sqlite3"
    execute(collected, model, output)
    with closing(sqlite3.connect(output)) as conn:
        conn.execute("DROP TRIGGER immutable_artifact_update")
        conn.execute(
            "UPDATE analysis_artifacts SET payload='{}' WHERE kind='daily_research_result'"
        )
        conn.commit()
    with pytest.raises(ValueError, match="invalid_daily_research_result"):
        daily.daily_status(output, DAY, AS_OF)
    with pytest.raises(ValueError, match="invalid_daily_research_result"):
        execute(collected, model, output)
