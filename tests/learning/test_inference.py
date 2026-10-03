"""No real checkpoint prediction, OOS evaluation or estimator fitting."""

import hashlib
import pickle
from dataclasses import replace
from importlib.metadata import version
from typing import Any

import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from donghak_stock_vision.data.learning import FEATURES, AnalysisError, canonical
from donghak_stock_vision.models.inference import (
    HGBArtifact,
    HGBInference,
    LinearInference,
    ResearchPolicy,
)


class StubEstimator:
    n_features_in_ = 8
    classes_ = [0, 1]
    n_iter_ = 150
    do_early_stopping_ = False

    def __init__(self, leaves: int = 7) -> None:
        self.leaves = leaves

    def get_params(self) -> dict[str, Any]:
        return dict(
            HistGradientBoostingClassifier(
                max_leaf_nodes=self.leaves,
                learning_rate=0.05,
                max_iter=150,
                min_samples_leaf=100,
                l2_regularization=1.0,
                max_bins=255,
                early_stopping=False,
                class_weight="balanced",
                random_state=42,
            ).get_params()
        )

    def predict_proba(self, values: Any) -> list[list[float]]:
        return [[0.3, 0.7]]


@pytest.fixture
def artifact() -> tuple[HGBArtifact, bytes]:
    blob = pickle.dumps(StubEstimator())
    return HGBArtifact(
        "up",
        hashlib.sha256(blob).hexdigest(),
        "a" * 64,
        "b" * 64,
        "snapshot",
        canonical(StubEstimator().get_params()),
        tuple(sorted((p, version(p)) for p in ("scikit-learn", "numpy", "scipy"))),
        FEATURES,
        "ohlcv_value_v1",
        "reversal_barrier_v1",
    ), blob


def load(artifact: tuple[HGBArtifact, bytes], monkeypatch: pytest.MonkeyPatch) -> HGBInference:
    monkeypatch.setattr("sklearn.ensemble.HistGradientBoostingClassifier", StubEstimator)
    a, blob = artifact
    return HGBInference(a, blob, trusted_artifact_fingerprint=a.fingerprint)


def test_score_and_context(
    artifact: tuple[HGBArtifact, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    model = load(artifact, monkeypatch)
    flags = frozenset({"research_sessions_excluded_not_holidays"})
    policy = ResearchPolicy("explicit", flags)
    result = model.research(
        FEATURES,
        (0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        mode="historical_research",
        quality_flags=flags,
        policy=policy,
    )
    assert result.score == 0.7
    assert not result.executable and not result.operational_eligible
    result = model.research(
        FEATURES, (0.0,) * 8, mode="historical_research", quality_flags=flags, policy=policy
    )
    assert result.score is None and result.status == "context_mismatch"
    with pytest.raises(AnalysisError, match="quality_not_allowed"):
        model.research(
            FEATURES,
            (0.0,) * 8,
            mode="historical_research",
            quality_flags=flags,
            policy=ResearchPolicy("strict", frozenset()),
        )
    with pytest.raises(AnalysisError, match="research_only"):
        model.research(
            FEATURES, (0.0,) * 8, mode="point_in_time", quality_flags=flags, policy=policy
        )


@pytest.mark.parametrize("values", [(0.0,) * 7, (float("nan"),) * 8, (True,) * 8])
def test_bad_features(
    artifact: tuple[HGBArtifact, bytes], monkeypatch: pytest.MonkeyPatch, values: tuple[float, ...]
) -> None:
    model = load(artifact, monkeypatch)
    with pytest.raises(AnalysisError, match="invalid_features"):
        model.score(FEATURES, values)
    with pytest.raises(AnalysisError, match="invalid_features"):
        model.score(tuple(reversed(FEATURES)), (0.0,) * 8)


def test_integrity(artifact: tuple[HGBArtifact, bytes]) -> None:
    a, blob = artifact
    with pytest.raises(AnalysisError, match="untrusted_artifact"):
        HGBInference(a, blob, trusted_artifact_fingerprint="bad")
    with pytest.raises(AnalysisError, match="checkpoint_mismatch"):
        HGBInference(a, blob + b"x", trusted_artifact_fingerprint=a.fingerprint)
    with pytest.raises(AnalysisError, match="invalid_checkpoint"):
        HGBInference(a, blob, trusted_artifact_fingerprint=a.fingerprint)
    with pytest.raises(AnalysisError, match="incompatible_artifact"):
        replace(a, features=tuple(reversed(FEATURES)))
    with pytest.raises(AnalysisError, match="incompatible_parameters"):
        replace(a, direction="down")
    assert replace(a, source_snapshot_id="other").fingerprint != a.fingerprint
    assert replace(a).fingerprint == a.fingerprint


def test_runtime_mismatch(artifact: tuple[HGBArtifact, bytes]) -> None:
    a, blob = artifact
    a = replace(a, runtime_versions=tuple((p, "0") for p, _ in a.runtime_versions))
    with pytest.raises(AnalysisError, match="runtime_mismatch"):
        HGBInference(a, blob, trusted_artifact_fingerprint=a.fingerprint)


def test_linear_unchanged() -> None:
    params = dict(
        features=list(FEATURES),
        classes=[0, 1],
        mean=[0] * 8,
        scale=[1] * 8,
        coefficient=[0] * 8,
        intercept=0,
    )
    assert LinearInference(canonical(params)).score(FEATURES, (0.0,) * 8) == 0.5


@pytest.mark.parametrize("field", ["feature_version", "label_version", "experiment_lock_id"])
def test_incompatible_metadata(artifact: tuple[HGBArtifact, bytes], field: str) -> None:
    changes: dict[str, Any] = {field: "invalid"}
    with pytest.raises(AnalysisError):
        replace(artifact[0], **changes)


def test_nonfinite_output(
    artifact: tuple[HGBArtifact, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    model = load(artifact, monkeypatch)
    monkeypatch.setattr(StubEstimator, "predict_proba", lambda self, values: [[0.0, float("nan")]])
    with pytest.raises(AnalysisError, match="inference_failed"):
        model.score(FEATURES, (0.0,) * 8)


def test_malformed_checkpoint(artifact: tuple[HGBArtifact, bytes]) -> None:
    blob = b"not a pickle"
    a = replace(artifact[0], checkpoint_sha256=hashlib.sha256(blob).hexdigest())
    with pytest.raises(AnalysisError, match="invalid_checkpoint"):
        HGBInference(a, blob, trusted_artifact_fingerprint=a.fingerprint)


def test_down_conditional_score(
    artifact: tuple[HGBArtifact, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    estimator = StubEstimator(15)
    blob = pickle.dumps(estimator)
    a = replace(
        artifact[0],
        direction="down",
        parameters_json=canonical(estimator.get_params()),
        checkpoint_sha256=hashlib.sha256(blob).hexdigest(),
    )
    model = load((a, blob), monkeypatch)
    kwargs: dict[str, Any] = dict(
        mode="historical_research",
        quality_flags=frozenset(),
        policy=ResearchPolicy("no-flags", frozenset()),
    )
    positive = model.research(FEATURES, (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0), **kwargs)
    negative = model.research(FEATURES, (0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0), **kwargs)
    assert positive.score == 0.7
    assert negative.score is None
