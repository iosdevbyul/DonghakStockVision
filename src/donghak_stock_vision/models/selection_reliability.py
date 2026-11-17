"""Pre-analysis selection stability and conservative paired historical assessment."""

import math
import random
import statistics as st
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.models.historical_research import priority

POLICY: dict[str, Any] = dict(
    version="selection_stability_v1",
    minimum_folds=4,
    minimum_nonoverlapping_dates=20,
    minimum_fold_dates=40,
    maximum_train_gap=0.10,
    maximum_ba_dispersion=0.05,
    minimum_mean_ba=0.50,
    minimum_median_baseline_ap_gain=0.005,
    minimum_worst_baseline_ap_gain=-0.02,
    maximum_mean_brier_gap=0.0,
    minimum_leave_one_period_out_agreement=0.75,
    minimum_ap_margin=0.005,
    fallback="abstain_with_frozen_training_prior_for_same_population_comparison",
    bootstrap_replicates=1000,
    bootstrap_seed=247,
    confidence=0.95,
    familywise_comparisons=18,
    minimum_assessment_calendar_anchors=20,
    ba_noninferiority=-0.01,
    q3_diagnostic_used=False,
    independent_future_performance_established=False,
)


def evidence(e: dict[str, Any], indices: list[int] | None = None) -> dict[str, Any]:
    rows = e["fold_seen_metrics"]
    indices = list(range(len(rows))) if indices is None else indices
    require(bool(indices), "selection_empty_fold_evidence")
    metrics = [rows[i] for i in indices]
    gains = [
        rows[i]["pr_auc_ap"] - max(b["pr_auc_ap"] for b in e["fold_baselines"][i].values())
        for i in indices
    ]
    brier = [
        rows[i]["brier_score"] - e["fold_baselines"][i]["training_prior"]["brier_score"]
        for i in indices
    ]
    require(
        all(
            math.isfinite(float(x))
            for m in metrics
            for x in (m["balanced_accuracy"], m["pr_auc_ap"], m["brier_score"])
        ),
        "selection_nonfinite_metrics",
    )
    dates = {d for i in indices for d in e["nonoverlapping"][i]["calendar_dates"]}
    return dict(
        folds=len(indices),
        nonoverlapping_dates=len(dates),
        minimum_fold_dates=min(
            e["fold_generalization"][i]["seen_symbols"]["counts"]["dates"] for i in indices
        ),
        mean_ap=st.mean(m["pr_auc_ap"] for m in metrics),
        mean_ba=st.mean(m["balanced_accuracy"] for m in metrics),
        worst_ba=min(m["balanced_accuracy"] for m in metrics),
        ba_dispersion=st.pstdev(m["balanced_accuracy"] for m in metrics),
        median_baseline_ap_gain=st.median(gains),
        worst_baseline_ap_gain=min(gains),
        mean_brier_gap=st.mean(brier),
        train_gap=e["overfit_gap"],
        excluded_stock_or_q3_scores_used=False,
    )


def rank(e: dict[str, Any], indices: list[int] | None = None) -> tuple[Any, ...]:
    v = evidence(e, indices)
    return (
        v["worst_baseline_ap_gain"],
        v["worst_ba"],
        -v["mean_brier_gap"],
        -v["ba_dispersion"],
        -v["train_gap"],
        e["experiment_id"],
    )


def select(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    require(bool(experiments) and len(experiments) <= 12, "selection_candidate_bound")
    first = experiments[0]["config"]
    require(
        all(
            e["config"]["goal"] == first["goal"]
            and e["config"]["stock_policy"] == first["stock_policy"]
            for e in experiments
        ),
        "selection_incomparable_population",
    )
    folds = len(experiments[0]["fold_seen_metrics"])
    require(
        all(len(e["fold_seen_metrics"]) == folds for e in experiments),
        "selection_incomparable_folds",
    )
    original = max(experiments, key=lambda e: priority(e["robust_summary"]))
    maximum = max(experiments, key=lambda e: (e["mean_seen_cv_ap"], e["experiment_id"]))
    proposed = max(experiments, key=rank)
    v = evidence(proposed)
    votes = (
        [
            max(experiments, key=lambda e: rank(e, [i for i in range(folds) if i != held]))[
                "experiment_id"
            ]
            for held in range(folds)
        ]
        if folds >= 2
        else []
    )
    agreement = sum(x == proposed["experiment_id"] for x in votes) / len(votes) if votes else 0.0
    others = [e for e in experiments if e["experiment_id"] != proposed["experiment_id"]]
    margin = v["mean_ap"] - max((e["mean_seen_cv_ap"] for e in others), default=v["mean_ap"])
    checks = dict(
        folds=v["folds"] >= POLICY["minimum_folds"],
        dates=v["nonoverlapping_dates"] >= POLICY["minimum_nonoverlapping_dates"],
        fold_dates=v["minimum_fold_dates"] >= POLICY["minimum_fold_dates"],
        overfit=v["train_gap"] <= POLICY["maximum_train_gap"],
        dispersion=v["ba_dispersion"] <= POLICY["maximum_ba_dispersion"],
        ba=v["mean_ba"] >= POLICY["minimum_mean_ba"],
        baseline_median=v["median_baseline_ap_gain"] >= POLICY["minimum_median_baseline_ap_gain"],
        baseline_worst=v["worst_baseline_ap_gain"] >= POLICY["minimum_worst_baseline_ap_gain"],
        calibration=v["mean_brier_gap"] <= POLICY["maximum_mean_brier_gap"],
        rank_stability=agreement >= POLICY["minimum_leave_one_period_out_agreement"],
        margin=margin >= POLICY["minimum_ap_margin"],
    )
    accepted = all(checks.values())
    return dict(
        version=POLICY["version"],
        original_group_selection=original["experiment_id"],
        highest_search_ap=maximum["experiment_id"],
        proposed_candidate=proposed["experiment_id"],
        stable_selection=proposed["experiment_id"] if accepted else None,
        state="SELECTED" if accepted else "ABSTAIN",
        reasons=[k for k, v in checks.items() if not v],
        evidence=v,
        leave_one_period_out_votes=votes,
        rank_agreement=agreement,
        ap_margin=margin,
        fallback=POLICY["fallback"],
        q3_used=False,
        heldout_scores_used=False,
    )


def paired_gain(values: list[float], cohort_gains: list[float], ba_delta: float) -> dict[str, Any]:
    require(
        all(math.isfinite(v) for v in values + cohort_gains + [ba_delta]),
        "selection_nonfinite_assessment",
    )
    n = len(values)
    interval = None
    if n >= 4:
        block = max(2, math.ceil(math.sqrt(n)))
        rng = random.Random(POLICY["bootstrap_seed"])
        means = []
        for _ in range(POLICY["bootstrap_replicates"]):
            chosen: list[float] = []
            while len(chosen) < n:
                start = rng.randrange(n)
                chosen.extend(values[(start + j) % n] for j in range(block))
            means.append(st.mean(chosen[:n]))
        means.sort()
        tail = (1 - POLICY["confidence"]) / (2 * POLICY["familywise_comparisons"])
        interval = [
            means[int(tail * len(means))],
            means[min(len(means) - 1, int((1 - tail) * len(means)))],
        ]
    enough = n >= POLICY["minimum_assessment_calendar_anchors"] and len(cohort_gains) >= 2
    gain = (
        enough
        and interval is not None
        and interval[0] > 0
        and all(v > 0 for v in cohort_gains)
        and ba_delta >= POLICY["ba_noninferiority"]
    )
    return dict(
        calendar_anchors=n,
        mean_brier_improvement=st.mean(values) if values else None,
        interval_familywise_95=interval,
        familywise_comparisons=POLICY["familywise_comparisons"],
        moving_block_length=max(2, math.ceil(math.sqrt(n))) if n else None,
        cohort_brier_improvements=cohort_gains,
        ba_delta=ba_delta,
        status="LOCAL_PAIRED_GAIN" if gain else "IMPROVEMENT_NOT_ESTABLISHED",
        sufficient_calendar_evidence=enough,
        performance_approved=False,
        pit_qualified=False,
        execution_authorized=False,
        independent_future_performance_established=False,
    )
