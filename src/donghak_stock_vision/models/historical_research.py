"""Versioned chronological discovery policy; no excluded-symbol/diagnostic feedback."""

import statistics as st
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.models import research_v2, stock_generalization
from donghak_stock_vision.models.experiment import Fold

SPACE = "historical_cross_period_v1"
POLICIES = ("adaptive", "predeclared", "robust")
PRIORITIES = [
    "sufficient_folds",
    "baseline_and_calibration_passes",
    "worst_balanced_accuracy",
    "worst_baseline_ap_gain",
    "negative_balanced_accuracy_dispersion",
    "worst_sufficient_regime_balanced_accuracy",
    "negative_overfit_gap",
]


def folds(snapshot: dict[str, Any], spec: dict[str, Any], h: int) -> tuple[Fold, ...]:
    days = sorted({r["trading_date"] for rows in snapshot["rows"].values() for r in rows})
    result = (
        list(
            research_v2.folds(
                snapshot,
                ExplicitResearchSplit.from_dict(
                    snapshot["real_market_source"]["request"]["access_split"]
                ),
                h,
            )
        )
        if spec.get("include_original_internal_folds", False)
        else []
    )
    for period in spec["evaluations"]:
        earlier = [d for d in days if d < period["start"]]
        require(len(earlier) > h + 252, "historical_training_history")
        # Purging remains owned by purged_fold; embargo excludes h observed input sessions.
        result.append(
            Fold(
                ResearchDateRange(date.fromisoformat(days[0]), date.fromisoformat(earlier[-h - 1])),
                ResearchDateRange(
                    date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
                ),
            )
        )
    return tuple(result)


def summary(folds: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations = [f["evaluation"] for f in folds]
    baselines = [max(b["pr_auc_ap"] for b in e["baselines"].values()) for e in evaluations]
    gaps = [
        f["train"]["balanced_accuracy"] - e["metrics"]["balanced_accuracy"]
        for f, e in zip(folds, evaluations, strict=True)
    ]
    sufficient = [
        e["counts"]["dates"] >= 20 and min(e["counts"]["positive"], e["counts"]["negative"]) >= 10
        for e in evaluations
    ]
    regimes = [
        r["metrics"]["balanced_accuracy"]
        for e in evaluations
        for r in e["seen_robustness"]["input_regimes"].values()
        if r["status"] == "internal_descriptive_sufficient"
    ]
    passes = sum(
        ok
        and e["metrics"]["pr_auc_ap"] > baseline
        and e["metrics"]["balanced_accuracy"] >= 0.5
        and gap <= 0.15
        and e["metrics"]["brier_score"] <= e["baselines"]["training_prior"]["brier_score"]
        for ok, e, baseline, gap in zip(sufficient, evaluations, baselines, gaps, strict=True)
    )
    return dict(
        version="historical_robust_lexicographic_v1",
        sufficient_folds=sum(sufficient),
        fold_count=len(folds),
        passes=passes,
        worst_balanced_accuracy=min(e["metrics"]["balanced_accuracy"] for e in evaluations),
        worst_baseline_ap_gain=min(
            e["metrics"]["pr_auc_ap"] - b for e, b in zip(evaluations, baselines, strict=True)
        ),
        negative_dispersion=-st.pstdev(e["metrics"]["balanced_accuracy"] for e in evaluations),
        worst_regime_balanced_accuracy=min(regimes, default=0),
        negative_overfit_gap=-max(gaps),
        heldout_or_diagnostic_used=False,
    )


def priority(s: dict[str, Any]) -> tuple[float, ...]:
    require(
        s["version"] == "historical_robust_lexicographic_v1"
        and not s["heldout_or_diagnostic_used"],
        "historical_summary_owner",
    )
    return tuple(
        float(s[k])
        for k in (
            "sufficient_folds",
            "passes",
            "worst_balanced_accuracy",
            "worst_baseline_ap_gain",
            "negative_dispersion",
            "worst_regime_balanced_accuracy",
            "negative_overfit_gap",
        )
    )


def selection(plan: dict[str, Any], done: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    policy = plan["selection_policy"]
    base = stock_generalization.selection(
        plan | dict(selection_policy="predeclared" if policy == "predeclared" else "adaptive"), done
    )
    if base is None or policy != "robust":
        return base
    cfg = base["config"]
    history = [
        v | dict(experiment_id=k)
        for k, v in done.items()
        if v["config"]["goal"]["horizon"] == cfg["goal"]["horizon"]
        and v["config"]["stock_policy"] == cfg["stock_policy"]
    ]
    choices: list[dict[str, Any]] = [
        c
        for c in plan["configurations"]
        if c["experiment_id"] not in done
        and c["config"]["goal"]["horizon"] == cfg["goal"]["horizon"]
        and c["config"]["stock_policy"] == cfg["stock_policy"]
    ]
    successful = [v for v in history if v["state"] == "completed"]

    def rank(c: dict[str, Any], transfer: bool) -> tuple[Any, ...]:
        config = c["config"]
        family = config["candidate"]["family"]
        matches = [
            v
            for v in successful
            if v["config"]["mask"] == config["mask"] or v["config"]["candidate"]["family"] == family
        ]
        evidence = max((priority(v["robust_summary"]) for v in matches), default=(0.0,) * 7)
        failed = any(
            v["state"] == "failed" and v["config"]["candidate"] == config["candidate"]
            for v in history
        )
        complexity = config["candidate"]["value"]
        transfer_match = transfer and any(
            config == h["config"] for h in plan["transfer_hypotheses"]
        )
        return (
            not failed,
            *evidence,
            not any(v["config"]["candidate"]["family"] == family for v in successful),
            transfer_match,
            -complexity,
        )

    chosen = max(choices, key=lambda c: rank(c, True))
    without = max(choices, key=lambda c: rank(c, False))
    parent = max(successful, key=lambda v: priority(v["robust_summary"]), default=None)
    return chosen | dict(
        selection_reason="historical_robust_lexicographic_v1",
        parent_experiment=None if parent is None else parent["experiment_id"],
        adaptation=dict(
            priorities=PRIORITIES,
            selected_with_memory=chosen["experiment_id"],
            selected_without_memory=min(choices, key=lambda c: c["config"]["candidate"]["value"])[
                "experiment_id"
            ],
            choice_changed=chosen["experiment_id"] != base["experiment_id"],
            original_adaptive_choice=base["experiment_id"],
            transfer_changed_choice=chosen["experiment_id"] != without["experiment_id"],
            history_ids=sorted(v["experiment_id"] for v in history),
            heldout_metrics_used=False,
            diagnostic_metrics_used=False,
            reference_scores_used_as_current_evidence=False,
            alternatives=[
                c["experiment_id"]
                for c in sorted(choices, key=lambda c: rank(c, True), reverse=True)[1:4]
            ],
        ),
    )


def curve(done: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Actual completion/adoption order, with separate incomparable horizon populations."""
    best: dict[str, float] = {}
    result = []
    for i, v in enumerate(
        sorted(done.values(), key=lambda x: x.get("adopted_at", x.get("finished_at", "")))
    ):
        if v["state"] != "completed":
            continue
        key = str(v["config"]["goal"]["horizon"]) + "/" + v["config"]["stock_policy"]
        best[key] = max(best.get(key, 0), v["mean_ap"])
        result.append(
            dict(
                selection=i + 1,
                experiment_id=v["experiment_id"],
                best_seen_ap_by_population=dict(best),
                robust_summary=v.get("robust_summary"),
                cache_reused="cache_source" in v,
                heldout_or_diagnostic_used=False,
            )
        )
    return result


def deferred_assessment(
    rows: list[Any], scores: list[float], held: set[str], unseen: bool
) -> dict[str, Any]:
    """Same nonoverlapping calendar rule as search folds; not independent authority."""
    days = sorted({s.trading_date for s in rows})
    chosen = set()
    end = None
    for day in days:
        if end is None or day > end:
            chosen.add(day)
            end = max(
                s.label_end for s in rows if s.trading_date == day and s.label_end is not None
            )
    pairs = [(s, p) for s, p in zip(rows, scores, strict=True) if s.trading_date in chosen]
    return dict(
        calendar_dates=[d.isoformat() for d in sorted(chosen)],
        assessment=stock_generalization.assessments(
            [s for s, _ in pairs], [p for _, p in pairs], held, unseen
        ),
    )
