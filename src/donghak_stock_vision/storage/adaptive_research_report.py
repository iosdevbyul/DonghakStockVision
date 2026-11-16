"""Read-only internal comparisons over existing immutable experiment artifacts."""

import statistics as st
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, terminals


def report(root: Path, pin: str) -> dict[str, Any]:
    plan, data = context(root, pin, code_check=False)
    require(plan["version"] == "autonomous_plan_v4", "research_v2_report_owner")
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        done = terminals(store)
        results = {
            k: store.get("autonomous_result", v["result_id"])
            for k, v in done.items()
            if v["state"] == "completed"
        }
        selections = [v for _, v in store.all("autonomous_selection")]
        resources = [v for _, v in store.all("autonomous_resource_audit")]
        proposals = [v for _, v in store.all("llm_proposal")]
    horizons = {}
    for h, goal in data["goals"].items():
        rows = [r for r in results.values() if str(r["config"]["goal"]["horizon"]) == h]
        best = max(rows, key=lambda r: r["stability_ap"], default=None)

        def groups(field: str, rows: list[dict[str, Any]] = rows) -> dict[str, Any]:
            names = sorted(
                {
                    str(
                        r["config"]["candidate"]["family"]
                        if field == "family"
                        else r["config"]["mask"]
                    )
                    for r in rows
                }
            )
            return {
                name: dict(
                    experiments=sum(
                        (
                            r["config"]["candidate"]["family"]
                            if field == "family"
                            else r["config"]["mask"]
                        )
                        == name
                        for r in rows
                    ),
                    best_internal_ap=max(
                        r["mean_ap"]
                        for r in rows
                        if (
                            r["config"]["candidate"]["family"]
                            if field == "family"
                            else r["config"]["mask"]
                        )
                        == name
                    ),
                )
                for name in names
            }

        horizons[h] = dict(
            training_rows=goal["training_rows"],
            readiness=goal["readiness"],
            exclusions=goal["excluded"],
            fold_count=len(goal["folds"]),
            multiple_fold_stability_available=len(goal["folds"]) > 1,
            models=groups("family"),
            features=groups("mask"),
            best=None
            if best is None
            else dict(
                experiment_id=best["experiment_id"],
                config=best["config"],
                mean_ap=best["mean_ap"],
                overfit_gap=best["largest_train_gap"],
                fold_metrics=[f["evaluation"]["metrics"] for f in best["folds"]],
                nonoverlapping_counts=[
                    f["evaluation"]["nonoverlapping_counts"] for f in best["folds"]
                ],
                nonoverlapping_metrics=[
                    f["evaluation"]["nonoverlapping_event_metrics"] for f in best["folds"]
                ],
                baselines=[f["evaluation"]["baselines"] for f in best["folds"]],
            ),
        )
    pairs = []
    for v in selections:
        if v["state"] != "accepted":
            continue
        chosen = v["experiment_id"]
        other = str(v["rule_counterfactual"])
        a, b = results.get(chosen), results.get(other)
        comparable = a is not None and b is not None
        require(
            digest(v["comparison_view"]) == v["candidate_view_pin"], "research_comparison_view_pin"
        )
        require(
            v["comparison_view"]["candidates"][0]["experiment_id"] == other,
            "research_counterfactual_owner",
        )
        if a is not None and b is not None:
            require(a["config"]["goal"] == b["config"]["goal"], "research_comparison_horizon")
        pairs.append(
            dict(
                candidate_view_pin=v.get("candidate_view_pin"),
                llm=chosen,
                rule=other,
                per_arm_selection_budget=1,
                rule_selection_seconds=v["rule_selection_seconds"],
                llm_seconds=next(
                    (p["wall_seconds"] for p in proposals if p["request_id"] == v["request_id"]),
                    None,
                ),
                same_available_candidates_and_history=True,
                completed=comparable,
                cache_reused=chosen == other,
                llm_ap=None if a is None else a["mean_ap"],
                rule_ap=None if b is None else b["mean_ap"],
                ap_difference=None if a is None or b is None else a["mean_ap"] - b["mean_ap"],
                claim="single_paired_choice_not_policy_superiority",
            )
        )
    return dict(
        version="adaptive_research_report_v2",
        dataset_version=data["dataset_version"],
        comparison_contract=digest(
            dict(
                dataset=plan["dataset_id"], space=plan["search_space"], code=plan["code"], budget=24
            )
        ),
        source_pin_verified=True,
        observations=data["observation_count"],
        dates=data["trading_dates"],
        tickers=data["tickers"],
        first_date=data["first_date"],
        last_date=data["last_date"],
        completed=len(results),
        failed=len(done) - len(results),
        budget=24,
        remaining_budget=max(0, 24 - len(done)),
        horizons=horizons,
        memory_changed_choices=sum(
            v.get("adaptation", {}).get("choice_changed", False) for v in selections
        ),
        selections=selections,
        paired_choices=pairs,
        llm_accepted=sum(p["state"] == "accepted" for p in proposals),
        llm_rejected=sum(p["state"] != "accepted" for p in proposals),
        llm_seconds=sum(p["wall_seconds"] for p in proposals),
        learner_seconds=sum(r["wall_seconds"] for r in resources),
        max_learner_rss_kb=max((r["maximum_rss_kb"] for r in resources), default=0),
        seconds_per_valid_fit=st.mean(r["wall_seconds"] for r in resources) if resources else None,
        historical_v1_results="different_labels_features_folds_not_comparable",
        protected_final_test_read=False,
        confirmation_read=False,
        krx_requests=0,
        independent_future_performance_established=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
