"""Training and frozen evaluation. Neither service registers operational models."""

import importlib.metadata
import os
import subprocess
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import (
    FEATURES,
    AnalysisError,
    Direction,
    Minimums,
    digest,
    event_contract,
)
from donghak_stock_vision.models.fit import fit_linear
from donghak_stock_vision.models.linear import score, validate_parameters
from donghak_stock_vision.models.metrics import baseline_scores, report
from donghak_stock_vision.signals.dataset import (
    build_dataset,
    population,
    requirements,
    split_dataset,
)
from donghak_stock_vision.storage.analysis import AnalysisStore


def provenance() -> dict[str, Any]:
    versions = {}
    for name in (
        "scikit-learn",
        "numpy",
        "scipy",
        "joblib",
        "threadpoolctl",
        "donghak-stock-vision",
    ):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    root = Path(__file__).resolve().parents[1]
    code_hash = digest(
        {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.py"))}
    )
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        revision = result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        revision = "unknown"
    return {
        "versions": versions,
        "git_sha": os.getenv("DSV_CODE_REVISION", revision),
        "implementation_hash": code_hash,
        "seed": 42,
        "threads": 1,
        "numeric_tolerance": 1e-10,
    }


class TrainingService:
    def __init__(self, store: AnalysisStore) -> None:
        self.store = store

    def train(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = self.store.get("snapshot", snapshot_id)
        samples, exclusions = build_dataset(snapshot)
        minimums = Minimums.synthetic() if snapshot["data_origin"] == "synthetic" else Minimums()
        try:
            split = split_dataset(samples, snapshot["mode"])
        except AnalysisError:
            failure = {
                "snapshot_id": snapshot_id,
                "status": "insufficient_data",
                "reason": "insufficient_split_dates",
                "exclusions": exclusions,
                "model_version": None,
                "mode": snapshot["mode"],
                "usage_restriction": snapshot["usage_restriction"],
            }
            failure["report_id"] = self.store.put("training_failure", failure)
            return failure
        dataset = {
            "snapshot_id": snapshot_id,
            "samples": [s.to_dict() for s in samples],
            "exclusions": exclusions,
            "split": split,
        }
        dataset_id = digest(dataset)
        directions: dict[str, Any] = {}
        for direction in ("up", "down"):
            directions[direction] = self._direction(dataset, direction, minimums)
        artifact = {
            "schema_version": 1,
            "snapshot_id": snapshot_id,
            "dataset_id": dataset_id,
            **{
                k: snapshot[k]
                for k in (
                    "mode",
                    "data_origin",
                    "usage_restriction",
                    "anchor_policy",
                    "quality_flags",
                    "profiles",
                    "markets",
                )
            },
            **event_contract(snapshot["session_basis"]),
            "quality_manifest_hash": digest(snapshot["quality"]) if snapshot["quality"] else None,
            "data_range": [snapshot["start"], snapshot["end"]],
            "snapshot_as_of": snapshot["cutoff"],
            "features": list(FEATURES),
            "population_up": "return_5 < 0",
            "population_down": "return_5 > 0",
            "directions": directions,
            "operational_status": "unregistered",
            "provenance": provenance(),
            "exclusions": exclusions,
            "split_hash": digest(split),
            "test_policy": "frozen_train_fit_no_refit",
            "training_policy": {
                "estimator": "LogisticRegression",
                "solver": "lbfgs",
                "penalty": "l2",
                "max_iter": 2000,
                "class_weight": "balanced_train_only",
                "sample_weight": "inverse_context_date_count_times_mean_label_uniqueness",
                "weight_normalization": "train_mean_one",
                "threshold": 0.5,
                "C_candidates": [0.1, 1.0, 10.0],
                "selection": "validation_ap_then_lower_C",
                "qualification_ap_margin": 0.02,
            },
        }
        fitted = any(d["parameters"] is not None for d in directions.values())
        if not fitted:
            failure = {
                "snapshot_id": snapshot_id,
                "dataset_id": dataset_id,
                "mode": snapshot["mode"],
                "usage_restriction": snapshot["usage_restriction"],
                "status": "insufficient_data_or_fit_failed",
                "model_version": None,
                "directions": directions,
                "exclusions": exclusions,
            }
            ids = self.store.put_many([("dataset", dataset), ("training_failure", failure)])
            return {**failure, "report_id": ids[1]}
        ids = self.store.put_many([("dataset", dataset), ("model", artifact)])
        return {
            "status": "trained",
            "snapshot_id": snapshot_id,
            "model_version": ids[1],
            "mode": snapshot["mode"],
            "usage_restriction": snapshot["usage_restriction"],
            "operational_status": "unregistered",
            "directions": {
                k: {
                    field: v[field]
                    for field in ("evaluation_status", "test_status", "requirements")
                }
                for k, v in directions.items()
            },
        }

    def _direction(
        self, dataset: dict[str, Any], direction: Direction, minimums: Minimums
    ) -> dict[str, Any]:
        from donghak_stock_vision.data.learning import Sample

        split = dataset["split"]
        samples = [Sample.from_dict(s) for s in dataset["samples"]]
        checked = requirements(samples, split, direction, minimums)
        result: dict[str, Any] = {
            "requirements": checked,
            "parameters": None,
            "population_counts": {
                "in_context": sum(s.direction == direction for s in samples),
                "context_mismatch": sum(s.direction != direction for s in samples),
                "flat": sum(s.direction is None for s in samples),
                "in_context_unlabeled": sum(
                    s.direction == direction and s.label is None for s in samples
                ),
            },
            "evaluation_status": "insufficient_data",
            "test_status": "unavailable",
            "operational_status": "unregistered",
        }
        if checked["failures"]:
            return result
        train = population(split, direction, "train")
        validation = population(split, direction, "validation")
        prior = sum(s.label == 1 for s in train) / len(train)
        candidates: list[dict[str, Any]] = []
        for c in (0.1, 1.0, 10.0):
            try:
                parameters = fit_linear(train, c)
            except AnalysisError as error:
                if str(error).startswith("learning_extra_required"):
                    raise
                candidates.append({"C": c, "status": str(error)})
                continue
            metrics = report(validation, [score(parameters, s.values)[0] for s in validation])
            candidates.append(
                {"C": c, "status": "fitted", "parameters": parameters, "validation": metrics}
            )
        fitted = [candidate for candidate in candidates if candidate["status"] == "fitted"]
        if not fitted:
            return {**result, "evaluation_status": "fit_failed", "candidates": candidates}
        selected = max(fitted, key=lambda x: (x["validation"]["pr_auc_ap"], -x["C"]))
        validation_baselines = {
            name: report(validation, scores)
            for name, scores in baseline_scores(validation, direction, prior).items()
        }
        best_baseline = max(m["pr_auc_ap"] for m in validation_baselines.values())
        qualified = selected["validation"]["pr_auc_ap"] >= best_baseline + 0.02
        # The selected parameters are now frozen. Test is never passed to fit/selection.
        test = population(split, direction, "test")
        test_scores = [score(selected["parameters"], s.values)[0] for s in test]
        return {
            **result,
            "parameters": selected["parameters"],
            "prior": prior,
            "evaluation_status": "validation_qualified" if qualified else "baseline_not_beaten",
            "test_status": "evaluated",
            "validation": selected["validation"],
            "validation_scores": [score(selected["parameters"], s.values)[0] for s in validation],
            "validation_ap_margin": selected["validation"]["pr_auc_ap"] - best_baseline,
            "validation_baselines": validation_baselines,
            "test": report(test, test_scores),
            "test_scores": test_scores,
            "test_baselines": {
                name: report(test, scores)
                for name, scores in baseline_scores(test, direction, prior).items()
            },
            "candidates": [
                {k: v for k, v in candidate.items() if k != "parameters"}
                for candidate in candidates
            ],
        }


def load_model(store: AnalysisStore, version: str, mode: str | None = None) -> dict[str, Any]:
    artifact = store.get("model", version)
    try:
        if mode is not None and artifact["mode"] != mode:
            raise AnalysisError("mode_mismatch")
        if artifact.get("schema_version") != 1 or artifact.get("features") != list(FEATURES):
            raise AnalysisError("invalid_model")
        for key, value in event_contract(artifact["session_basis"]).items():
            if artifact.get(key) != value:
                raise AnalysisError("invalid_model")
        snapshot = store.get("snapshot", artifact["snapshot_id"])
        dataset = store.get("dataset", artifact["dataset_id"])
        if (
            dataset["snapshot_id"] != artifact["snapshot_id"]
            or digest(dataset["split"]) != artifact["split_hash"]
        ):
            raise AnalysisError("invalid_model")
        for key in ("mode", "data_origin", "usage_restriction", "anchor_policy", "session_basis"):
            if artifact[key] != snapshot[key]:
                raise AnalysisError("invalid_model")
        if set(artifact["directions"]) != {"up", "down"}:
            raise AnalysisError("invalid_model")
        for value in artifact["directions"].values():
            if value["parameters"] is not None:
                validate_parameters(value["parameters"])
        return artifact
    except AnalysisError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise AnalysisError("invalid_model") from error


class EvaluationService:
    def __init__(self, store: AnalysisStore) -> None:
        self.store = store

    def evaluate(self, version: str, mode: str) -> dict[str, Any]:
        model = load_model(self.store, version, mode)
        dataset = self.store.get("dataset", model["dataset_id"])
        snapshot = self.store.get("snapshot", model["snapshot_id"])
        # Re-derive from the immutable prices, not current MarketDataStore or cached feature output.
        samples, exclusions = build_dataset(snapshot)
        rebuilt = {
            "snapshot_id": model["snapshot_id"],
            "samples": [s.to_dict() for s in samples],
            "exclusions": exclusions,
            "split": split_dataset(samples, mode),
        }
        if digest(rebuilt) != model["dataset_id"] or dataset["snapshot_id"] != model["snapshot_id"]:
            raise AnalysisError("dataset_reproduction_mismatch")
        result = {
            "model_version": version,
            "model_created_at": self.store.created_at("model", version).isoformat(),
            "snapshot_id": model["snapshot_id"],
            "mode": mode,
            "usage_restriction": model["usage_restriction"],
            "operational_status": "unregistered",
            **event_contract(model["session_basis"]),
            "quality_flags": model["quality_flags"],
            "directions": model["directions"],
            "exclusions": exclusions,
            "split": {k: v for k, v in dataset["split"].items() if k != "partitions"},
        }
        for direction in ("up", "down"):
            entry = model["directions"][direction]
            if entry["parameters"] is None:
                continue
            for part in ("validation", "test"):
                rows = population(dataset["split"], direction, part)
                scores = [score(entry["parameters"], s.values)[0] for s in rows]
                if report(rows, scores) != entry[part]:
                    raise AnalysisError("evaluation_reproduction_mismatch")
        result["evaluation_id"] = self.store.put("evaluation", result)
        return result
