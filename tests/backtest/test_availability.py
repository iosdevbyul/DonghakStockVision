from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import ContractError, utc
from donghak_stock_vision.data.learning import FEATURES, digest, event_contract
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.strategy.adapter import ReadOnlyAnalysisStore
from tests.backtest.helpers import T, manifest
from tests.backtest.time_helpers import LATER
from tests.strategy.test_adapter_cli import create_analysis


class MemoryArtifacts(AnalysisStore):
    """Explicit fixture timestamps via public API; no SQL or production clock changes."""

    def __init__(
        self, records: dict[tuple[str, str], dict[str, Any]], times: dict[str, str]
    ) -> None:
        self.records = records
        self.times = times

    def get(self, kind: str, artifact_id: str) -> dict[str, Any]:
        return self.records[(kind, artifact_id)]

    def created_at(self, kind: str, artifact_id: str) -> datetime:
        self.get(kind, artifact_id)
        return utc(self.times[kind])


def fixture(
    mode: str = "historical_research", **times: str
) -> tuple[MemoryArtifacts, RunManifest, FrozenJSON, dict[str, str]]:
    origin = {
        "mode": mode,
        "data_origin": "synthetic",
        "usage_restriction": "synthetic_test_only",
        "anchor_policy": "received_at" if mode == "point_in_time" else "nominal_eod_1600",
        "session_basis": "verified_sessions",
    }
    snap: dict[str, Any] = {**origin, "cutoff": T, "rows": {}, "quality": None}
    sid = digest(snap)
    dataset = {
        "snapshot_id": sid,
        "split": {},
        "samples": [{"label": 1, "label_available_at": "2026-01-04T00:00:00Z"}],
    }
    did = digest(dataset)
    model = {
        **origin,
        **event_contract("verified_sessions"),
        "schema_version": 1,
        "features": list(FEATURES),
        "snapshot_id": sid,
        "dataset_id": did,
        "split_hash": digest({}),
        "directions": {d: {"parameters": None} for d in ("up", "down")},
    }
    mid = digest(model)
    actual = {
        "model": "2026-01-04T12:00:00Z",
        "signal": LATER,
        "snapshot": "2026-01-04T11:00:00Z",
        "dataset": "2026-01-04T11:30:00Z",
        **times,
    }
    signal = {
        **origin,
        **event_contract("verified_sessions"),
        "model_version": mid,
        "model_created_at": actual["model"],
        "snapshot_id": sid,
        "snapshot_as_of": T,
        "anchor_at": T,
        "ticker": "005930",
        "input_status": "insufficient_history",
        "context": None,
        "up_score": None,
        "down_score": None,
        "up_status": "context_mismatch",
        "down_status": "context_mismatch",
        "signal_state": "insufficient_history",
        "last_trading_date": None,
        "latest_collected_at": None,
        "quality_flags": ["synthetic_fixture"],
    }
    aid = digest(signal)
    ids = {"model_id": mid, "snapshot_id": sid, "analysis_id": aid}
    store = MemoryArtifacts(
        {
            ("model", mid): model,
            ("snapshot", sid): snap,
            ("dataset", did): dataset,
            ("signal", aid): signal,
        },
        actual,
    )
    m = manifest().to_dict()
    provenance = {
        "information_mode": mode,
        "data_origin": "synthetic",
        "usage_restriction": "synthetic_test_only",
    }
    plan = FrozenJSON.freeze(
        {
            **provenance,
            "releases": [],
            "analysis_releases": [{**ids, "sequence": 1, "available_at": T}],
        }
    )
    m.update(provenance)
    m["availability_assumption_id"] = plan.identifier if mode == "historical_research" else None
    for key, value in (("models", mid), ("analyses", aid), ("market_snapshots", sid)):
        m[key] = [{**provenance, "artifact_id": value, "version": "fixture", "content_hash": value}]
    for key in ("quality_manifest", "calendar", "universe"):
        m[key].update(provenance)
    return store, RunManifest.from_dict(m), plan, ids


def frozen(mode: str = "historical_research", **times: str) -> FrozenAnalysis:
    store, run, plan, ids = fixture(mode, **times)
    return FrozenAnalysis.capture(store, run, plan, **ids)


def test_anchor_is_not_actual_creation_and_historical_is_labelled() -> None:
    artifact = frozen(model="2026-02-01T00:00:00Z", signal="2026-02-02T00:00:00Z")
    view = artifact.view(VirtualClock.at(T, 1))
    assert view.to_dict()["status"] == "research_only"
    assert view.to_dict()["information_mode"] == "historical_research"
    assert utc(view.to_dict()["items"][0]["anchor_at"]) == utc(T)
    assert view.lineage.to_dict()["created_at"]["signal"].startswith("2026-02-02")
    assert view.to_dict()["usage_restriction"] == "synthetic_test_only"
    assert not view.to_dict()["executable"]


@pytest.mark.parametrize(
    "times,cutoff,reason",
    [
        ({"model": LATER}, LATER, "model_not_available_at_feature_cutoff"),
        ({"signal": LATER}, T, "analysis_artifact_not_available"),
        ({"snapshot": LATER}, T, "analysis_artifact_not_available"),
        ({"dataset": "2026-01-06T00:00:00Z"}, LATER, "dependency_artifact_not_available"),
        ({"model": "2026-01-03T00:00:00Z"}, LATER, "future_label_dependency"),
        ({}, LATER, "pit_selection_and_revision_evidence_unavailable"),
    ],
)
def test_pit_timestamp_and_unavailable_proof_blocking(
    times: dict[str, str], cutoff: str, reason: str
) -> None:
    view = frozen("point_in_time", **times).view(VirtualClock.at(cutoff, 1)).to_dict()
    assert view["status"] == "blocked" and view["reason"] == reason
    assert view["items"] == [] and view["information_mode"] == "point_in_time"
    assert view["usage_restriction"] == "synthetic_test_only"


def test_future_signal_content_cannot_change_unreleased_view() -> None:
    a = frozen()
    store, run, plan, ids = fixture()
    signal = store.get("signal", ids["analysis_id"])
    signal["future_labels"] = [0, 0, 0]
    replacement = digest(signal)
    store.records[("signal", replacement)] = signal
    m = run.to_dict()
    m["analyses"][0].update(artifact_id=replacement, content_hash=replacement)
    p = plan.to_dict()
    p["analysis_releases"][0]["analysis_id"] = replacement
    updated = FrozenJSON.freeze(p)
    m["availability_assumption_id"] = updated.identifier
    b = FrozenAnalysis.capture(
        store, RunManifest.from_dict(m), updated, **{**ids, "analysis_id": replacement}
    )
    cursor = VirtualClock.at(T, 0)
    assert a.view(cursor).identifier == b.view(cursor).identifier
    assert a.view(cursor).to_dict()["items"] == []
    assert "future_labels" not in str(b.view(VirtualClock.at(T, 1)).to_dict())


def test_wrong_snapshot_and_provenance_fail_without_inference() -> None:
    store, run, plan, ids = fixture()
    s = dict(store.get("snapshot", ids["snapshot_id"]))
    s["cutoff"] = LATER
    sid = digest(s)
    store.records[("snapshot", sid)] = s
    with pytest.raises(ContractError):
        FrozenAnalysis.capture(store, run, plan, **{**ids, "snapshot_id": sid})
    model = store.get("model", ids["model_id"])
    model["data_origin"] = "real"
    with pytest.raises(ValueError):
        FrozenAnalysis.capture(store, run, plan, **ids)


def test_real_phase2_public_api_capture_survives_db_deletion(tmp_path: Path) -> None:
    path, row = create_analysis(tmp_path)
    reader = ReadOnlyAnalysisStore(path)
    before = path.read_bytes()
    m = manifest().to_dict()
    start = "2020-06-01T00:00:00Z"
    m.update(start_at=start, end_at="2020-06-03T00:00:00Z")
    m["initial_account"].update(observed_at=start, received_at=start)
    ids = {
        "model_id": row["model_version"],
        "snapshot_id": row["snapshot_id"],
        "analysis_id": row["analysis_id"],
    }
    p = {
        "information_mode": "historical_research",
        "data_origin": "synthetic",
        "usage_restriction": "synthetic_test_only",
        "releases": [],
        "analysis_releases": [{**ids, "sequence": 1, "available_at": row["anchor_at"]}],
    }
    plan = FrozenJSON.freeze(p)
    m["availability_assumption_id"] = plan.identifier
    for key, value in (
        ("models", ids["model_id"]),
        ("market_snapshots", ids["snapshot_id"]),
        ("analyses", ids["analysis_id"]),
    ):
        m[key][0].update(artifact_id=value, content_hash=value)
    try:
        result = FrozenAnalysis.capture(reader, RunManifest.from_dict(m), plan, **ids)
        cursor = VirtualClock.at(row["anchor_at"], 1)
        original = result.view(cursor)
        assert result.bundle.to_dict()["created_at"]["signal"] == reader.created_at(
            "signal", ids["analysis_id"]
        ).isoformat(timespec="microseconds")
    finally:
        reader.close()
    assert path.read_bytes() == before
    path.unlink()
    (tmp_path / "market.db").unlink()
    replay = FrozenAnalysis(
        result.manifest, result.release_plan, FrozenJSON(result.bundle.payload_json)
    )
    assert replay.view(cursor) == original
    assert original.to_dict()["items"][0]["up_score"] == row["up_score"]
