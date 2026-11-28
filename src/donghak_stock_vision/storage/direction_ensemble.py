"""Resolve fixed Shadow ensembles from pinned, completed development studies."""

import math
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require, sha, text
from donghak_stock_vision.data.learning import digest, timestamp
from donghak_stock_vision.features.research import GROUPS
from donghak_stock_vision.models.research_v2 import contract
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.real_market_training import _quiescent
from donghak_stock_vision.storage.research_checkpoint import verified_estimator

VERSION = "direction_fixed_ensemble_v1"
WEIGHT_BPS = 10_000
FAMILIES = {"random_forest", "hist_gradient_boosting", "linear"}


def _r3(
    root: Path, pin: str, run: str, experiment: str, at: datetime
) -> tuple[dict[str, Any], dict[str, Any], datetime]:
    from donghak_stock_vision.storage import predictive_research as owner

    plan = owner._plan(root, pin)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        result = store.connection.execute(
            "SELECT json_extract(payload,'$.plan_id'),json_extract(payload,'$.shortlist_id') "
            "FROM analysis_artifacts WHERE kind='predictive_research_result' AND id=?",
            (run,),
        ).fetchone()
        require(result is not None and result[0] == pin, "direction_ensemble_r3_owner")
        shortlist = store.get("predictive_research_shortlist", result[1])
        matches = [
            (key, value)
            for key, value in store.all("predictive_research_trial")
            if value["experiment_id"] == experiment
        ]
        require(len(matches) == 1, "direction_ensemble_base_missing")
        key, trial = matches[0]
        created = store.created_at("predictive_research_trial", key)
    require(
        shortlist["plan_id"] == pin
        and timestamp(plan["registered_at"]) <= created <= timestamp(shortlist["frozen_at"]) <= at,
        "direction_ensemble_base_clock",
    )
    return plan | {"source_owners": plan["sources"]}, trial, created


def _r6(
    root: Path, pin: str, run: str, experiment: str, at: datetime
) -> tuple[dict[str, Any], dict[str, Any], datetime]:
    from donghak_stock_vision.storage import supervised_evidence as owner

    plan = owner._plan(root, pin)
    state = owner._ledger(root)
    closed = state["closures"].get(run, {})
    require(
        run in state["windows"]
        and state["windows"][run]["plan_id"] == pin
        and closed.get("reason") == "requested_steps_complete",
        "direction_ensemble_r6_owner",
    )
    matches = [
        (key, value)
        for key, value in state["completed"].items()
        if value["trial"]["experiment_id"] == experiment
        and state["attempts"][value["attempt"]]["window"] == run
    ]
    require(len(matches) == 1, "direction_ensemble_base_missing")
    key, value = matches[0]
    attempt = state["attempts"][value["attempt"]]
    audit = state["audited"][value["attempt"]]
    trial = value["trial"]
    raw = state["trials"][experiment]
    checkpoint = state["checkpoints"][key]
    require(
        attempt["plan_id"] == pin
        and not audit["returncode"]
        and audit["reason"] is None
        and all(trial[k] == v for k, v in raw.items())
        and all(
            checkpoint[k] == trial[k]
            for k in (
                "experiment_id",
                "model_id",
                "checkpoint",
                "training_population",
                "evaluation_population",
                "config",
            )
        ),
        "direction_ensemble_r6_checkpoint",
    )
    created = timestamp(value["recorded_at"])
    require(
        timestamp(plan["registered_at"])
        <= timestamp(state["windows"][run]["started_at"])
        <= created
        <= timestamp(closed["ended_at"])
        <= at,
        "direction_ensemble_base_clock",
    )
    return plan, trial, created


def _member(ref: dict[str, Any], at: datetime, load_checkpoint: bool) -> dict[str, Any]:
    fields(ref, "owner root plan_pin_file run_id database_sha256 experiment_id")
    require(ref["owner"] in ("r3", "r6"), "direction_ensemble_base_owner")
    root = Path(text(ref["root"]))
    pin_file = Path(text(ref["plan_pin_file"]))
    require((root / DB).is_file() and pin_file.is_file(), "direction_ensemble_base_missing")
    pin = read_pin(pin_file)
    run, experiment, db_hash = (sha(ref[k]) for k in ("run_id", "experiment_id", "database_sha256"))
    _quiescent(root / DB, db_hash)
    reader = _r3 if ref["owner"] == "r3" else _r6
    plan, trial, created = reader(root, pin, run, experiment, at)
    cfg = trial["config"]
    goal = cfg["goal"]
    candidate = cfg["candidate"]
    train = trial["training_counts"]
    train_pin = sha(trial["training_population"])
    model_id = sha(trial["model_id"])
    checkpoint = root / "models" / (experiment + ".checkpoint")
    require(
        trial["state"] == "completed"
        and "rows_id" in trial
        and candidate["family"] in FAMILIES
        and candidate["parameters"].get("solver") != "liblinear"
        and cfg["mask"] in {"v2_" + group for group in GROUPS}
        and goal == contract(goal["horizon"])
        and cfg["stock_policy"] == "heldout"
        and experiment == digest(dict(plan_id=pin, config=cfg, train=train_pin))
        and checkpoint.relative_to(root).as_posix() == trial["checkpoint"]
        and min(train["positive"], train["negative"]) > 0
        and train["positive"] + train["negative"] == train["rows"],
        "direction_ensemble_base_ineligible",
    )
    owners = plan["source_owners"]
    protected = owners[0]["original_final_test"]
    require(
        all(s["original_final_test"] == protected for s in owners),
        "direction_ensemble_protected_binding",
    )
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        retained = store.get("predictive_research_rows", sha(trial["rows_id"]))
        require(
            retained["experiment_id"] == experiment
            and len(retained["rows"]) == trial["summary"]["metrics"]["n"],
            "direction_ensemble_base_evaluation",
        )
    fit_end = plan["windows"]["train"]["end"]
    require(fit_end < created.date().isoformat(), "direction_ensemble_base_clock")
    require(checkpoint.is_file(), "direction_ensemble_checkpoint_missing")
    _quiescent(checkpoint, model_id)
    if load_checkpoint:
        verified_estimator(checkpoint, model_id, candidate["family"], plan["code"]["versions"])
    _quiescent(root / DB, db_hash)
    return dict(
        experiment_id=experiment,
        sha256=model_id,
        created_at=created.isoformat(),
        config=cfg,
        checkpoint=str(checkpoint),
        versions=plan["code"]["versions"],
        fit_end=fit_end,
        protected=protected,
        baseline=train["positive"] / train["rows"],
        lineage=dict(
            plan_id=pin,
            run_id=run,
            source_owners=[
                {k: sha(s[k]) for k in ("source_pin", "dataset_id", "plan_pin")} for s in owners
            ],
            training_population=train_pin,
            evaluation_population=sha(trial["evaluation_population"]),
            development_rows_id=sha(trial["rows_id"]),
            exposed_through=plan["windows"]["evaluation"]["end"],
        ),
    )


def resolve(ref: dict[str, Any], at: datetime, *, load_checkpoint: bool) -> dict[str, Any]:
    fields(ref, "version horizon members")
    require(ref["version"] == VERSION, "direction_ensemble_version")
    h = ref["horizon"]
    require(type(h) is int and h in (1, 5, 20), "direction_ensemble_horizon")
    specs = ref["members"]
    require(type(specs) is list and 2 <= len(specs) <= 3, "direction_ensemble_member_count")
    for spec in specs:
        fields(spec, "reference weight_bps")
        w = spec["weight_bps"]
        require(type(w) is int and 0 < w < WEIGHT_BPS, "direction_ensemble_weight")
    require(sum(s["weight_bps"] for s in specs) == WEIGHT_BPS, "direction_ensemble_weight_sum")
    members = [_member(spec["reference"], at, load_checkpoint) for spec in specs]
    require(
        len({m["experiment_id"] for m in members}) == len(members)
        and len({m["sha256"] for m in members}) == len(members),
        "direction_ensemble_duplicate_base",
    )
    require(
        all(m["config"]["goal"]["horizon"] == h for m in members),
        "direction_ensemble_horizon_mismatch",
    )
    control = members[0]
    population_keys = ("source_owners", "training_population", "evaluation_population")
    require(
        all(
            m["fit_end"] == control["fit_end"]
            and m["baseline"] == control["baseline"]
            and m["protected"] == control["protected"]
            and all(m["lineage"][k] == control["lineage"][k] for k in population_keys)
            for m in members[1:]
        ),
        "direction_ensemble_population_mismatch",
    )
    ordered = sorted(zip(members, specs, strict=True), key=lambda item: item[0]["experiment_id"])
    config = dict(
        version=VERSION,
        rule="weighted_upward_score_mean",
        goal=contract(h),
        members=[
            dict(
                experiment_id=m["experiment_id"],
                model_id=m["sha256"],
                weight_bps=s["weight_bps"],
                lineage=m["lineage"],
            )
            for m, s in ordered
        ],
    )
    config_hash = digest(config)
    identity = digest(dict(kind=VERSION, config_hash=config_hash))
    return dict(
        reference=ref | {"members": [s for _, s in ordered]},
        experiment_id=identity,
        ensemble_id=identity,
        sha256=config_hash,
        created_at=at.isoformat(),
        config=config,
        members=[m for m, _ in ordered],
        fit_end=control["fit_end"],
        protected=control["protected"],
        baseline=control["baseline"],
        lineage=dict(
            exposed_through=max(m["lineage"]["exposed_through"] for m in members),
            training_population=control["lineage"]["training_population"],
            evaluation_population=control["lineage"]["evaluation_population"],
            source_owners=control["lineage"]["source_owners"],
            descriptive_only=h == 20,
        ),
    )


def average(weights: list[int], scores: list[float]) -> float:
    require(
        all(math.isfinite(p) and 0 <= p <= 1 for p in scores),
        "direction_ensemble_invalid_score",
    )
    return math.fsum(w * p for w, p in zip(weights, scores, strict=True)) / WEIGHT_BPS


def source_paths(ref: dict[str, Any]) -> list[Path]:
    specs = (
        [part["reference"] for part in ref["members"]] if ref.get("version") == VERSION else [ref]
    )
    return [
        p for s in specs for p in (Path(text(s["root"])), Path(text(s["plan_pin_file"])).parent)
    ]
