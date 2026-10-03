"""Score-only research inference. No registration, fitting or trading thresholds."""

import hashlib
import math
import pickle
from dataclasses import dataclass
from importlib.metadata import version
from typing import Protocol

from donghak_stock_vision.data.learning import (
    FEATURE_VERSION,
    FEATURES,
    LABEL_VERSION,
    AnalysisError,
    canonical,
    digest,
)
from donghak_stock_vision.models.linear import score, validate_parameters


class ScoreModel(Protocol):
    def score(self, names: tuple[str, ...], values: tuple[float, ...]) -> float: ...


def validate_features(names: tuple[str, ...], values: tuple[float, ...]) -> None:
    if (
        names != FEATURES
        or len(values) != len(FEATURES)
        or any(type(v) not in {int, float} or not math.isfinite(v) for v in values)
    ):
        raise AnalysisError("invalid_features")


@dataclass(frozen=True)
class LinearInference:
    parameters_json: str

    def __post_init__(self) -> None:
        import json

        validate_parameters(json.loads(self.parameters_json))

    def score(self, names: tuple[str, ...], values: tuple[float, ...]) -> float:
        import json

        validate_features(names, values)
        return score(json.loads(self.parameters_json), values)[0]


@dataclass(frozen=True)
class ResearchPolicy:
    """Explicit allowlist, independent of Phase 3 decision policy."""

    policy_id: str
    allowed_quality_flags: frozenset[str]

    def __post_init__(self) -> None:
        if (
            not self.policy_id
            or type(self.allowed_quality_flags) is not frozenset
            or any(type(flag) is not str or not flag for flag in self.allowed_quality_flags)
        ):
            raise AnalysisError("invalid_research_policy")

    @property
    def fingerprint(self) -> str:
        return digest([self.policy_id, sorted(self.allowed_quality_flags)])


@dataclass(frozen=True)
class HGBArtifact:
    direction: str
    checkpoint_sha256: str
    experiment_lock_id: str
    qualification_id: str
    source_snapshot_id: str
    parameters_json: str
    runtime_versions: tuple[tuple[str, str], ...]
    features: tuple[str, ...]
    feature_version: str
    label_version: str

    def __post_init__(self) -> None:
        import json

        if self.direction not in {"up", "down"} or self.features != FEATURES:
            raise AnalysisError("incompatible_artifact")
        if self.feature_version != FEATURE_VERSION or self.label_version != LABEL_VERSION:
            raise AnalysisError("incompatible_artifact")
        for identifier in (self.checkpoint_sha256, self.experiment_lock_id, self.qualification_id):
            if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
                raise AnalysisError("invalid_provenance")
        if not self.source_snapshot_id:
            raise AnalysisError("invalid_provenance")
        if type(self.runtime_versions) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2 for pair in self.runtime_versions
        ):
            raise AnalysisError("invalid_runtime")
        if tuple(sorted(dict(self.runtime_versions).items())) != self.runtime_versions or set(
            dict(self.runtime_versions)
        ) != {"scikit-learn", "numpy", "scipy"}:
            raise AnalysisError("invalid_runtime")
        try:
            params = json.loads(self.parameters_json)
            expected = {
                "max_leaf_nodes": 7 if self.direction == "up" else 15,
                "learning_rate": 0.05,
                "max_iter": 150,
                "min_samples_leaf": 100,
                "l2_regularization": 1.0,
                "max_bins": 255,
                "early_stopping": False,
                "class_weight": "balanced",
                "random_state": 42,
            }
            if any(params.get(k) != v for k, v in expected.items()):
                raise ValueError("parameters")
            if canonical(params) != self.parameters_json:
                raise ValueError("canonical parameters required")
        except (ValueError, TypeError, AttributeError) as error:
            raise AnalysisError("incompatible_parameters") from error

    @property
    def fingerprint(self) -> str:
        from dataclasses import asdict

        return digest(asdict(self))


@dataclass(frozen=True)
class ResearchScore:
    direction: str
    score: float | None
    status: str
    artifact_fingerprint: str
    policy_fingerprint: str
    quality_flags: tuple[str, ...]
    operational_eligible: bool = False
    executable: bool = False


class HGBInference:
    """Only load locally trusted checkpoints. Hashes are integrity, not pickle sandboxing.

    trusted_artifact_fingerprint must come from independently verified experiment
    lock/qualification provenance, never from an untrusted artifact itself.
    """

    def __init__(
        self, artifact: HGBArtifact, checkpoint: bytes, *, trusted_artifact_fingerprint: str
    ) -> None:
        from sklearn.ensemble import HistGradientBoostingClassifier

        if artifact.fingerprint != trusted_artifact_fingerprint:
            raise AnalysisError("untrusted_artifact")
        if hashlib.sha256(checkpoint).hexdigest() != artifact.checkpoint_sha256:
            raise AnalysisError("checkpoint_mismatch")
        if any(version(package) != expected for package, expected in artifact.runtime_versions):
            raise AnalysisError("runtime_mismatch")
        try:
            estimator = pickle.loads(
                checkpoint
            )  # Trusted local bytes only, after integrity checks.
            if (
                type(estimator) is not HistGradientBoostingClassifier
                or canonical(estimator.get_params()) != artifact.parameters_json
                or estimator.n_features_in_ != len(FEATURES)
                or list(estimator.classes_) != [0, 1]
                or estimator.n_iter_ != 150
                or estimator.do_early_stopping_
            ):
                raise ValueError("incompatible estimator")
        except Exception as error:
            raise AnalysisError("invalid_checkpoint") from error
        self._artifact = artifact
        self._estimator = estimator

    def score(self, names: tuple[str, ...], values: tuple[float, ...]) -> float:
        from threadpoolctl import threadpool_limits

        validate_features(names, values)
        try:
            with threadpool_limits(limits=1):
                result = float(self._estimator.predict_proba([values])[0][1])
            if not math.isfinite(result) or not 0 <= result <= 1:
                raise ValueError("score")
            return result
        except Exception as error:
            raise AnalysisError("inference_failed") from error

    def research(
        self,
        names: tuple[str, ...],
        values: tuple[float, ...],
        *,
        mode: str,
        quality_flags: frozenset[str],
        policy: ResearchPolicy,
    ) -> ResearchScore:
        if mode != "historical_research":
            raise AnalysisError("research_only")
        if not quality_flags <= policy.allowed_quality_flags:
            raise AnalysisError("quality_not_allowed")
        validate_features(names, values)
        eligible = values[2] < 0 if self._artifact.direction == "up" else values[2] > 0
        return ResearchScore(
            self._artifact.direction,
            self.score(names, values) if eligible else None,
            "scored" if eligible else "context_mismatch",
            self._artifact.fingerprint,
            policy.fingerprint,
            tuple(sorted(quality_flags)),
        )
