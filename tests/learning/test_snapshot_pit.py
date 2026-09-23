import json
import sqlite3
from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from donghak_stock_vision.data.learning import AnalysisError
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import EvaluationService, TrainingService
from donghak_stock_vision.signals.dataset import build_dataset
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, quality, seed


def test_snapshot_freezes_prices_features_labels_evaluation_and_inference(
    snapshot: tuple[str, dict[str, Any]],
    trained: dict[str, Any],
    store: SQLiteStore,
    analysis: AnalysisStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, snap = snapshot
    version = trained["model_version"]
    dataset_before = build_dataset(analysis.get("snapshot", sid))
    evaluator = EvaluationService(analysis)
    evaluation_before = evaluator.evaluate(version, "historical_research")
    service = SignalService(analysis)
    inference_before = service.research(version, sid, ["000001"], date(2020, 6, 1))[0]
    # Change a real latest-store payload after capture, including features AND future labels.
    with store.connection:
        for day in ("2020-05-28", "2020-06-02"):
            row = store.connection.execute(
                "SELECT payload FROM bars WHERE ticker=? AND trading_date=?", ("000001", day)
            ).fetchone()
            payload = json.loads(row[0])
            payload["volume"] *= 100
            payload["high"] *= 2
            store.connection.execute(
                "UPDATE bars SET payload=? WHERE ticker=? AND trading_date=?",
                (json.dumps(payload), "000001", day),
            )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay must never read mutable market storage")

    monkeypatch.setattr(store, "read", forbidden)
    assert analysis.get("snapshot", sid) == snap
    assert build_dataset(analysis.get("snapshot", sid)) == dataset_before
    assert evaluator.evaluate(version, "historical_research") == evaluation_before
    inference_after = service.research(version, sid, ["000001"], date(2020, 6, 1))[0]
    assert {k: v for k, v in inference_after.items() if k != "analyzed_at"} == {
        k: v for k, v in inference_before.items() if k != "analyzed_at"
    }
    assert TrainingService(analysis).train(sid)["model_version"] == version


def test_sql_immutability_and_detached_loaded_object(
    snapshot: tuple[str, dict[str, Any]], analysis: AnalysisStore
) -> None:
    sid, original = snapshot
    read = analysis.get("snapshot", sid)
    read["rows"]["000001"][0]["close"] = -1
    assert analysis.get("snapshot", sid) == original
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        analysis.connection.execute("UPDATE analysis_artifacts SET payload='{}' WHERE id=?", (sid,))
    analysis.connection.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        analysis.connection.execute("DELETE FROM analysis_artifacts WHERE id=?", (sid,))
    analysis.connection.rollback()


def test_bulk_history_research_accepts_pit_rejects(
    store: SQLiteStore, analysis: AnalysisStore
) -> None:
    source = bars()
    seed(store, source)
    end = max(b.trading_date for b in source)
    args = (store, ["000001", "000002"], source[0].trading_date, end, CUTOFF)
    research = capture(*args, "historical_research", acknowledge=True, synthetic=True)
    pit = capture(*args, "point_in_time", quality=quality(source), synthetic=True)
    assert len(build_dataset(research)[0]) > 100
    pit_samples, exclusions = build_dataset(pit)
    assert all(s.label is None for s in pit_samples)
    assert exclusions["insufficient_point_in_time_data"] > 100
    report = TrainingService(analysis).train(analysis.put("snapshot", pit))
    assert report["model_version"] is None


def test_quality_and_synthetic_gates(store: SQLiteStore) -> None:
    source = bars(30, 1)
    seed(store, source)
    args = (store, ["000001"], source[0].trading_date, source[-1].trading_date, CUTOFF)
    with pytest.raises(AnalysisError, match="acknowledgement"):
        capture(*args, "historical_research", synthetic=True)
    with pytest.raises(AnalysisError, match="quality_unverified"):
        capture(*args, "point_in_time", synthetic=True)
    with pytest.raises(AnalysisError, match="synthetic_mode_required"):
        capture(*args, "historical_research", acknowledge=True)


def test_pit_late_revision_removes_feature_not_rewrites_past(store: SQLiteStore) -> None:
    source = bars(80, 1, bulk=False)
    seed(store, source)
    snap = capture(
        store,
        ["000001"],
        source[0].trading_date,
        source[-1].trading_date,
        CUTOFF,
        "point_in_time",
        quality=quality(source),
        synthetic=True,
    )
    samples, _ = build_dataset(snap)
    day = source[30].trading_date
    assert any(s.trading_date == day for s in samples)
    changed = deepcopy(snap)
    changed["rows"]["000001"][29]["collected_at"] = CUTOFF.isoformat()
    revised, _ = build_dataset(changed)
    assert not any(s.trading_date == day for s in revised)
    before = [
        (s.trading_date, s.values) for s in samples if s.trading_date < source[29].trading_date
    ]
    after = [
        (s.trading_date, s.values) for s in revised if s.trading_date < source[29].trading_date
    ]
    assert before == after
