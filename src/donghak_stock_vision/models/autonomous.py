"""Pure bounded experiment execution using the existing research fitter/metrics."""

import statistics
from dataclasses import replace
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.data.research_split import ResearchDateRange
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.directional import MASKS
from donghak_stock_vision.models.experiment import (
    Candidate,
    Fold,
    estimator_bytes,
    fit_candidate,
    predict_candidate,
    purged_fold,
)
from donghak_stock_vision.models.linear import sigmoid
from donghak_stock_vision.models.metrics import evaluate_metrics
from donghak_stock_vision.signals.dataset import counts


def masked(rows: list[Sample], name: str) -> list[Sample]:
    from donghak_stock_vision.features.research import GROUPS

    masks = MASKS | {"v2_" + k: v for k, v in GROUPS.items()}
    require(name in masks, "autonomous_feature_mask")
    return [
        replace(s, values=tuple(v if i in masks[name] else 0.0 for i, v in enumerate(s.values)))
        for s in rows
    ]


def measure(
    rows: list[Sample], scores: list[float], training: list[Sample], returns: dict[str, float]
) -> dict[str, Any]:
    require(bool(rows) and {s.label for s in rows} == {0, 1}, "autonomous_metric_population")
    labels = [int(s.label) for s in rows if s.label is not None]
    prior = sum(s.label == 1 for s in training) / len(training)
    result = metrics(labels, scores)
    bins = evaluate_metrics(labels, scores)["calibration_bins"]
    result["calibration_bins"] = bins
    result["expected_calibration_error"] = sum(
        b["count"] / len(rows) * abs(b["mean_score"] - b["positive_fraction"])
        for b in bins
        if b["count"]
    )
    baseline_scores = {
        "training_prior": [prior] * len(rows),
        "training_majority": [float(prior >= 0.5)] * len(rows),
        "momentum": [sigmoid(s.values[2] / s.values[4]) for s in rows],
    }
    comparison = {k: metrics(labels, v) for k, v in baseline_scores.items()}
    # Research-only overlapping event means, not an executable portfolio return.
    net = [
        (returns[digest(s.to_dict())] - 0.002) if p >= 0.5 else 0.0
        for s, p in zip(rows, scores, strict=True)
    ]
    return dict(
        metrics=result,
        baselines=comparison,
        counts=counts(rows),
        prediction_pin=digest(list(zip([digest(s.to_dict()) for s in rows], scores, strict=True))),
        by_ticker={
            t: metrics(
                [labels[i] for i, s in enumerate(rows) if s.ticker == t],
                [p for s, p in zip(rows, scores, strict=True) if s.ticker == t],
            )
            for t in sorted({s.ticker for s in rows})
        },
        by_quarter={
            q: metrics(
                [
                    labels[i]
                    for i, s in enumerate(rows)
                    if f"{s.trading_date.year}-Q{(s.trading_date.month - 1) // 3 + 1}" == q
                ],
                [
                    p
                    for s, p in zip(rows, scores, strict=True)
                    if f"{s.trading_date.year}-Q{(s.trading_date.month - 1) // 3 + 1}" == q
                ],
            )
            for q in sorted(
                {f"{s.trading_date.year}-Q{(s.trading_date.month - 1) // 3 + 1}" for s in rows}
            )
        },
        cost_scenario=dict(
            round_trip_bps=20,
            mean_net_event_return=statistics.mean(net),
            compounded_portfolio_return=None,
            overlapping_events=True,
            execution_available=False,
        ),
    )


def execute(
    dataset: dict[str, Any], config: dict[str, Any], *, gate: bool = False
) -> tuple[dict[str, Any], bytes]:
    horizon = str(config["goal"]["horizon"])
    value = dataset["goals"][horizon]
    historical = dataset["version"] == "direction_dataset_v4"
    multi = historical or dataset["version"] == "direction_dataset_v3"
    names = (
        config["goal"].get("features")
        if dataset["version"]
        in ("direction_dataset_v2", "direction_dataset_v3", "direction_dataset_v4")
        else None
    )
    heldout_symbols = set(dataset["generalization"]["heldout_tickers"]) if multi else set()
    if multi:
        from donghak_stock_vision.models import stock_generalization

        require(not gate, "multi_stock_confirmation_sealed")
        require(
            config["candidate"].get("parameters", {}).get("solver") != "liblinear",
            "multi_liblinear_quarantined",
        )
        require(
            config in stock_generalization.catalog(sorted(int(h) for h in dataset["goals"])),
            "multi_stock_configuration",
        )
    unseen = multi and config["stock_policy"] == "heldout"
    samples = [Sample.from_dict(s) for s in value["samples"]]
    returns = value["returns"]
    span = ResearchDateRange(
        date.fromisoformat(dataset["split"]["training"]["start"]),
        date.fromisoformat(
            dataset["historical"]["search_end"]
            if historical
            else dataset["split"]["training"]["end"]
        ),
    )
    training = [
        s
        for s in samples
        if s.label_end is not None and span.start <= s.trading_date <= s.label_end <= span.end
    ]
    if unseen:
        training = [s for s in training if s.ticker not in heldout_symbols]
    candidate = Candidate(**config["candidate"])
    folds: list[dict[str, Any]] = []
    if not gate:
        for f in value.get("folds", dataset["folds"]):
            fold = Fold(
                ResearchDateRange(**{k: date.fromisoformat(v) for k, v in f["training"].items()}),
                ResearchDateRange(**{k: date.fromisoformat(v) for k, v in f["evaluation"].items()}),
            )
            train, holdout, purged = purged_fold(samples, fold, "up")
            full_holdout = holdout
            if historical:
                train = [
                    s for s in train if s.label_end is not None and s.label_end <= fold.training.end
                ]
            if multi:
                holdout = [s for s in holdout if s.ticker not in heldout_symbols]
                if unseen:
                    train = [s for s in train if s.ticker not in heldout_symbols]
                    require(
                        not ({s.ticker for s in train} & heldout_symbols),
                        "multi_stock_training_leak",
                    )
                require(
                    all(
                        s.label_end is not None and s.label_end < fold.evaluation.start
                        for s in train
                    ),
                    "multi_stock_time_leak",
                )
            require(
                counts(train)["dates"] >= 252
                and counts(train)["rows"] >= 1000
                and counts(holdout)["rows"] >= 200
                and counts(holdout)["dates"] >= 40
                and min(
                    counts(train)["positive"],
                    counts(train)["negative"],
                    counts(holdout)["positive"],
                    counts(holdout)["negative"],
                )
                >= 20,
                "autonomous_fold_population",
            )
            fitted = fit_candidate(candidate, masked(train, config["mask"]), feature_names=names)
            validation_scores = predict_candidate(
                candidate, fitted, masked(holdout, config["mask"])
            )
            evaluated = measure(
                holdout,
                validation_scores,
                train,
                returns,
            )
            if historical:
                evaluated["seen_robustness"] = stock_generalization.assessments(
                    holdout, validation_scores, set(), False
                )
            if multi:
                full_scores = predict_candidate(
                    candidate, fitted, masked(full_holdout, config["mask"])
                )
                evaluated["stock_generalization"] = stock_generalization.assessments(
                    full_holdout, full_scores, heldout_symbols, unseen
                )
                evaluated["selection_population"] = (
                    "seen_symbols_only_heldout_scores_not_search_inputs"
                )
            if dataset["version"] in (
                "direction_dataset_v2",
                "direction_dataset_v3",
                "direction_dataset_v4",
            ):
                # One common calendar stride for all tickers avoids overlapping
                # event windows; cross-sectional dependence remains unqualified.
                calendar_rows = full_holdout if multi else holdout
                days = sorted({s.trading_date for s in calendar_rows})
                selected_days = set()
                last_end = None
                for day in days:
                    if last_end is None or day > last_end:
                        selected_days.add(day)
                        last_end = max(
                            s.label_end
                            for s in calendar_rows
                            if s.trading_date == day and s.label_end is not None
                        )
                independent_rows = [s for s in holdout if s.trading_date in selected_days]
                independent_scores = predict_candidate(
                    candidate, fitted, masked(independent_rows, config["mask"])
                )
                evaluated["nonoverlapping_event_metrics"] = metrics(
                    [int(s.label) for s in independent_rows if s.label is not None],
                    independent_scores,
                )
                evaluated["nonoverlapping_counts"] = counts(independent_rows)
                if multi:
                    whole_rows = [s for s in full_holdout if s.trading_date in selected_days]
                    whole_scores = [
                        p
                        for s, p in zip(full_holdout, full_scores, strict=True)
                        if s.trading_date in selected_days
                    ]
                    evaluated["nonoverlapping_generalization"] = stock_generalization.assessments(
                        whole_rows, whole_scores, heldout_symbols, unseen
                    )
                    evaluated["nonoverlapping_calendar_dates"] = [
                        d.isoformat() for d in sorted(selected_days)
                    ]
                evaluated["independent_performance_established"] = False
                evaluated["cross_sectional_dependence"] = True
                evaluated["overlapping_full_population_metrics"] = True
            learned = metrics(
                [int(s.label) for s in train if s.label is not None],
                predict_candidate(candidate, fitted, masked(train, config["mask"])),
            )
            folds.append(dict(purged=purged, evaluation=evaluated, train=learned))
    require(
        counts(training)["rows"] >= 1000
        and counts(training)["dates"] >= 252
        and min(counts(training)["positive"], counts(training)["negative"]) >= 50,
        "autonomous_training_population",
    )
    fitted = fit_candidate(candidate, masked(training, config["mask"]), feature_names=names)
    checkpoint = estimator_bytes(fitted)
    import hashlib

    model_hash = hashlib.sha256(checkpoint).hexdigest()
    result: dict[str, Any] = dict(
        config=config,
        model_sha256=model_hash,
        training=counts(training),
        parameters=fitted if candidate.family == "linear" else None,
        checkpoint_load_available=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    if multi:
        result["generalization_training"] = dict(
            policy=config["stock_policy"],
            training_tickers=sorted({s.ticker for s in training}),
            heldout_tickers=sorted(heldout_symbols),
            heldout_scores_used_for_selection=False,
            liblinear_quarantined=True,
        )
    if gate:
        val = dataset["split"]["validation"]
        held = [
            s
            for s in samples
            if val["start"] <= s.trading_date.isoformat()
            and s.label_end is not None
            and s.label_end.isoformat() <= val["end"]
        ]
        require(
            counts(held)["rows"] >= 200
            and counts(held)["dates"] >= 63
            and min(counts(held)["positive"], counts(held)["negative"]) >= 20,
            "autonomous_confirmation_population",
        )
        result["confirmation"] = measure(
            held,
            predict_candidate(candidate, fitted, masked(held, config["mask"])),
            training,
            returns,
        )
    else:
        aps = [f["evaluation"]["metrics"]["pr_auc_ap"] for f in folds]
        result.update(
            folds=folds,
            mean_ap=statistics.mean(aps),
            stability_ap=statistics.mean(aps) - statistics.pstdev(aps),
            worst_balanced_accuracy=min(
                f["evaluation"]["metrics"]["balanced_accuracy"] for f in folds
            ),
            largest_train_gap=max(
                f["train"]["balanced_accuracy"] - f["evaluation"]["metrics"]["balanced_accuracy"]
                for f in folds
            ),
        )
    if historical:
        from donghak_stock_vision.models.historical_research import deferred_assessment, summary

        result["robust_summary"] = summary(folds)
        diagnostic = dataset["historical"]["diagnostic"]
        deferred = [
            s
            for s in samples
            if diagnostic["start"] <= s.trading_date.isoformat()
            and s.label_end is not None
            and s.label_end.isoformat() <= diagnostic["end"]
        ]
        predicted = (
            predict_candidate(candidate, fitted, masked(deferred, config["mask"]))
            if deferred
            else []
        )
        result["deferred_diagnostic"] = stock_generalization.assessments(
            deferred, predicted, heldout_symbols, unseen
        )
        result["deferred_diagnostic"]["nonoverlapping"] = deferred_assessment(
            deferred, predicted, heldout_symbols, unseen
        )
        result["deferred_diagnostic"]["used_for_selection"] = False
        result["deferred_diagnostic"]["independent_or_prospective"] = False
    return result, checkpoint


def champion_check(
    search: dict[str, Any], confirmation: dict[str, Any], incumbent: dict[str, Any] | None = None
) -> dict[str, Any]:
    c = confirmation["confirmation"]
    m = c["metrics"]
    failures = []
    if confirmation.get("prior_cross_study_exposure", True):
        failures.append("independent_confirmation_not_established")
    best = max(b["pr_auc_ap"] for b in c["baselines"].values())
    if search["worst_balanced_accuracy"] < 0.50:
        failures.append("unstable_internal_folds")
    if search["largest_train_gap"] > 0.15:
        failures.append("overfit_gap")
    if m["balanced_accuracy"] < 0.52 or m["roc_auc"] < 0.55:
        failures.append("confirmation_discrimination")
    if m["pr_auc_ap"] < best + 0.02:
        failures.append("baseline_margin")
    if m["brier_score"] > c["baselines"]["training_prior"]["brier_score"]:
        failures.append("calibration_baseline")
    if c["cost_scenario"]["mean_net_event_return"] <= 0:
        failures.append("cost_scenario")
    if any(
        v["balanced_accuracy"] is None or v["balanced_accuracy"] < 0.50
        for v in c["by_quarter"].values()
    ):
        failures.append("period_stability")
    if (
        sum(
            v["balanced_accuracy"] is not None and v["balanced_accuracy"] >= 0.50
            for v in c["by_ticker"].values()
        )
        < 3
    ):
        failures.append("ticker_stability")
    if incumbent and m["balanced_accuracy"] < incumbent["balanced_accuracy"] + 0.02:
        failures.append("incumbent_margin")
    return dict(
        promote=not failures,
        reasons=failures,
        scope="research_only_once_locked_confirmation_not_independent_future_performance",
        performance_approved=False,
        pit_qualified=False,
        execution_authorized=False,
    )
