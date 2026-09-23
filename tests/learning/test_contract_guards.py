from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from donghak_stock_vision.data.learning import AnalysisError, Minimums
from donghak_stock_vision.data.snapshot import capture, snapshot_bars
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.fit import fit_linear
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.service import TrainingService, load_model
from donghak_stock_vision.signals.dataset import (
    build_dataset,
    population,
    sample_weights,
    split_dataset,
)
from donghak_stock_vision.signals.service import SignalQueryService, SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, quality, seed


def test_json_predictions_match_sklearn(snapshot: tuple[str, dict[str, Any]]) -> None:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    samples, _ = build_dataset(snapshot[1])
    split = split_dataset(samples, "historical_research")
    for direction in ("up", "down"):
        train = population(split, direction, "train")
        holdout = population(split, direction, "test")
        parameters = fit_linear(train, 1.0)
        weights = np.asarray(sample_weights(train))
        with threadpool_limits(limits=1):
            scaler = StandardScaler().fit([s.values for s in train], sample_weight=weights)
            estimator = LogisticRegression(
                C=1.0, solver="lbfgs", max_iter=2000, class_weight="balanced", random_state=42
            ).fit(
                scaler.transform([s.values for s in train]),
                [s.label for s in train],
                sample_weight=weights,
            )
            expected = estimator.predict_proba(scaler.transform([s.values for s in holdout]))[:, 1]
        assert [score(parameters, s.values)[0] for s in holdout] == pytest.approx(
            expected.tolist(), abs=1e-10, rel=0
        )


def test_single_class_and_convergence_never_replace_model(
    trained: dict[str, Any], analysis: AnalysisStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression

    version = trained["model_version"]
    model = load_model(analysis, version)
    dataset = analysis.get("dataset", model["dataset_id"])
    changed = deepcopy(dataset)
    for row in changed["split"]["partitions"]["train"]:
        if row["direction"] == "up":
            row["label"] = 1
    result = TrainingService(analysis)._direction(changed, "up", Minimums.synthetic())
    assert result["parameters"] is None
    assert result["requirements"]["actual"]["train"]["negative"] == 0

    def no_convergence(*args: Any, **kwargs: Any) -> None:
        raise ConvergenceWarning("fixture")

    monkeypatch.setattr(LogisticRegression, "fit", no_convergence)
    result = TrainingService(analysis).train(model["snapshot_id"])
    assert result["model_version"] is None
    assert result["directions"]["up"]["evaluation_status"] == "fit_failed"
    assert load_model(analysis, version) == model
    assert not analysis.connection.execute("SELECT * FROM operational_registry").fetchall()


def test_verified_missing_session_and_revision_cutoff(store: SQLiteStore) -> None:
    source = bars(30, 1)
    manifest = quality(source)
    # Remove an interior date; the verified calendar must prevent bridging the missing bar.
    seed(store, source[:20] + source[21:])
    args = (store, ["000001"], source[0].trading_date, source[-1].trading_date, CUTOFF)
    snap = capture(*args, "historical_research", quality=manifest, synthetic=True)
    with pytest.raises(AnalysisError, match="missing_verified_session"):
        feature_vector(snapshot_bars(snap, "000001"), snap)
    # A revision received after cutoff cannot enter a newly captured PIT snapshot.
    revised = replace(source[22], close=source[22].open, collected_at=CUTOFF + timedelta(days=1))
    seed(store, [revised])
    pit = capture(*args, "point_in_time", quality=manifest, synthetic=True)
    assert source[22].trading_date.isoformat() in pit["unavailable_dates"]["000001"]
    assert not any(b.trading_date == source[22].trading_date for b in snapshot_bars(pit, "000001"))


def test_real_data_cannot_use_synthetic_minimums(store: SQLiteStore) -> None:
    source = [replace(b, provider="krx") for b in bars(30, 1)]
    # Populate via the public store boundary using a test-only Provider with matching metadata.
    from donghak_stock_vision.ingestion.pipeline import Pipeline
    from donghak_stock_vision.providers.fake import FakeProvider

    provider = FakeProvider(source)
    provider.name = "krx"
    result = Pipeline(provider, store).collect(
        ["000001"], source[0].trading_date, source[-1].trading_date
    )
    assert not result.failed
    with pytest.raises(AnalysisError, match="synthetic_mode_requires_fake_provider"):
        capture(
            store,
            ["000001"],
            source[0].trading_date,
            source[-1].trading_date,
            CUTOFF,
            "historical_research",
            acknowledge=True,
            synthetic=True,
        )


def test_research_anchor_range_and_conflict_diagnostic(
    trained: dict[str, Any],
    snapshot: tuple[str, dict[str, Any]],
    analysis: AnalysisStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = trained["model_version"]
    service = SignalService(analysis)
    first = service.research(version, snapshot[0], ["000001"], date(2020, 6, 1))[0]
    service.research(version, snapshot[0], ["000001"], date(2020, 6, 2))
    query = SignalQueryService(analysis)
    rows = query.get_research_analyses(
        version, snapshot[0], anchor_range=(date(2020, 6, 1), date(2020, 6, 2))
    )
    assert len(rows) == 2
    assert len(query.get_research_analyses(version, snapshot[0])) == 1
    conflicting = {**first, "up_score": 0.9, "down_score": 0.8}
    monkeypatch.setattr(query, "_latest", lambda *args, **kwargs: [conflicting])
    rows = query.get_point_in_time_analyses(version, datetime(2020, 6, 3, tzinfo=UTC))
    assert rows[0]["input_status"] == "contract_conflict"
    assert rows[0]["up_score"] is None and rows[0]["down_score"] is None


def test_validation_qualified_alone_cannot_register(
    trained: dict[str, Any], analysis: AnalysisStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import donghak_stock_vision.signals.service as module

    version = trained["model_version"]
    candidate = deepcopy(load_model(analysis, version))
    # Unit-test the registration policy independently; no real artifact is promoted.
    candidate.update(
        mode="point_in_time", data_origin="real", usage_restriction="pit_review_required"
    )
    candidate["directions"]["up"].update(
        evaluation_status="validation_qualified", test_status="evaluated"
    )
    monkeypatch.setattr(module, "load_model", lambda *args: candidate)
    query = SignalQueryService(analysis)
    assert not query._registered(version, "up", CUTOFF)
    with analysis.connection:
        analysis.connection.execute(
            "INSERT INTO operational_registry VALUES(?,?,?,?)",
            (version, "up", CUTOFF.isoformat(), json.dumps({"approved": True})),
        )
    assert not query._registered(version, "up", CUTOFF)
    with pytest.raises(AnalysisError, match="registration_forbidden"):
        query.get_latest_up_signals(CUTOFF, version)


def test_adjustment_unknown_and_mixed_are_blocked(
    store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = bars(30, 1)
    seed(store, [replace(b, adjustment="adjusted") for b in source])
    args = (store, ["000001"], source[0].trading_date, source[-1].trading_date, CUTOFF)
    with pytest.raises(AnalysisError, match="unverified_adjustment"):
        capture(*args, "historical_research", acknowledge=True, synthetic=True)
    manifest = quality(source)
    manifest["coverage"]["000001"]["adjustment"] = "adjusted"
    manifest["coverage"]["000001"]["adjustment_method"] = "synthetic_fixture_v1"
    valid = capture(*args, "historical_research", quality=manifest, synthetic=True)
    assert valid["profiles"][0][2] == "synthetic_fixture_v1"
    # Phase 1 already rejects basis-changing updates. Simulate another Store adapter instead.
    mixed = [replace(b, adjustment="adjusted") for b in source]
    mixed[15] = replace(source[15], adjustment="unknown")
    monkeypatch.setattr(store, "read", lambda *args: mixed)
    with pytest.raises(AnalysisError, match="quality_unverified"):
        capture(*args, "historical_research", quality=manifest, synthetic=True)
    mixed[15] = source[15]
    with pytest.raises(AnalysisError, match="adjustment_mismatch"):
        capture(*args, "historical_research", quality=manifest, synthetic=True)
