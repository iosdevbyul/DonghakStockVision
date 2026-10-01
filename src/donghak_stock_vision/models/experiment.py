"""Training-only research selection and one locked validation gate; no test path.

Reuses the Phase 2 dataset, weighting, linear fit and metric contracts. Nonlinear
estimators remain research objects, never production signal-model artifacts.
"""

import logging
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from donghak_stock_vision.data.learning import FEATURES, AnalysisError, Direction, Sample, digest
from donghak_stock_vision.data.research_split import ResearchDateRange
from donghak_stock_vision.models.fit import fit_linear
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.metrics import evaluate_metrics
from donghak_stock_vision.signals.dataset import build_dataset, counts, sample_weights
from donghak_stock_vision.storage.analysis import AnalysisStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    value: float

    def __post_init__(self) -> None:
        if (
            not self.name
            or (self.family == "linear" and self.value not in (0.1, 1.0, 10.0))
            or (self.family == "hist_gradient_boosting" and self.value not in (7, 15))
            or self.family not in ("linear", "hist_gradient_boosting")
        ):
            raise AnalysisError("invalid_research_candidate")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CANDIDATES = (
    Candidate("linear_c01", "linear", 0.1),
    Candidate("linear_c1", "linear", 1.0),
    Candidate("linear_c10", "linear", 10.0),
    Candidate("hist_leaves7", "hist_gradient_boosting", 7),
    Candidate("hist_leaves15", "hist_gradient_boosting", 15),
)


@dataclass(frozen=True)
class Fold:
    training: ResearchDateRange
    evaluation: ResearchDateRange

    def __post_init__(self) -> None:
        if self.training.end >= self.evaluation.start:
            raise AnalysisError("overlapping_fold")

    def to_dict(self) -> dict[str, Any]:
        return {"training": self.training.to_dict(), "evaluation": self.evaluation.to_dict()}


def bounded_samples(
    snapshot: dict[str, Any], permitted: ResearchDateRange
) -> tuple[list[Sample], dict[str, int]]:
    """Reject an over-wide snapshot before calling feature/label generation."""
    if snapshot["mode"] != "historical_research":
        raise AnalysisError("research_only")
    if date.fromisoformat(snapshot["end"]) > permitted.end or any(
        date.fromisoformat(row["trading_date"]) > permitted.end
        for rows in snapshot["rows"].values()
        for row in rows
    ):
        raise AnalysisError("snapshot_beyond_permitted_period")
    samples, exclusions = build_dataset(snapshot)
    return [
        s
        for s in samples
        if permitted.start <= s.trading_date <= permitted.end
        and s.label in (0, 1)
        and s.direction is not None
        and s.label_end is not None
        and s.label_end <= permitted.end
    ], exclusions


def purged_fold(
    samples: list[Sample], fold: Fold, direction: Direction
) -> tuple[list[Sample], list[Sample], int]:
    train, evaluation = [], []
    purged = 0
    for s in samples:
        if s.direction != direction or s.label is None or s.label_end is None:
            continue
        if fold.training.start <= s.trading_date <= fold.training.end:
            if s.label_end >= fold.evaluation.start:
                purged += 1
            else:
                train.append(s)
        elif fold.evaluation.start <= s.trading_date <= fold.evaluation.end:
            if s.label_end > fold.evaluation.end:
                purged += 1
            else:
                evaluation.append(s)
    return train, evaluation, purged


def fit_candidate(candidate: Candidate, samples: list[Sample]) -> Any:
    if {s.label for s in samples} != {0, 1}:
        raise AnalysisError("two_classes_required")
    if candidate.family == "linear":
        return fit_linear(samples, candidate.value)
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from threadpoolctl import threadpool_limits

    # No randomized early-stopping validation split; every supplied row is training.
    estimator = HistGradientBoostingClassifier(
        loss="log_loss",
        learning_rate=0.05,
        max_iter=150,
        max_leaf_nodes=int(candidate.value),
        min_samples_leaf=100,
        l2_regularization=1.0,
        max_bins=255,
        early_stopping=False,
        class_weight="balanced",
        random_state=42,
    )
    with threadpool_limits(limits=1):
        estimator.fit(
            np.asarray([s.values for s in samples]),
            np.asarray([s.label for s in samples]),
            sample_weight=np.asarray(sample_weights(samples)),
        )
    return estimator


def predict_candidate(candidate: Candidate, fitted: Any, samples: list[Sample]) -> list[float]:
    if candidate.family == "linear":
        return [score(fitted, s.values)[0] for s in samples]
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        return [float(p[1]) for p in fitted.predict_proba([s.values for s in samples])]


def metrics(samples: list[Sample], scores: list[float]) -> dict[str, Any]:
    result = evaluate_metrics([int(s.label) for s in samples if s.label is not None], scores)
    cm = result["confusion_matrix"]
    denominator = 2 * cm["tp"] + cm["fp"] + cm["fn"]
    result["f1"] = 2 * cm["tp"] / denominator if denominator else None
    return result


def select_candidate(results: list[dict[str, Any]]) -> str:
    """Predeclared stability criterion; exact ties follow the fixed candidate order."""
    if not results:
        raise AnalysisError("no_candidates")
    return str(max(results, key=lambda r: (r["stability_ap"], r["mean_ap"]))["candidate"])


def develop(
    samples: list[Sample],
    development: ResearchDateRange,
    folds: tuple[Fold, ...],
) -> dict[str, Any]:
    if not folds or any(
        s.trading_date < development.start
        or s.trading_date > development.end
        or s.label_end is None
        or s.label_end > development.end
        for s in samples
    ):
        raise AnalysisError("development_boundary_violation")
    for i, fold in enumerate(folds):
        if (
            fold.training.start != development.start
            or fold.evaluation.end > development.end
            or (i and folds[i - 1].evaluation.end >= fold.evaluation.start)
            or (i and folds[i - 1].training.end >= fold.training.end)
        ):
            raise AnalysisError("invalid_expanding_folds")
    directions: dict[str, Any] = {}
    for direction in ("up", "down"):
        results = []
        for candidate in CANDIDATES:
            evaluations = []
            for index, fold in enumerate(folds):
                train, holdout, purged = purged_fold(samples, fold, direction)
                for rows, min_dates in ((train, 252), (holdout, 40)):
                    n = counts(rows)
                    if (
                        n["rows"] < 200
                        or n["dates"] < min_dates
                        or min(n["positive"], n["negative"]) < 20
                    ):
                        raise AnalysisError("insufficient_internal_population")
                fitted = fit_candidate(candidate, train)
                measured = metrics(holdout, predict_candidate(candidate, fitted, holdout))
                evaluations.append(
                    {"fold": index, "train": counts(train), "purged": purged, **measured}
                )
                logger.info("internal %s %s fold=%d complete", direction, candidate.name, index)
            aps = [e["pr_auc_ap"] for e in evaluations]
            mean, variation = statistics.mean(aps), statistics.pstdev(aps)
            results.append(
                {
                    "candidate": candidate.name,
                    "folds": evaluations,
                    "mean_ap": mean,
                    "std_ap": variation,
                    "stability_ap": mean - variation,
                }
            )
        directions[direction] = {"candidates": results, "selected": select_candidate(results)}
    return {
        "directions": directions,
        "selection": "mean_ap_minus_population_std_then_mean_then_fixed_candidate_order",
        "sample_hash": digest([s.to_dict() for s in samples]),
        "features": list(FEATURES),
        "folds": [f.to_dict() for f in folds],
        "final_test_accessed": False,
    }


def estimator_bytes(fitted: Any) -> bytes:
    """Local version-bound research checkpoint only; there is no pickle load API."""
    import pickle

    return pickle.dumps(fitted, protocol=5)


def estimator_hash(fitted: Any) -> str:
    import hashlib

    return hashlib.sha256(estimator_bytes(fitted)).hexdigest()


def qualify_locked(
    store: AnalysisStore,
    lock_id: str,
    fitted: dict[str, Any],
    snapshot_loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Load validation only after verifying a persisted lock; consume the gate once.

    The callback must use a bounded market read. No existing model/dataset artifact
    is loaded because those artifacts may contain the prohibited final test.
    """
    lock = store.get("experiment_lock", lock_id)
    span = ResearchDateRange(
        date.fromisoformat(lock["validation"]["start"]),
        date.fromisoformat(lock["validation"]["end"]),
    )
    if set(fitted) != {"up", "down"} or any(
        estimator_hash(fitted[d]) != lock["directions"][d]["estimator_hash"] for d in ("up", "down")
    ):
        raise AnalysisError("locked_estimator_mismatch")
    if any(
        record["lock_id"] == lock_id for _, record in store.all("experiment_validation_started")
    ):
        raise AnalysisError("validation_already_consumed")
    store.put("experiment_validation_started", {"lock_id": lock_id})
    snapshot = snapshot_loader()
    if snapshot.get("research_calendar") != lock["research_calendar"]:
        raise AnalysisError("research_calendar_mismatch")
    rows, exclusions = bounded_samples(snapshot, span)
    result: dict[str, Any] = {
        "lock_id": lock_id,
        "validation_snapshot_hash": digest(snapshot),
        "exclusions": exclusions,
        "directions": {},
        "final_test_accessed": False,
        "usage_restriction": "research_only",
        "operational_status": "unregistered",
    }
    for direction in ("up", "down"):
        selected = lock["directions"][direction]
        candidate = next(c for c in CANDIDATES if c.to_dict() == selected["candidate"])
        population = [s for s in rows if s.direction == direction]
        n = counts(population)
        if n["rows"] < 200 or n["dates"] < 63 or min(n["positive"], n["negative"]) < 20:
            raise AnalysisError("insufficient_validation_population")
        measured = metrics(population, predict_candidate(candidate, fitted[direction], population))
        reference = selected["approved_baseline_validation_ap"]
        margin = measured["pr_auc_ap"] - reference
        result["directions"][direction] = {
            "candidate": candidate.to_dict(),
            "population": n,
            "metrics": measured,
            "approved_baseline_validation_ap": reference,
            "ap_improvement": margin,
            "qualification": "validation_gate_passed" if margin >= 0.02 else "baseline_not_beaten",
        }
        logger.info("locked validation %s complete", direction)
    result["report_id"] = store.put("experiment_qualification", result)
    return result
