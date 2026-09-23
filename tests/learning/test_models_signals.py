from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from donghak_stock_vision.data.learning import AnalysisError
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.service import EvaluationService, TrainingService, load_model
from donghak_stock_vision.signals.service import SignalQueryService, SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, quality, seed


def test_model_roundtrip_conditional_inference_and_registration_gate(
    trained: dict[str, Any],
    snapshot: tuple[str, dict[str, Any]],
    analysis: AnalysisStore,
) -> None:
    version = trained["model_version"]
    model = load_model(analysis, version, "historical_research")
    assert model["usage_restriction"] == "synthetic_test_only"
    assert model["horizon_sessions"] == 5 and model["min_barrier"] == 0.02
    assert model["volatility_multiplier"] == 2
    assert "최초 도달" in model["event_description"]
    service = SignalService(analysis)
    rows = service.research(version, snapshot[0], ["000001", "000002"], date(2020, 6, 1))
    for row in rows:
        assert row["input_status"] == "ok"
        up = row["context"] == "prior_decline"
        applicable, outside = ("up", "down") if up else ("down", "up")
        assert row[f"{applicable}_score"] is not None
        assert row[f"{outside}_score"] is None
        assert row[f"{outside}_status"] == "context_mismatch"
        assert outside not in row["reasons"]
    query = SignalQueryService(analysis)
    assert len(query.get_research_analyses(version, snapshot[0])) == 2
    assert query.get_latest_up_signals(datetime.now(UTC))["reason"] == "no_registered_model"
    # Even artificially recording a registry row can never promote a synthetic/research artifact.
    with analysis.connection:
        analysis.connection.execute(
            "INSERT INTO operational_registry VALUES(?,?,?,?)",
            (version, "up", CUTOFF.isoformat(), "{}"),
        )
    with pytest.raises(AnalysisError, match="registration_forbidden"):
        query.get_latest_up_signals(datetime.now(UTC), version)
    assert load_model(analysis, version)["operational_status"] == "unregistered"


def test_mode_mismatch_missing_model_and_tampering(
    trained: dict[str, Any],
    analysis: AnalysisStore,
) -> None:
    version = trained["model_version"]
    with pytest.raises(AnalysisError, match="mode_mismatch"):
        EvaluationService(analysis).evaluate(version, "point_in_time")
    with pytest.raises(AnalysisError, match="missing_model"):
        load_model(analysis, "missing")
    model = load_model(analysis, version)
    params = deepcopy(model["directions"]["up"]["parameters"])
    params["scale"][0] = 0
    with pytest.raises(AnalysisError, match="invalid_model"):
        score(params, (1.0,) * 8)
    # Checksum protects even against corruption bypassing the normal immutable API.
    analysis.connection.execute("DROP TRIGGER immutable_artifact_update")
    with analysis.connection:
        analysis.connection.execute(
            "UPDATE analysis_artifacts SET payload='{}' WHERE id=?", (version,)
        )
    with pytest.raises(AnalysisError, match="invalid_model"):
        load_model(analysis, version)


def test_conditional_prior_and_test_not_used_in_selection(
    trained: dict[str, Any], analysis: AnalysisStore
) -> None:
    from donghak_stock_vision.data.learning import Minimums
    from donghak_stock_vision.signals.dataset import population

    model = load_model(analysis, trained["model_version"])
    dataset = analysis.get("dataset", model["dataset_id"])
    for direction in ("up", "down"):
        entry = model["directions"][direction]
        train = population(dataset["split"], direction, "train")
        assert entry["prior"] == sum(s.label == 1 for s in train) / len(train)
        assert entry["validation_baselines"]["prior"]["n"] == len(
            population(dataset["split"], direction, "validation")
        )
    changed = deepcopy(dataset)
    for row in changed["split"]["partitions"]["test"]:
        row["label"] = 1 - row["label"]
        row["values"] = [v * 10 for v in row["values"]]
    result = TrainingService(analysis)._direction(changed, "up", Minimums.synthetic())
    assert result["parameters"] == model["directions"]["up"]["parameters"]
    assert result["validation"] == model["directions"]["up"]["validation"]
    assert result["test"] != model["directions"]["up"]["test"]


def test_strict_pit_future_model_stale_and_unregistered(
    store: SQLiteStore,
    analysis: AnalysisStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = bars(240, 2, bulk=False)
    seed(store, source)
    manifest = quality(source)
    end = max(b.trading_date for b in source)
    snap = capture(
        store,
        ["000001", "000002"],
        source[0].trading_date,
        end,
        CUTOFF,
        "point_in_time",
        quality=manifest,
        synthetic=True,
    )
    result = TrainingService(analysis).train(analysis.put("snapshot", snap))
    assert result["model_version"], result
    version = result["model_version"]
    service = SignalService(analysis)
    assert (
        service.point_in_time(store, version, ["000001"], CUTOFF, manifest)[0]["input_status"]
        == "model_not_available_as_of"
    )
    original_created = analysis.created_at
    monkeypatch.setattr(
        analysis,
        "created_at",
        lambda kind, key: (
            datetime(2020, 1, 1, tzinfo=UTC) if kind == "model" else original_created(kind, key)
        ),
    )
    fresh_cutoff = max(b.collected_at for b in source)
    timely_manifest = deepcopy(manifest)
    timely_manifest["verified_at"] = fresh_cutoff.isoformat()
    fresh = service.point_in_time(store, version, ["000001"], fresh_cutoff, timely_manifest)[0]
    assert fresh["input_status"] == "ok", fresh
    assert (fresh["up_score"] is None) != (fresh["down_score"] is None)
    assert fresh["usage_restriction"] == "synthetic_test_only"
    # The manifest covers the requested cutoff even though the source has no recent data.
    extended = deepcopy(manifest)
    for entry in extended["coverage"].values():
        entry["end"] = "2024-01-31"
    stale = service.point_in_time(store, version, ["000001"], CUTOFF, extended)[0]
    assert stale["input_status"] == "stale_data" and stale["up_score"] is None
    query = SignalQueryService(analysis)
    assert query.get_latest_down_signals(CUTOFF)["items"] == []


def test_latest_ineligible_does_not_resurrect_old_signal(
    trained: dict[str, Any],
    snapshot: tuple[str, dict[str, Any]],
    analysis: AnalysisStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise read-side registry filtering with a stub, not an actual promotion of fixture models.
    version = trained["model_version"]
    service = SignalService(analysis)
    row = service.research(version, snapshot[0], ["000001"], date(2020, 6, 1))[0]
    original = {k: v for k, v in row.items() if k not in {"analysis_id", "analyzed_at"}}
    original.update(
        mode="point_in_time",
        data_as_of="2024-01-01T00:00:00+00:00",
        anchor_at="2024-01-01T00:00:00+00:00",
        last_trading_date="2023-12-29",
        up_score=0.9,
        down_score=None,
        signal_state="up",
    )
    analysis.put("signal", original)
    bad = {
        **original,
        "anchor_at": "2024-01-02T00:00:00+00:00",
        "data_as_of": "2024-01-02T00:00:00+00:00",
        "up_score": None,
        "input_status": "quality_unverified",
        "signal_state": "quality_unverified",
    }
    analysis.put("signal", bad)
    query = SignalQueryService(analysis)
    # Registry/mode gates are independently tested above; stub them to isolate latest-row behavior.
    monkeypatch.setattr(query, "_registered", lambda *args: True)
    import donghak_stock_vision.signals.service as module

    real_load = load_model
    monkeypatch.setattr(
        module, "load_model", lambda store, version, mode=None: real_load(store, version)
    )
    cutoff = datetime(2024, 1, 3, tzinfo=UTC)
    assert query.get_latest_up_signals(cutoff, version)["items"] == []
    diagnostic = query.get_point_in_time_analyses(version, cutoff)
    assert diagnostic[0]["input_status"] == "quality_unverified"
    # Delayed queries re-check freshness rather than trusting persisted input_status.
    assert (
        query.get_point_in_time_analyses(version, cutoff + timedelta(days=10))[0]["input_status"]
        == "stale_data"
    )


def test_flat_context_null(
    snapshot: tuple[str, dict[str, Any]], trained: dict[str, Any], analysis: AnalysisStore
) -> None:
    from donghak_stock_vision.data.snapshot import snapshot_bars

    snap = deepcopy(snapshot[1])
    bars_list = snapshot_bars(snap, "000001")
    index = 100
    row = snap["rows"]["000001"][index]
    row["close"] = bars_list[index - 5].close
    row["high"] = max(row["high"], row["close"])
    row["low"] = min(row["low"], row["close"])
    sid = analysis.put("snapshot", snap)
    version = TrainingService(analysis).train(sid)["model_version"]
    output = SignalService(analysis).research(
        version, sid, ["000001"], date.fromisoformat(row["trading_date"])
    )[0]
    assert output["signal_state"] == "no_context"
    assert output["up_score"] is None and output["down_score"] is None
