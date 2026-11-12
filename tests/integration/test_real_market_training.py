"""Independent offline KRX-schema fixtures, never genuine exchange observations.

Real-data minimums and all receipt/parser/training/loading checks run unchanged.
No fixture or model is installed in an operator's operational environment.
"""

import hashlib
import json
import random
import socket
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from donghak_stock_vision.cli import main
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.inference import LinearInference
from donghak_stock_vision.models.service import EvaluationService, load_model
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.historical_readiness import _OfflineKRXParser
from donghak_stock_vision.storage.real_market_training import (
    RealMarketTrainingRequest,
    prepare_real_market_training,
    train_real_market,
    validate_real_daily_model,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(root: Path, count: int) -> RealMarketTrainingRequest:
    database = root / "research.sqlite3"
    days: list[date] = []
    day = date(2020, 1, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    cutoff = datetime.combine(days[-1] + timedelta(days=3), time(8), UTC)
    tickers = tuple(f"{i:06d}" for i in range(1, 6))
    prices = dict.fromkeys(tickers, 10000)
    randoms = {t: random.Random(321 + int(t)) for t in tickers}
    with closing(SQLiteStore(database)) as store:
        for day in days:
            rows = []
            for ticker in tickers:
                opening = prices[ticker]
                price = max(500, round(opening * (1 + randoms[ticker].gauss(0, 0.014))))
                volume = randoms[ticker].randint(10000, 100000)
                rows.append(
                    {
                        "ISU_CD": ticker,
                        "BAS_DD": day.strftime("%Y%m%d"),
                        "MKT_NM": "KOSPI",
                        "TDD_OPNPRC": str(opening),
                        "TDD_HGPRC": str(max(opening, price) + 10),
                        "TDD_LWPRC": str(min(opening, price) - 10),
                        "TDD_CLSPRC": str(price),
                        "ACC_TRDVOL": str(volume),
                        "ACC_TRDVAL": str(volume * price),
                    }
                )
                prices[ticker] = price
            page = RawPage(
                json.dumps({"OutBlock_1": rows}).encode(), cutoff - timedelta(days=1), day
            )
            raw_id = store.archive(
                "offline-schema-fixture-no-provider-request", "krx", "@krx_market:KOSPI", page
            )
            store.save(_OfflineKRXParser("KOSPI").parse_market(page), [raw_id])
        # Deliberately invalid raw payload in the OUTER holdout. Any accidental
        # replay/unsealing of it would fail, so normal training proves confinement.
        protected_day = days[-1] + timedelta(days=1)
        raw_id = store.archive(
            "protected-poison-must-never-be-read",
            "krx",
            "000001",
            RawPage(b"not valid KRX JSON", cutoff - timedelta(days=1), protected_day),
        )
        store.save(
            [
                DailyBar(
                    "000001",
                    protected_day,
                    10000,
                    10100,
                    9900,
                    10000,
                    100,
                    1000000,
                    "krx",
                    "KOSPI",
                    "unadjusted",
                    cutoff - timedelta(days=1),
                )
            ],
            [raw_id],
        )
    train_end = int(count * 0.625) - 1
    val_end = int(count * 0.8125) - 1
    fit = ExplicitResearchSplit(
        ResearchDateRange(days[0], days[train_end]),
        ResearchDateRange(days[train_end + 1], days[val_end]),
        ResearchDateRange(days[val_end + 1], days[-1]),
    )
    access = ExplicitResearchSplit(
        fit.training,
        ResearchDateRange(fit.validation.start, fit.final_test.end),
        ResearchDateRange(protected_day, protected_day + timedelta(days=365)),
    )
    return RealMarketTrainingRequest(
        database, sha(database), access, access.identifier, fit, fit.identifier, tickers, cutoff
    )


@pytest.fixture(scope="module")
def full_source(tmp_path_factory: pytest.TempPathFactory) -> RealMarketTrainingRequest:
    return source(tmp_path_factory.mktemp("krx-schema-only"), 820)


@pytest.fixture(scope="module")
def trained(
    full_source: RealMarketTrainingRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, dict[str, Any]]:
    output = tmp_path_factory.mktemp("trained-schema-fixture") / "artifacts.sqlite3"
    before = sha(full_source.database)
    result = train_real_market(full_source, output).to_dict()
    assert result["training"]["status"] == "trained", result
    assert sha(full_source.database) == before
    return output, result


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


def test_real_contract_training_loading_and_separate_evaluation(
    trained: tuple[Path, dict[str, Any]],
    full_source: RealMarketTrainingRequest,
) -> None:
    from donghak_stock_vision.storage.analysis import AnalysisStore

    output, result = trained
    assert not result["evaluation_recorded"] and not result["operational_eligible"]
    with closing(ReadOnlyAnalysisStore(output)) as store:
        model = load_model(store, result["training"]["model_version"], "historical_research")
        assert model["data_origin"] == "real"  # Contract fixture, NOT a genuine KRX observation.
        assert model["usage_restriction"] == "research_only"
        assert not store.all("evaluation")
        snapshot = store.get("snapshot", model["snapshot_id"])
        assert snapshot["real_market_source"]["protected_final_test_read"] is False
        series = [DailyBar.from_dict(b) for b in snapshot["rows"]["000001"]]
        names = tuple(model["features"])
        values = feature_vector(series[-11:], snapshot)
        direction = "up" if values[2] < 0 else "down"
        from donghak_stock_vision.data.learning import canonical

        assert (
            0
            <= LinearInference(canonical(model["directions"][direction]["parameters"])).score(
                names, values
            )
            <= 1
        )
    with closing(AnalysisStore(output)) as store:
        evaluated = EvaluationService(store).evaluate(
            result["training"]["model_version"], "historical_research"
        )
        assert evaluated["model_version"] == result["training"]["model_version"]
        assert len(store.all("evaluation")) == 1
    assert sha(full_source.database) == full_source.database_pin


def test_minimums_missing_development_split_and_no_output_on_block(tmp_path: Path) -> None:
    request = source(tmp_path, 30)
    output = tmp_path / "not-created.sqlite3"
    report = train_real_market(request, output).to_dict()
    assert report["status"] == "BLOCKED" and not report["can_train"]
    assert report["directions"]["up"]["required"]["dates"] == 504
    assert not output.exists()
    without_fit = replace(request, fit_split=None, fit_split_pin=None)
    _, unconfigured = prepare_real_market_training(without_fit)
    assert unconfigured.to_dict()["blockers"] == ["development_split_not_supplied"]


def test_holdout_and_wrong_split_pins_reject_before_source_io(
    full_source: RealMarketTrainingRequest,
) -> None:
    bad = replace(
        full_source,
        database=Path("/absent/never-open"),
        fit_split=full_source.access_split,
        fit_split_pin=full_source.access_split_pin,
    )
    with pytest.raises(ValueError, match="inside_original_training_and_tuning"):
        prepare_real_market_training(bad)
    with pytest.raises(ValueError, match="split_pin"):
        prepare_real_market_training(replace(bad, fit_split_pin="0" * 64))


@pytest.mark.parametrize("damage", ["raw", "receipt", "ownership", "bar", "missing_receipt"])
def test_rehashed_source_damage_rejected(tmp_path: Path, damage: str) -> None:
    request = source(tmp_path, 30)
    with closing(sqlite3.connect(request.database)) as conn:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall():
            conn.execute(f'DROP TRIGGER "{name}"')
        if damage == "raw":
            conn.execute("UPDATE raw_pages SET body=? WHERE id=1", (b"{}",))
        elif damage == "receipt":
            conn.execute("UPDATE raw_capture_receipts SET raw_sha256=? WHERE raw_id=1", ("0" * 64,))
        elif damage == "ownership":
            conn.execute(
                "UPDATE bar_sources SET raw_id=(SELECT MAX(id) FROM raw_pages) "
                "WHERE ticker='000001' AND raw_id=1"
            )
        elif damage == "bar":
            conn.execute(
                "UPDATE bars SET payload=json_set(payload,'$.close',10001) "
                "WHERE ticker='000001' AND trading_date=(SELECT MIN(trading_date) FROM bars)"
            )
        else:
            conn.execute("DELETE FROM raw_capture_receipts WHERE raw_id=1")
        conn.commit()
    # Rehashing the container cannot repair original receipt/source relationships.
    request = replace(request, database_pin=sha(request.database))
    with pytest.raises(ValueError):
        prepare_real_market_training(request)


def test_daily_real_model_gate_and_no_legacy_promotion(
    trained: tuple[Path, dict[str, Any]],
    full_source: RealMarketTrainingRequest,
) -> None:
    with closing(ReadOnlyAnalysisStore(trained[0])) as store:
        model = load_model(store, trained[1]["training"]["model_version"], "historical_research")
        validate_real_daily_model(
            store, model, full_source.access_split.final_test.end + timedelta(days=1)
        )
        with pytest.raises(ValueError, match="protected_final_test"):
            validate_real_daily_model(store, model, full_source.access_split.final_test.start)
        with pytest.raises(ValueError, match="training_overlap"):
            validate_real_daily_model(store, model, full_source.end)
        # A version that has no linked frozen source artifact cannot become a daily model.
        with pytest.raises(ValueError):
            validate_real_daily_model(store, {**model, "snapshot_id": "0" * 64}, None)


@pytest.mark.parametrize("legacy", [False, True])
def test_cli_prepare_is_read_only_and_credentials_free(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    legacy: bool,
) -> None:
    request = source(tmp_path, 30)
    if legacy:
        with closing(sqlite3.connect(request.database)) as conn:
            conn.execute("DROP TABLE raw_capture_receipts")
            conn.commit()
        request = replace(
            request, database_pin=sha(request.database), source_contract="legacy_research_v1"
        )

    split = tmp_path / "access.json"
    split.write_text(json.dumps(request.access_split.to_dict()))
    before = sha(request.database)
    output = tmp_path / "uncreated.sqlite3"
    with patch("donghak_stock_vision.cli.load_dotenv", side_effect=AssertionError("credentials")):
        code = main(
            [
                "--db",
                str(request.database),
                "train",
                "--mode",
                "historical-research",
                "--analysis-db",
                str(output),
                "--tickers",
                *request.tickers,
                "--start",
                request.access_split.training.start.isoformat(),
                "--end",
                request.access_split.validation.end.isoformat(),
                "--snapshot-as-of",
                request.cutoff.isoformat(),
                "--acknowledge-research-limitations",
                "--real-market-source",
                "--market-sha256",
                request.database_pin,
                "--access-split",
                str(split),
                "--access-split-pin",
                request.access_split_pin,
                "--research-source-contract",
                request.source_contract,
                "--prepare-only",
            ]
        )
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["status"] == "BLOCKED"
    assert sha(request.database) == before and not output.exists()
    assert report["network_requests"] == 0 and report["performance_metrics"] is None


def test_explicit_legacy_schema_retains_missing_receipt_boundary(tmp_path: Path) -> None:
    request = source(tmp_path, 30)
    with closing(sqlite3.connect(request.database)) as conn:
        conn.execute("DROP TABLE raw_capture_receipts")
        conn.commit()
    request = replace(request, database_pin=sha(request.database))
    with pytest.raises(ValueError, match="archive_contract_mismatch"):
        prepare_real_market_training(request)
    legacy = replace(request, source_contract="legacy_research_v1")
    frozen, report = prepare_real_market_training(legacy)
    proof = frozen.to_dict()["real_market_source"]
    assert proof["local_originals_verified"] is True
    assert proof["original_receipts_verified"] is False
    assert proof["raw_source_custody_contract_compatible"] is False
    assert all(r["receipt_sha256"] is None for r in proof["sources"])
    assert report.to_dict()["can_train"] is False
    with closing(sqlite3.connect(request.database)) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='raw_capture_receipts'"
            ).fetchone()[0]
            == 0
        )


def test_future_inputs_and_unknown_contract_do_not_create_a_model(tmp_path: Path) -> None:
    request = source(tmp_path, 30)
    output = tmp_path / "never-created.sqlite3"
    future_inputs = replace(request, cutoff=request.cutoff - timedelta(days=2))
    result = train_real_market(future_inputs, output).to_dict()
    assert result["can_train"] is False and not output.exists()
    assert all(v["observations"] == 0 for v in result["inventory"].values())
    with pytest.raises(ValueError, match="unsupported_training_source_contract"):
        prepare_real_market_training(replace(request, source_contract="unknown_v999"))


@pytest.mark.parametrize("damage", ["missing", "request_pin"])
def test_generic_model_compatibility_does_not_promote_missing_or_forged_source(
    trained: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    damage: str,
) -> None:
    from donghak_stock_vision.storage.analysis import AnalysisStore

    with closing(ReadOnlyAnalysisStore(trained[0])) as original:
        model = load_model(original, trained[1]["training"]["model_version"], "historical_research")
        snapshot = original.get("snapshot", model["snapshot_id"])
        dataset = original.get("dataset", model["dataset_id"])
    if damage == "missing":
        snapshot.pop("real_market_source")
        expected = "verified_training_source_required"
    else:
        snapshot["real_market_source"]["request_pin"] = "0" * 64
        expected = "source_binding"
    with closing(AnalysisStore(tmp_path / "unlinked-model.sqlite3")) as store:
        model["snapshot_id"] = store.put("snapshot", snapshot)
        dataset["snapshot_id"] = model["snapshot_id"]
        model["dataset_id"] = store.put("dataset", dataset)
        version = store.put("model", model)
        loaded = load_model(store, version, "historical_research")
        with pytest.raises(ValueError, match=expected):
            validate_real_daily_model(store, loaded, None)


def test_declared_research_exclusion_precedes_raw_replay_without_repair(tmp_path: Path) -> None:
    from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy

    request = source(tmp_path, 30)
    with closing(sqlite3.connect(request.database)) as conn:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall():
            conn.execute(f'DROP TRIGGER "{name}"')
        excluded = date.fromisoformat(
            conn.execute("SELECT source_date FROM raw_pages WHERE id=4").fetchone()[0]
        )
        invalid_body = b"{}"
        fingerprint = hashlib.sha256(invalid_body).hexdigest()
        conn.execute("UPDATE raw_pages SET body=?,sha256=? WHERE id=4", (invalid_body, fingerprint))
        conn.execute("UPDATE raw_capture_receipts SET raw_sha256=? WHERE raw_id=4", (fingerprint,))
        conn.commit()
    request = replace(request, database_pin=sha(request.database))
    with pytest.raises(ValueError, match="original_raw_replay_failed"):
        prepare_real_market_training(request)
    allowed_exclusion = replace(request, research_calendar=ResearchCalendarPolicy((excluded,)))
    frozen, report = prepare_real_market_training(allowed_exclusion)
    assert all(
        excluded.isoformat() not in [bar["trading_date"] for bar in rows]
        for rows in frozen.to_dict()["rows"].values()
    )
    assert report.to_dict()["source"]["applied_research_exclusions"] == [excluded.isoformat()]
    assert sha(request.database) == request.database_pin


def test_legacy_contract_flag_cannot_silently_use_original_train_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "not-created.sqlite3"
    code = main(
        [
            "--db",
            str(tmp_path / "absent-source.sqlite3"),
            "train",
            "--mode",
            "historical-research",
            "--analysis-db",
            str(output),
            "--tickers",
            "005930",
            "--start",
            "2022-01-03",
            "--end",
            "2023-12-28",
            "--research-source-contract",
            "legacy_research_v1",
        ]
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "real_market_source_mode_required"
    assert not output.exists()


def test_operational_sqlite_is_not_reinterpreted_as_research_source(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.operational_market import (
        DB,
        OperationalMarketStore,
        StorageLimits,
    )

    request = source(tmp_path, 30)
    root = tmp_path / "operational"
    OperationalMarketStore.initialize(root, StorageLimits(min_free_bytes=0))
    database = root / DB
    before = sha(database)
    with pytest.raises(ValueError, match="research_source_schema_required"):
        prepare_real_market_training(replace(request, database=database, database_pin=before))
    assert sha(database) == before
