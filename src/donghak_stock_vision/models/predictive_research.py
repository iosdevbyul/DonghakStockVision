"""Fixed comparisons and exploratory selection for the R3 development window."""

from typing import Any

from donghak_stock_vision.models.failure_feedback import evidence, paired, readout

HORIZONS = (1, 5, 20)
SIZES = (8, 16, 24)
POLICY = dict(
    minimum_evaluation_rows=200,
    minimum_evaluation_dates=40,
    minimum_evaluation_class_rows=20,
    balanced_accuracy_margin=0.005,
    accuracy_regression_tolerance=0.0,
    ap_regression_tolerance=0.0,
    brier_regression_tolerance=0.0,
    maximum_h5_fpr_increase=0.02,
    maximum_h5_recall_loss=0.05,
)


def summary(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    dates = {r["input_date"] for r in rows}
    evidence_counts = evidence(rows)
    measured = readout(rows, arm)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        for group in (
            ("ticker", row["ticker"]),
            ("month", row["input_date"][:7]),
            *(("regime", name) for name in row["regimes"]),
        ):
            groups.setdefault(group, []).append(row)
    breakdown: dict[str, dict[str, Any]] = {}
    for (kind, group_name), group_rows in groups.items():
        breakdown.setdefault(kind, {})[group_name] = {
            "metrics": readout(group_rows, arm),
            "evidence": evidence(group_rows),
        }
    return dict(
        metrics=measured,
        trading_dates=len(dates),
        evidence=evidence_counts,
        breakdown=breakdown,
    )


def meets_population(value: dict[str, Any]) -> bool:
    e = value["evidence"]
    return bool(
        e["status"] == "READY_FOR_EXISTING_EVALUATOR"
        and e["valid_rows"] >= POLICY["minimum_evaluation_rows"]
        and min(e["positive_rows"], e["negative_rows"]) >= POLICY["minimum_evaluation_class_rows"]
        and value["trading_dates"] >= POLICY["minimum_evaluation_dates"]
    )


def direction_guard(left: dict[str, Any], right: dict[str, Any], horizon: int) -> bool:
    return bool(
        right["up_predictions"] > 0
        and right["down_predictions"] > 0
        and (
            horizon != 5
            or (
                right["fpr"] <= left["fpr"] + POLICY["maximum_h5_fpr_increase"]
                and right["recall"] >= left["recall"] - POLICY["maximum_h5_recall_loss"]
            )
        )
    )


def select(
    candidates: list[dict[str, Any]],
    horizon: int,
    control: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Use only development results, including an exploratory fallback."""
    baseline = control["summary"]["metrics"]
    eligible = [
        trial
        for trial in candidates
        if trial["summary"]["metrics"]["balanced_accuracy"] is not None
        and direction_guard(baseline, trial["summary"]["metrics"], horizon)
    ]
    qualified = [
        trial
        for trial in eligible
        if trial["experiment_id"] != control["experiment_id"]
        and trial["summary"]["metrics"]["balanced_accuracy"] >= baseline["balanced_accuracy"]
        and trial["summary"]["metrics"]["accuracy"] >= baseline["accuracy"]
        and trial["summary"]["metrics"]["pr_auc_ap"] >= baseline["pr_auc_ap"]
        and trial["summary"]["metrics"]["brier_score"] <= baseline["brier_score"]
    ]

    def priority(trial: dict[str, Any]) -> tuple[float, float, float, str]:
        m = trial["summary"]["metrics"]
        return -m["balanced_accuracy"], m["brier_score"], -m["pr_auc_ap"], trial["experiment_id"]

    if qualified:
        return min(qualified, key=priority), "development_non_regression"
    novel = [v for v in eligible if v["experiment_id"] != control["experiment_id"]]
    if novel:
        return min(novel, key=priority), "exploratory_development_fallback"
    return control, "no_direction_eligible_challenger"


def compare(
    rows: list[dict[str, Any]],
    measured: dict[str, Any],
    left: str,
    right: str,
    horizon: int,
) -> dict[str, Any]:
    a, b = measured[left]["metrics"], measured[right]["metrics"]
    names = ("accuracy", "balanced_accuracy", "pr_auc_ap", "brier_score", "fpr", "fdr", "recall")
    delta = {k: b[k] - a[k] if a[k] is not None and b[k] is not None else None for k in names}
    status = "INSUFFICIENT_EVIDENCE"
    ba, accuracy, ap, brier = (delta[k] for k in names[:4])
    if meets_population(measured[right]) and (
        ba is not None and accuracy is not None and ap is not None and brier is not None
    ):
        status = "INTERNAL_NO_CLEAR_ADVANTAGE"
        if (
            ba >= POLICY["balanced_accuracy_margin"]
            and accuracy >= -POLICY["accuracy_regression_tolerance"]
            and ap >= -POLICY["ap_regression_tolerance"]
            and brier <= POLICY["brier_regression_tolerance"]
            and direction_guard(a, b, horizon)
        ):
            status = "INTERNAL_IMPROVEMENT_OBSERVED"
        elif ba <= -POLICY["balanced_accuracy_margin"] and accuracy <= 0 and ap <= 0 and brier >= 0:
            status = "INTERNAL_REGRESSION_OBSERVED"
    return dict(delta=delta, status=status, transitions=paired(rows, left, right))
