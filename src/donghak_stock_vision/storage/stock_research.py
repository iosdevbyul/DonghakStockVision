"""Policy-arm reuse and read-only generalization summaries over existing memory."""

import json
import statistics as st
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, terminals
from donghak_stock_vision.storage.krx_backfill import write_new
from donghak_stock_vision.storage.real_market_training import _quiescent


def fork_policy(
    parent: Path, pin: str, root: Path, pins: Path, *, policy: str = "predeclared"
) -> str:
    plan, data = context(parent, pin)
    require(
        plan["version"] in ("autonomous_plan_v5", "autonomous_plan_v6")
        and plan["selection_policy"] == "adaptive"
        and "policy_parent" not in plan
        and policy
        in (
            ("predeclared", "robust")
            if plan["version"] == "autonomous_plan_v6"
            else ("predeclared",)
        ),
        "multi_stock_policy_parent",
    )
    require(
        all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and root.parent == parent.parent
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "multi_stock_new_policy_root",
    )
    request = json.loads((parent / "request.json").read_text())
    require(digest(request) == plan["request_pin"], "multi_stock_original_request")
    _quiescent(Path(request["research"]["database"]), request["research"]["pin"])
    new = plan | dict(selection_policy=policy, policy_parent=dict(root=str(parent), pin=pin))
    root.mkdir(mode=0o700, parents=True)
    pins.mkdir(mode=0o700, parents=True)
    with (
        closing(ReadOnlyAnalysisStore(parent / DB)) as source,
        closing(AnalysisStore(root / DB)) as target,
    ):
        target.put_many(
            [
                ("autonomous_snapshot", source.get("autonomous_snapshot", plan["snapshot_id"])),
                ("autonomous_dataset", data),
                ("autonomous_plan", new),
                *[
                    ("autonomous_source_verification", v)
                    for _, v in source.all("autonomous_source_verification")
                ],
            ]
        )
    (root / DB).chmod(0o600)
    write_new(root / "request.json", (parent / "request.json").read_text())
    write_new(
        root / "policy-arm.json",
        json.dumps(
            dict(
                version="multi_stock_policy_fork_v1",
                source_request_pin=plan["request_pin"],
                parent_plan=pin,
                policy=policy,
                plan=digest(new),
            )
        ),
    )
    for name, value in (
        ("plan", digest(new)),
        ("dataset", plan["dataset_id"]),
        ("source", plan["source_pin"]),
        ("snapshot", plan["snapshot_id"]),
    ):
        write_new(pins / (name + ".pin"), value + "\n")
    return digest(new)


def report(root: Path, pin: str) -> dict[str, Any]:
    plan, data = context(root, pin, code_check=False)
    require(
        plan["version"] in ("autonomous_plan_v5", "autonomous_plan_v6"), "multi_stock_report_owner"
    )
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        done = terminals(store)
        results = [
            store.get("autonomous_result", v["result_id"])
            for v in done.values()
            if v["state"] == "completed"
        ]
        selections = [v for _, v in store.all("autonomous_selection")]
        costs = [v for _, v in store.all("autonomous_resource_audit")]
    locked = len(done) >= plan["experiment_budget"]
    horizons = {}
    for h, g in data["goals"].items():
        rows = [r for r in results if str(r["config"]["goal"]["horizon"]) == h]
        if plan["version"] == "autonomous_plan_v6":
            from donghak_stock_vision.models.historical_research import priority

            best = max(rows, key=lambda r: priority(r["robust_summary"]), default=None)
        else:
            best = max(rows, key=lambda r: r["stability_ap"], default=None)
        horizons[h] = dict(
            training_samples=g["training_rows"],
            samples_by_ticker=g["samples_by_ticker"],
            exclusions_by_ticker=g["exclusions_by_ticker"],
            readiness=g["readiness"],
            selected_without_heldout_scores=None if best is None else best["experiment_id"],
            experiments=[
                dict(
                    experiment_id=r["experiment_id"],
                    config=r["config"],
                    mean_seen_cv_ap=r["mean_ap"],
                    stability_seen_cv_ap=r["stability_ap"],
                    overfit_gap=r["largest_train_gap"],
                    robust_summary=r.get("robust_summary"),
                    deferred_diagnostic=r.get("deferred_diagnostic") if locked else None,
                    training=r["generalization_training"],
                    fold_seen_metrics=[f["evaluation"]["metrics"] for f in r["folds"]],
                    fold_baselines=[f["evaluation"]["baselines"] for f in r["folds"]],
                    fold_generalization=[
                        f["evaluation"]["stock_generalization"] for f in r["folds"]
                    ]
                    if locked
                    else None,
                    nonoverlapping=[
                        dict(
                            calendar_dates=f["evaluation"]["nonoverlapping_calendar_dates"],
                            assessment=f["evaluation"]["nonoverlapping_generalization"],
                        )
                        for f in r["folds"]
                    ]
                    if locked
                    else None,
                )
                for r in rows
            ],
        )
    discovery = []
    if plan["version"] == "autonomous_plan_v6":
        from donghak_stock_vision.models.historical_research import curve

        discovery = curve(done)
    return dict(
        version="multi_stock_research_report_v1",
        dataset_version=data["dataset_version"],
        cohort=data["generalization"]["cohort"],
        tickers=data["tickers"],
        heldout_tickers=data["generalization"]["heldout_tickers"],
        observations=data["observation_count"],
        dates=data["trading_dates"],
        first_date=data["first_date"],
        last_date=data["last_date"],
        original_rows_verified=True,
        source_pin_verified=True,
        dataset_id=plan["dataset_id"],
        selection_policy=plan["selection_policy"],
        comparison_conditions_pin=digest(
            {k: v for k, v in plan.items() if k not in ("selection_policy", "policy_parent")}
        ),
        budget=12,
        completed=len(results),
        failed=len(done) - len(results),
        locked=locked,
        heldout_scores_used_for_selection=False,
        liblinear_quarantined=True,
        memory_changed_choices=sum(
            v.get("adaptation", {}).get("choice_changed", False) for v in selections
        ),
        cache_hits=sum("cache_source" in v for v in done.values()),
        learner_seconds=sum(v["wall_seconds"] for v in costs),
        maximum_rss_kb=max((v["maximum_rss_kb"] for v in costs), default=0),
        horizons=horizons,
        historical=data.get("historical"),
        final_retrieval_basis="precommitted_robust_tuple_seen_only"
        if plan["version"] == "autonomous_plan_v6"
        else "stability_seen_cv_ap",
        transfer_hypotheses=plan.get("transfer_hypotheses", []),
        discovery_curve=discovery,
        population_comparison="different_cohorts_are_reference_only",
        survivorship_bias="UNKNOWN",
        independent_future_performance_established=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
        protected_final_test_read=False,
        krx_requests=0,
    )


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    require(
        a["locked"]
        and b["locked"]
        and a["comparison_conditions_pin"] == b["comparison_conditions_pin"]
        and a["budget"] == b["budget"]
        and a["selection_policy"] != b["selection_policy"],
        "multi_stock_policy_comparison",
    )

    def arm(v: dict[str, Any]) -> dict[str, Any]:
        return dict(
            policy=v["selection_policy"],
            completed=v["completed"],
            failed=v["failed"],
            cache_hits=v["cache_hits"],
            learner_seconds=v["learner_seconds"],
            by_horizon={
                h: dict(
                    best_seen_cv_ap=max(
                        (r["mean_seen_cv_ap"] for r in g["experiments"]), default=None
                    ),
                    mean_seen_cv_ap=st.mean(r["mean_seen_cv_ap"] for r in g["experiments"])
                    if g["experiments"]
                    else None,
                    distinct_profiles=len(
                        {digest(r["config"]["candidate"]) for r in g["experiments"]}
                    ),
                    masks=sorted({r["config"]["mask"] for r in g["experiments"]}),
                )
                for h, g in v["horizons"].items()
            },
        )

    return dict(
        version="multi_stock_policy_comparison_v1",
        conditions=a["comparison_conditions_pin"],
        budget_per_arm=a["budget"],
        arms=[arm(a), arm(b)],
        heldout_feedback_used=False,
        cache_saves_fits_not_budget=True,
        policy_superiority_established=False,
        performance_approved=False,
    )
