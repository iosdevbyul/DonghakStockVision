"""Synthetic end-to-end model -> frozen evaluation -> original outcome replay."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.models.service import EvaluationService, TrainingService
from donghak_stock_vision.signals.dataset import build_dataset, split_dataset
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.prediction_outcomes import prediction_outcomes


@pytest.fixture
def frozen(analysis: AnalysisStore, trained: dict[str, Any]) -> dict[str, Any]:
    evaluation = EvaluationService(analysis).evaluate(
        trained["model_version"], "historical_research"
    )
    path = Path(analysis.connection.execute("PRAGMA database_list").fetchone()[2])
    return {
        "path": path,
        "evaluation_id": evaluation["evaluation_id"],
        "model_version": evaluation["model_version"],
        "snapshot_id": evaluation["snapshot_id"],
        "direction": "up",
    }


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_replayed_predictions_match_original_labels_and_metrics(
    frozen: dict[str, Any], analysis: AnalysisStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = fingerprint(frozen["path"])
    evaluation = analysis.get("evaluation", frozen["evaluation_id"])

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("replay must not train or write")

    monkeypatch.setattr(AnalysisStore, "put", forbidden)
    monkeypatch.setattr(AnalysisStore, "put_many", forbidden)
    monkeypatch.setattr("donghak_stock_vision.models.service.fit_linear", forbidden)
    result = prediction_outcomes(**frozen, limit=250)
    cases = [row for row in result["cases"] if row["status"] == "evaluated"]
    original = evaluation["directions"]["up"]["validation"]
    assert result["metrics"] == {
        key: value for key, value in original.items() if key not in {"by_ticker", "by_date_count"}
    }
    assert len(cases) == result["evaluatedCases"] == original["n"]
    assert sum(row["actualLabel"] for row in cases) == original["positive"]
    assert sum(row["correct"] for row in cases) == sum(
        original["confusion_matrix"][key] for key in ("tp", "tn")
    )
    assert result["dataOrigin"] == "synthetic"
    assert result["usageRestriction"] == "synthetic_test_only"
    assert not result["forwardValidated"] and not result["pitQualified"]
    assert not result["executionAuthorized"]
    assert all(row["predictionRecordedAt"] is None for row in result["cases"])
    assert sum(result["statusCounts"].values()) == result["totalCases"]
    assert result["statusCounts"]["purged_at_split_boundary"] > 0
    assert result["statusCounts"]["context_mismatch"] > 0
    for row in result["cases"]:
        if row["status"] != "evaluated":
            assert row["score"] is None and row["correct"] is None
            assert row["actualLabel"] is None
    assert fingerprint(frozen["path"]) == before


def test_deterministic_pages_and_report_identity(frozen: dict[str, Any]) -> None:
    entire = prediction_outcomes(**frozen, limit=250)
    first = prediction_outcomes(**frozen, limit=3)
    second = prediction_outcomes(**frozen, offset=first["nextOffset"], limit=3)
    assert first == prediction_outcomes(**frozen, limit=3)
    assert first["cases"] + second["cases"] == entire["cases"][:6]
    assert first["totalCases"] == second["totalCases"] == entire["totalCases"]
    assert first["evaluatedCases"] == entire["evaluatedCases"]
    assert prediction_outcomes(**frozen, offset=10_000)["cases"] == []
    assert entire["contentFingerprint"] == digest(
        {k: v for k, v in entire.items() if k != "contentFingerprint"}
    )
    assert all(row["anchorAt"].endswith("+00:00") for row in entire["cases"])


def test_explicit_validation_gap_is_not_added_to_the_evaluation_cohort(
    snapshot: tuple[str, dict[str, Any]], analysis: AnalysisStore
) -> None:
    samples, _ = build_dataset(snapshot[1])
    automatic = split_dataset(samples, "historical_research")
    days = sorted({sample.trading_date for sample in samples})
    start = datetime.fromisoformat(automatic["validation_start"]).date()
    end = datetime.fromisoformat(automatic["test_start"]).date()
    config = ExplicitResearchSplit(
        ResearchDateRange(days[0], days[days.index(start) - 1]),
        ResearchDateRange(start, days[days.index(end) - 6]),
        ResearchDateRange(end, days[-1]),
    )
    trained = TrainingService(analysis).train(snapshot[0], split_configuration=config)
    assert trained["status"] == "trained"
    evaluation = EvaluationService(analysis).evaluate(
        trained["model_version"], "historical_research"
    )
    path = Path(analysis.connection.execute("PRAGMA database_list").fetchone()[2])
    result = prediction_outcomes(
        path,
        evaluation_id=evaluation["evaluation_id"],
        model_version=trained["model_version"],
        snapshot_id=snapshot[0],
        direction="up",
        limit=250,
    )
    assert result["totalCases"] == sum(
        config.validation.start <= sample.trading_date <= config.validation.end
        for sample in samples
    )
    assert all(
        config.validation.start.isoformat()
        <= row["tradingDate"]
        <= config.validation.end.isoformat()
        for row in result["cases"]
    )


@pytest.mark.parametrize("name", ["model_version", "snapshot_id"])
def test_wrong_original_owner_is_rejected(frozen: dict[str, Any], name: str) -> None:
    with pytest.raises(ValueError, match="reference mismatch"):
        prediction_outcomes(**{**frozen, name: "0" * 64})


@pytest.mark.parametrize(
    "field,value", [("mode", "point_in_time"), ("operational_status", "registered")]
)
def test_nonresearch_evaluation_is_blocked_before_source_replay(
    frozen: dict[str, Any],
    analysis: AnalysisStore,
    field: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = analysis.get("evaluation", frozen["evaluation_id"])
    original[field] = value
    identifier = analysis.put("evaluation", original)

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("ineligible source must not be replayed")

    monkeypatch.setattr(EvaluationService, "evaluate", forbidden)
    with pytest.raises(ValueError, match="historical research evaluation required"):
        prediction_outcomes(**{**frozen, "evaluation_id": identifier})


@pytest.mark.parametrize(
    "options",
    [{"limit": 0}, {"limit": 251}, {"offset": -1}, {"direction": "BUY"}, {"max_source_bars": 0}],
)
def test_input_bounds_before_opening_missing_source(
    tmp_path: Path, options: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        prediction_outcomes(
            tmp_path / "missing.db",
            **{
                "evaluation_id": "0" * 64,
                "model_version": "0" * 64,
                "snapshot_id": "0" * 64,
                "direction": "up",
                **options,
            },
        )
    assert not (tmp_path / "missing.db").exists()


def test_source_budget_before_reconstruction(
    frozen: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("source over budget must not be reconstructed")

    monkeypatch.setattr(EvaluationService, "evaluate", forbidden)
    with pytest.raises(ValueError, match="budget exceeded"):
        prediction_outcomes(**frozen, max_source_bars=1)


def test_read_only_evaluation_has_original_identity_without_audit_writes(
    frozen: dict[str, Any], analysis: AnalysisStore
) -> None:
    from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore

    before = fingerprint(frozen["path"])
    store = ReadOnlyAnalysisStore(frozen["path"])
    try:
        replay = EvaluationService(store).evaluate(
            frozen["model_version"], "historical_research", persist=False
        )
    finally:
        store.close()
    assert replay["evaluation_id"] == frozen["evaluation_id"]
    assert {k: v for k, v in replay.items() if k != "evaluation_id"} == analysis.get(
        "evaluation", frozen["evaluation_id"]
    )
    assert fingerprint(frozen["path"]) == before


def test_rehashed_evaluation_forgery_is_rejected(
    frozen: dict[str, Any], analysis: AnalysisStore
) -> None:
    forged = analysis.get("evaluation", frozen["evaluation_id"])
    forged["directions"]["up"]["validation"]["confusion_matrix"]["tp"] += 1
    identifier = analysis.put("evaluation", forged)
    with pytest.raises(ValueError, match="reproduction mismatch"):
        prediction_outcomes(**{**frozen, "evaluation_id": identifier})


def test_rehashed_stored_prediction_forgery_is_rejected(
    frozen: dict[str, Any], analysis: AnalysisStore
) -> None:
    model = analysis.get("model", frozen["model_version"])
    model["directions"]["up"]["validation_scores"][0] = 0.123456789
    version = analysis.put("model", model)
    # Legacy aggregate replay does not validate the retained per-case vector.
    evaluation = EvaluationService(analysis).evaluate(version, "historical_research")
    with pytest.raises(ValueError, match="stored prediction scores"):
        prediction_outcomes(
            **{**frozen, "model_version": version, "evaluation_id": evaluation["evaluation_id"]}
        )


def test_corrupt_snapshot_checksum_is_rejected(
    frozen: dict[str, Any], analysis: AnalysisStore
) -> None:
    analysis.connection.execute("DROP TRIGGER immutable_artifact_update")
    with analysis.connection:
        analysis.connection.execute(
            "UPDATE analysis_artifacts SET payload='{}' WHERE kind='snapshot' AND id=?",
            (frozen["snapshot_id"],),
        )
    with pytest.raises(ValueError, match="invalid_snapshot"):
        prediction_outcomes(**frozen)


def test_rehashed_dataset_labels_do_not_replace_price_reconstruction(
    frozen: dict[str, Any], analysis: AnalysisStore
) -> None:
    model = analysis.get("model", frozen["model_version"])
    dataset = analysis.get("dataset", model["dataset_id"])
    row = next(row for row in dataset["samples"] if row["label"] is not None)
    row["label"] = 1 - row["label"]
    model["dataset_id"] = analysis.put("dataset", dataset)
    version = analysis.put("model", model)
    evaluation = analysis.get("evaluation", frozen["evaluation_id"])
    evaluation["model_version"] = version
    identifier = analysis.put("evaluation", evaluation)
    with pytest.raises(ValueError, match="dataset_reproduction_mismatch"):
        prediction_outcomes(**{**frozen, "model_version": version, "evaluation_id": identifier})


def test_unavailable_model_never_generates_zero_predictions(
    frozen: dict[str, Any], analysis: AnalysisStore
) -> None:
    model = analysis.get("model", frozen["model_version"])
    model["directions"]["up"]["parameters"] = None
    version = analysis.put("model", model)
    evaluation = EvaluationService(analysis).evaluate(version, "historical_research")
    result = prediction_outcomes(
        **{**frozen, "model_version": version, "evaluation_id": evaluation["evaluation_id"]},
        limit=250,
    )
    assert result["metrics"] is None and result["evaluatedCases"] == 0
    assert result["statusCounts"]["model_unavailable"] > 0
    assert all(row["score"] is None and row["correct"] is None for row in result["cases"])


def test_ambiguous_outcomes_remain_unavailable(
    analysis: AnalysisStore, snapshot: tuple[str, dict[str, Any]]
) -> None:
    altered = deepcopy(snapshot[1])
    for bars in altered["rows"].values():
        for row in bars[160:170]:
            row["high"] = max(row["high"], int(row["open"] * 1.10))
            row["low"] = min(row["low"], int(row["open"] * 0.90))
    identifier = analysis.put("snapshot", altered)
    trained = TrainingService(analysis).train(identifier)
    assert trained["status"] == "trained"
    frozen = EvaluationService(analysis).evaluate(trained["model_version"], "historical_research")
    path = Path(analysis.connection.execute("PRAGMA database_list").fetchone()[2])
    result = prediction_outcomes(
        path,
        evaluation_id=frozen["evaluation_id"],
        model_version=trained["model_version"],
        snapshot_id=identifier,
        direction="up",
        limit=250,
    )
    ambiguous = [row for row in result["cases"] if row["status"] == "unavailable:ambiguous"]
    assert ambiguous
    assert all(row["actualLabel"] is None and row["score"] is None for row in ambiguous)
    assert result["totalCases"] > result["evaluatedCases"]


def test_cli_json_read_only_and_missing_database(frozen: dict[str, Any], tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "phase6_prediction_outcomes.py"
    before = fingerprint(frozen["path"])
    args = [
        sys.executable,
        str(script),
        "--analysis-db",
        str(frozen["path"]),
        "--evaluation-id",
        frozen["evaluation_id"],
        "--model-version",
        frozen["model_version"],
        "--snapshot-id",
        frozen["snapshot_id"],
        "--direction",
        "up",
        "--limit",
        "3",
    ]
    run = subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["schemaVersion"] == "phase6_prediction_outcomes_v1"
    assert len(result["cases"]) == 3
    assert fingerprint(frozen["path"]) == before
    args[3] = str(tmp_path / "missing.db")
    missing = subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)
    assert missing.returncode == 2 and not (tmp_path / "missing.db").exists()
