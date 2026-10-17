"""Reference transport must reproduce the entire existing validated trading result."""

import copy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.service import freeze_request, run_backtest_with_report
from donghak_stock_vision.backtest.shared_evidence import (
    EvidenceBuilder,
    SharedEvidence,
    expand,
    pack,
)
from donghak_stock_vision.backtest.trading_report import report_backtest
from tests.backtest.test_hgb_trading import hgb_runner
from tests.backtest.test_performance import valuation_policy
from tests.backtest.test_public_inference import scored_runner


@pytest.fixture(scope="module", params=["linear", "hgb"])
def pair(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[FrozenJSON, FrozenJSON]:
    root = tmp_path_factory.mktemp(f"shared-{request.param}")
    runner = (scored_runner if request.param == "linear" else hgb_runner)(root)
    legacy = freeze_request(runner, valuation_policy(), shared=False)
    optimized = freeze_request(runner, valuation_policy())
    assert optimized == pack(legacy)
    original = report_backtest(runner, valuation_policy())
    d = legacy.to_dict()
    args = dict(
        start=d["config"]["start_at"],
        end=d["config"]["end_at"],
        initial_cash="1000000",
        tickers=["005930"],
    )
    assert run_backtest_with_report(legacy, **args) == original
    assert run_backtest_with_report(optimized, **args) == original
    return legacy, optimized


def test_entire_evidence_and_result_equivalence(pair: tuple[FrozenJSON, FrozenJSON]) -> None:
    legacy, optimized = pair
    assert expand(optimized) == legacy
    assert optimized.identifier != legacy.identifier
    assert optimized.legacy_identifier == legacy.identifier
    assert optimized.historical_identifier == legacy.historical_identifier
    assert FrozenJSON(optimized.payload_json) == optimized
    assert pack(expand(optimized)) == optimized
    for artifact in optimized.to_dict()["evidence_table"]["artifacts"]:
        if artifact["kind"] in {"hgb_lock", "hgb_qualification"}:
            key = "lock_id" if artifact["kind"] == "hgb_lock" else "report_id"
            assert artifact["source_id"] == artifact["payload"][key]
            assert artifact["artifact_id"] != artifact["source_id"]
    tape = FrozenTape.from_json(FrozenJSON.freeze(legacy.to_dict()["tape"]).payload_json)
    analyses = optimized.shared_evidence.analyses(tape.manifest, tape.release_plan)
    assert [a.bundle.to_dict() for a in analyses] == legacy.to_dict()["analyses"]
    assert [a.bundle.identifier for a in analyses] == [
        FrozenJSON.freeze(a).identifier for a in legacy.to_dict()["analyses"]
    ]
    for original, reconstructed in zip(legacy.to_dict()["analyses"], analyses, strict=True):
        assert reconstructed.bundle.to_dict()["created_at"] == original["created_at"]
        assert reconstructed.bundle.to_dict()["signal"] == original["signal"]


def test_deduplication_and_detached_immutable_views(pair: tuple[FrozenJSON, FrozenJSON]) -> None:
    legacy, optimized = pair
    source = legacy.to_dict()["analyses"][0]
    builder = EvidenceBuilder()
    builder.add(source)
    builder.add(source)
    assert builder.records[0] == builder.records[1]
    before = len(builder.artifacts)
    altered = copy.deepcopy(source)
    altered["created_at"]["signal"] = "2099-01-01T00:00:00Z"
    builder.add(altered)
    assert builder.records[2] != builder.records[0]
    assert len(builder.artifacts) == before + 1
    evidence = SharedEvidence(builder.table(optimized.shared_evidence.run_id), builder.records)
    ref = evidence.references[0]
    value = evidence.resolve(ref)
    value["signal"]["ticker"] = "999999"
    assert evidence.resolve(ref) == source
    with pytest.raises(FrozenInstanceError):
        evidence.run_id = "0" * 64  # type: ignore[misc]
    assert len(optimized.payload_json.encode()) < len(legacy.payload_json.encode())


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "source",
        "payload",
        "digest",
        "kind",
        "duplicate",
        "conflicting_duplicate",
        "consumer",
        "reference",
        "run",
    ],
)
def test_tampered_references_fail_closed(pair: tuple[FrozenJSON, FrozenJSON], damage: str) -> None:
    legacy, optimized = pair
    value = optimized.to_dict()
    table = value["evidence_table"]
    artifacts = table["artifacts"]
    if damage == "missing":
        artifacts.pop()
    elif damage == "source":
        artifacts[0]["source_id"] = "0" * 64
    elif damage == "payload":
        artifacts[0]["payload"]["tampered"] = True
    elif damage == "digest":
        artifacts[0]["artifact_id"] = "0" * 64
        artifacts[0]["source_id"] = "0" * 64
    elif damage == "kind":
        artifacts[0]["kind"] = "signal" if artifacts[0]["kind"] != "signal" else "quality"
    elif damage in {"duplicate", "conflicting_duplicate"}:
        duplicate = copy.deepcopy(artifacts[0])
        if damage == "conflicting_duplicate":
            duplicate["payload"]["tampered"] = True
        artifacts.append(duplicate)
    elif damage == "consumer":
        artifacts[0]["analysis_ids"] = []
    elif damage == "reference":
        value["analyses"][0]["kind"] = "dataset"
    elif damage == "run":
        table["run_id"] = "0" * 64
    d = legacy.to_dict()
    with pytest.raises(ValueError):
        run_backtest_with_report(
            FrozenJSON.freeze(value),
            start=d["config"]["start_at"],
            end=d["config"]["end_at"],
            initial_cash="1000000",
            tickers=["005930"],
        )


def test_historical_service_legacy_and_shared_exact_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.historical import HistoricalBacktestInput
    from donghak_stock_vision.backtest.historical_service import run_historical_backtest
    from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
    from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
    from tests.backtest.test_historical_service import setup_request

    request = setup_request(tmp_path, monkeypatch)
    market = ReadOnlyMarketStore(tmp_path / "market.db")
    source = ReadOnlyAnalysisStore(tmp_path / "analysis.db")
    output = AnalysisStore(tmp_path / "isolated-output.db")
    before = {p: (tmp_path / p).read_bytes() for p in ("market.db", "analysis.db")}
    legacy = run_historical_backtest(
        request, market, source, run_output=output, shared_evidence=False
    ).document.to_dict()
    optimized = run_historical_backtest(
        request, market, source, run_output=output
    ).document.to_dict()
    assert optimized["historical_input"]["version"] == 2
    assert (
        expand(FrozenJSON.freeze(optimized["historical_input"])).to_dict()
        == legacy["historical_input"]
    )
    for field in (
        "run",
        "run_hash",
        "performance",
        "performance_hash",
        "detailed_report",
        "detailed_report_hash",
        "report",
        "historical_input_hash",
    ):
        assert optimized[field] == legacy[field], field
    a = HistoricalBacktestInput(FrozenJSON.freeze(optimized["historical_input"]))
    assert (
        a.identifier
        == HistoricalBacktestInput(FrozenJSON.freeze(legacy["historical_input"])).identifier
    )
    assert a.document.identifier != FrozenJSON.freeze(legacy["historical_input"]).identifier
    assert before == {p: (tmp_path / p).read_bytes() for p in before}
    market.close()
    source.close()
    output.close()


def test_explicit_versions_and_empty_observation_input() -> None:
    from tests.backtest.test_position_history import round_trip

    runner = round_trip()
    for shared in (False, True):
        value = freeze_request(runner, valuation_policy(), shared=shared).to_dict()
        value["version"] = 2
        value.pop("evidence_table", None)
        with pytest.raises(ValueError, match="shared_evidence_required"):
            run_backtest_with_report(
                FrozenJSON.freeze(value),
                start=value["config"]["start_at"],
                end=value["config"]["end_at"],
                initial_cash=runner.initial.to_dict()["total_cash_krw"],
                tickers=["005930"],
            )
    builder = EvidenceBuilder()
    assert SharedEvidence(builder.table(runner.tape.manifest.identifier), []).references == ()


def test_cross_run_direct_analysis_rejected(pair: tuple[FrozenJSON, FrozenJSON]) -> None:
    from donghak_stock_vision.backtest.availability import FrozenAnalysis
    from donghak_stock_vision.backtest.contracts import RunManifest

    legacy, optimized = pair
    tape = FrozenTape.from_json(FrozenJSON.freeze(legacy.to_dict()["tape"]).payload_json)
    analysis = optimized.shared_evidence.analyses(tape.manifest, tape.release_plan)[0]
    altered = tape.manifest.to_dict()
    altered["initial_account"]["available_cash_krw"] = "999999"
    with pytest.raises(ValueError, match="shared_run_mismatch"):
        FrozenAnalysis(RunManifest.from_dict(altered), tape.release_plan, analysis.bundle)


@pytest.mark.parametrize("count", [0, 101])
def test_benchmark_is_explicitly_bounded(tmp_path: Path, count: int) -> None:
    from tests.backtest.shared_evidence_benchmark import benchmark

    with pytest.raises(ValueError, match="bounded_benchmark_required"):
        benchmark(tmp_path / "must-not-exist", (count,))
    assert not (tmp_path / "must-not-exist").exists()
