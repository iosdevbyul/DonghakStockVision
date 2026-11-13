"""Read-only descriptive diagnostics around the unchanged Phase 2 metrics.

All scores refer to conditional reversal-barrier events, not calibrated price
probabilities. Temporal groups use anchor dates, never select or refit parameters.
"""

import statistics
from datetime import date
from typing import Any

from donghak_stock_vision.data.learning import FEATURES, THRESHOLD, Direction, Minimums, Sample
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.metrics import baseline_scores, evaluate_metrics
from donghak_stock_vision.signals.dataset import population


def metrics(labels: list[int], scores: list[float]) -> dict[str, Any]:
    result = evaluate_metrics(labels, scores)
    result.pop("calibration_bins")  # Keep the original report/identifiers untouched.
    c = result["confusion_matrix"]
    n, positives, negatives = result["n"], result["positive"], result["negative"]
    result["accuracy"] = (c["tp"] + c["tn"]) / n if n else None
    result["balanced_accuracy"] = (
        (c["tp"] / positives + c["tn"] / negatives) / 2 if positives and negatives else None
    )
    denominator = 2 * c["tp"] + c["fp"] + c["fn"]
    result["f1"] = 2 * c["tp"] / denominator if denominator else None
    auc = None
    if positives and negatives:
        # Mann–Whitney concordance with half credit for ties; O(n log n).
        groups: dict[float, list[int]] = {}
        for label, value in zip(labels, scores, strict=True):
            groups.setdefault(value, []).append(label)
        lower_negative, concordance = 0, 0.0
        for value in sorted(groups):
            p, m = sum(groups[value]), len(groups[value]) - sum(groups[value])
            concordance += p * (lower_negative + 0.5 * m)
            lower_negative += m
        auc = concordance / (positives * negatives)
    result["roc_auc"] = auc
    for name in ("accuracy", "balanced_accuracy", "f1", "roc_auc"):
        if result[name] is None:
            result["undefined_reasons"][name] = (
                "empty_sample"
                if not n
                else "no_positive_or_predicted_positive"
                if name == "f1"
                else "single_class"
            )
    return result


def _shift(rows: list[Sample], training: list[Sample]) -> dict[str, Any]:
    values = {}
    for i, name in enumerate(FEATURES):
        base = [s.values[i] for s in training]
        selected = [s.values[i] for s in rows]
        std = statistics.pstdev(base) if base else None
        values[name] = dict(
            training_mean=statistics.mean(base) if base else None,
            selected_mean=statistics.mean(selected) if selected else None,
            training_std=std,
            standardized_mean_shift=(statistics.mean(selected) - statistics.mean(base)) / std
            if selected and std
            else None,
        )
    return values


def _bucket(
    rows: list[Sample],
    training: list[Sample],
    direction: Direction,
    parameters: dict[str, Any],
    prior: float,
) -> dict[str, Any]:
    scores = [score(parameters, s.values)[0] for s in rows]
    labels = [int(s.label) for s in rows if s.label is not None]
    measured = metrics(labels, scores)
    baselines = baseline_scores(rows, direction, prior)
    # Fixed from Training, NOT an oracle selected using this evaluation bucket.
    baselines["training_majority"] = [float(prior >= THRESHOLD)] * len(rows)
    comparison = {name: metrics(labels, values) for name, values in baselines.items()}
    ap = measured["pr_auc_ap"]
    eligible = [
        comparison[name]["pr_auc_ap"]
        for name in ("prior", "reversal_strength")
        if comparison[name]["pr_auc_ap"] is not None
    ]
    best = max(eligible) if eligible else None
    outcomes: dict[str, list[Sample]] = {name: [] for name in ("tp", "tn", "fp", "fn")}
    for sample, value in zip(rows, scores, strict=True):
        name = (
            ("tp" if sample.label else "fp")
            if value >= THRESHOLD
            else ("fn" if sample.label else "tn")
        )
        outcomes[name].append(sample)
    minimum = Minimums()
    return dict(
        metrics=measured,
        baselines=comparison,
        ap_minus_best_existing_baseline=ap - best if ap is not None and best is not None else None,
        dates=len({s.trading_date for s in rows}),
        interpretation_limited=(
            len(rows) < minimum.holdout_rows
            or len({s.trading_date for s in rows}) < minimum.holdout_dates
            or min(measured["positive"], measured["negative"]) < minimum.holdout_class
        ),
        failure_patterns={
            name: dict(
                count=len(selected),
                mean_score=statistics.mean(score(parameters, s.values)[0] for s in selected)
                if selected
                else None,
                mean_features={
                    feature: statistics.mean(s.values[i] for s in selected) if selected else None
                    for i, feature in enumerate(FEATURES)
                },
            )
            for name, selected in outcomes.items()
        },
    )


def _periods(start: date, end: date) -> list[tuple[str, str]]:
    current, periods = date(start.year, start.month, 1), []
    while current <= end:
        periods.append(("month", current.strftime("%Y-%m")))
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    periods += [
        ("quarter", f"{year}-Q{q}")
        for year in range(start.year, end.year + 1)
        for q in range(1, 5)
        if (year, q) >= (start.year, (start.month - 1) // 3 + 1)
        and (year, q) <= (end.year, (end.month - 1) // 3 + 1)
    ]
    return periods


def diagnose(
    model: dict[str, Any], dataset: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    split = dataset["split"]
    config = split["configuration"]
    start, end = (date.fromisoformat(config["final_test"][key]) for key in ("start", "end"))
    results: dict[str, Any] = {}
    temporal = []
    for direction in ("up", "down"):
        d: Direction = "up" if direction == "up" else "down"
        fitted = model["directions"][d]
        if fitted["parameters"] is None:
            raise ValueError("diagnostic_direction_not_fitted")
        training = population(split, d, "train")
        parts = {}
        for part in ("train", "validation", "test"):
            rows = population(split, d, part)
            bucket = _bucket(rows, training, d, fitted["parameters"], fitted["prior"])
            bucket["feature_distribution"] = _shift(rows, training)
            parts["diagnostic" if part == "test" else part] = bucket
        rows = population(split, d, "test")
        by_ticker = {
            ticker: _bucket(
                [s for s in rows if s.ticker == ticker],
                training,
                d,
                fitted["parameters"],
                fitted["prior"],
            )
            for ticker in sorted(snapshot["rows"])
        }
        for kind, period in _periods(start, end):
            selected = [
                s
                for s in rows
                if (
                    s.trading_date.strftime("%Y-%m")
                    if kind == "month"
                    else f"{s.trading_date.year}-Q{(s.trading_date.month - 1) // 3 + 1}"
                )
                == period
            ]
            for ticker in ("ALL", *sorted(snapshot["rows"])):
                values = (
                    selected if ticker == "ALL" else [s for s in selected if s.ticker == ticker]
                )
                temporal.append(
                    dict(
                        direction=d,
                        period_kind=kind,
                        period=period,
                        ticker=ticker,
                        **_bucket(values, training, d, fitted["parameters"], fitted["prior"]),
                    )
                )
        margin = parts["diagnostic"]["ap_minus_best_existing_baseline"]
        reference_margin = model["training_policy"]["qualification_ap_margin"]
        viable = [
            (ticker, bucket["ap_minus_best_existing_baseline"])
            for ticker, bucket in by_ticker.items()
            if bucket["ap_minus_best_existing_baseline"] is not None
        ]
        summary = dict(
            validation_to_diagnostic_ap_change=parts["diagnostic"]["metrics"]["pr_auc_ap"]
            - parts["validation"]["metrics"]["pr_auc_ap"]
            if all(
                parts[p]["metrics"]["pr_auc_ap"] is not None for p in ("validation", "diagnostic")
            )
            else None,
            diagnostic_accuracy_minus_training_majority=parts["diagnostic"]["metrics"]["accuracy"]
            - parts["diagnostic"]["baselines"]["training_majority"]["accuracy"]
            if parts["diagnostic"]["metrics"]["accuracy"] is not None
            else None,
            tickers_above_best_existing_ap_baseline=sum(value > 0 for _, value in viable),
            tickers_with_defined_ap=len(viable),
            ticker_ap_margins=dict(sorted(viable, key=lambda item: item[1])),
            causal_explanation="not_established_descriptive_associations_only",
        )
        results[d] = dict(
            partitions=parts,
            by_ticker=by_ticker,
            original_validation_status=fitted["evaluation_status"],
            descriptive_comparison=summary,
            existing_validation_ap_margin=reference_margin,
            diagnostic_ap_margin_at_least_existing_validation_margin=margin >= reference_margin
            if margin is not None
            else None,
            approval_effect="none_retrospective_diagnostic_only",
        )
    return dict(
        version="real_model_diagnostics_v1",
        directions=results,
        temporal=temporal,
        threshold=THRESHOLD,
        calibration="none",
        score_semantics="conditional_reversal_barrier_research_score_not_price_probability",
        temporal_basis="anchor_date_labels_may_end_in_next_month_within_diagnostic_window",
        baselines=dict(
            prior="existing_training_prior",
            reversal_strength="existing_phase2_baseline",
            training_majority="training_prior_majority_diagnostic_not_approval_standard",
        ),
        market_regime=dict(
            status="not_available", reason="no_verified_benchmark_index_series_in_frozen_inputs"
        ),
        performance_approved=False,
        performance_metrics=None,
        pit_qualified=False,
        independent_publication_time_verified=False,
        execution_authorized=False,
    )
