"""Original read-only research evidence and verified-context cache ownership."""

from __future__ import annotations

import hashlib
import statistics
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, private_root, terminals

_REPLAY_OWNER = object()


def evidence(
    root: Path, pin: str, *, extended: bool = False, _verified: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    cache_key = digest(dict(root=str(root), pin=pin, extended=extended))
    before = file_sha(root / DB) if _verified is not None else None
    if _verified is not None and cache_key in _verified:
        cached = _verified[cache_key]
        private_root(root)
        require(
            cached.get("owner") is _REPLAY_OWNER and before == cached["database_sha256"],
            "memory_evidence_snapshot_changed",
        )
        plan, data = cached["plan"], cached["data"]
        require(
            digest(plan) == pin and digest(data) == plan["dataset_id"], "memory_evidence_retrieval"
        )
    else:
        plan, data = context(root, pin, code_check=False)
    require(plan["version"] == "autonomous_plan_v6", "memory_historical_owner")
    allowed = {c["experiment_id"]: c["config"] for c in plan["configurations"]}
    rows = []
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        for key, t in terminals(store).items():
            require(allowed.get(key) == t["config"], "memory_original_experiment_owner")
            item = dict(experiment_id=key, config=t["config"], state=t["state"])
            if t["state"] == "completed":
                r = store.get("autonomous_result", t["result_id"])
                require(r["dataset_id"] == plan["dataset_id"], "memory_result_dataset")
                fs = r["folds"]
                fold_defs = data["goals"][str(t["config"]["goal"]["horizon"])]["folds"]
                require(len(fs) == len(fold_defs), "memory_original_fold_owner")
                details = []
                for f, spec in zip(fs, fold_defs, strict=True):
                    e = f["evaluation"]
                    regimes = e.get("seen_robustness", {}).get("input_regimes", {})
                    usable = [
                        (name, row["metrics"]["balanced_accuracy"])
                        for name, row in regimes.items()
                        if row["metrics"].get("balanced_accuracy") is not None
                    ]
                    details.append(
                        dict(
                            period=spec["evaluation"],
                            training_end=spec["training"]["end"],
                            metrics={
                                k: e["metrics"][k]
                                for k in (
                                    "balanced_accuracy",
                                    "pr_auc_ap",
                                    "roc_auc",
                                    "brier_score",
                                )
                            },
                            baseline_ap=max(b["pr_auc_ap"] for b in e["baselines"].values()),
                            overfit_gap=f["train"]["balanced_accuracy"]
                            - e["metrics"]["balanced_accuracy"],
                            counts=e["counts"],
                            weakest_seen_regime=min(usable, key=lambda pair: pair[1], default=None),
                        )
                    )
                item["internal_folds"] = details[-3:]
                if extended:
                    item["calibration_delta"] = statistics.mean(
                        f["evaluation"]["metrics"]["brier_score"]
                        - f["evaluation"]["baselines"]["training_prior"]["brier_score"]
                        for f in fs
                    )
                item.update(
                    result_id=t["result_id"],
                    mean_ap=r["mean_ap"],
                    stability_ap=r["stability_ap"],
                    overfit_gap=r["largest_train_gap"],
                    worst_balanced_accuracy=r["worst_balanced_accuracy"],
                    baseline_ap_gain=statistics.mean(
                        f["evaluation"]["metrics"]["pr_auc_ap"]
                        - max(b["pr_auc_ap"] for b in f["evaluation"]["baselines"].values())
                        for f in fs
                    ),
                )
            else:
                item["failure_reason"] = t["reason"]
            rows.append(item)
    if _verified is not None:
        require(before == file_sha(root / DB), "memory_evidence_snapshot_changed")
        require(cache_key in _verified or len(_verified) < 13, "memory_source_bound")
        if _verified:
            shared = next(iter(_verified.values()))
            require(
                plan["dataset_id"] == shared["plan"]["dataset_id"] and data == shared["data"],
                "memory_cross_dataset",
            )
            data = shared["data"]
        _verified[cache_key] = dict(
            plan=plan, data=data, owner=_REPLAY_OWNER, database_sha256=before
        )
    return plan, data, rows


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
