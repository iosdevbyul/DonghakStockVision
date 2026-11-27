"""Contract-separated diagnostics; no model ranking or performance approval."""

from itertools import combinations
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.direction_shadow import contract
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.evaluation_evidence import calendar_evidence
from donghak_stock_vision.models.linear import sigmoid

METRICS = (
    "n",
    "positive",
    "negative",
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
    "roc_auc",
    "pr_auc_ap",
    "brier_score",
)


def measure(labels: list[int], scores: list[float]) -> dict[str, Any]:
    result = metrics(labels, scores)
    return {key: result[key] for key in METRICS} | dict(confusion_matrix=result["confusion_matrix"])


def summarize(
    predictions: list[dict[str, Any]], identities: list[dict[str, Any]]
) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for p in predictions:
        require(
            p["predictionId"] not in unique or unique[p["predictionId"]] == p,
            "direction_publication_conflict",
        )
        unique[p["predictionId"]] = p
    predictions = sorted(unique.values(), key=lambda p: (p["createdAt"], p["predictionId"]))
    keys = (
        "modelId",
        "modelSha256",
        "horizon",
        "mode",
        "sourceQuality",
        "labelContract",
        "evaluationPolicyId",
        "predictionType",
        "featureVersion",
    )
    require(
        len({tuple(i[k] for k in keys[:1] + keys[2:5]) for i in identities}) == len(identities),
        "direction_duplicate_model_population",
    )
    require(
        all(any(all(p[k] == i[k] for k in keys) for i in identities) for p in predictions),
        "direction_unbound_public_prediction",
    )
    groups = []
    for identity in identities:
        ps = [
            p
            for p in predictions
            if all(p[k] == identity[k] for k in ("modelId", "horizon", "mode", "sourceQuality"))
        ]
        evaluated = [p for p in ps if p["state"] == "evaluated"]
        distinct: dict[tuple[Any, ...], dict[str, Any]] = {}
        for p in evaluated:
            key = (p["ticker"], p["inputTradingDate"], tuple(p["outcomeSessions"]))
            if key in distinct:
                require(
                    all(
                        p.get(k) == distinct[key].get(k)
                        for k in (
                            "score",
                            "baselineScore",
                            "actualLabel",
                            "inputCohort",
                            "r5Evidence",
                        )
                    ),
                    "direction_conflicting_duplicate_population",
                )
            distinct[key] = p
        ev = list(distinct.values())
        real = (
            ev
            if identity["mode"] == "research_forward_candidate"
            and identity["sourceQuality"] == "local_provider_capture"
            else []
        )
        evidence = calendar_evidence(
            [
                dict(
                    ticker=p["ticker"],
                    day=p["targetTradingDate"],
                    end=p["outcomeSessions"][-1],
                    label=p["actualLabel"],
                )
                for p in real
            ]
        )
        labels = [p["actualLabel"] for p in ev]
        diagnostic = measure(labels, [p["score"] for p in ev])
        baseline = measure(labels, [p["baselineScore"] for p in ev])
        supplementary = [p for p in ev if "r5Evidence" in p]
        if any("r5Evidence" in p for p in ps):
            require(len(supplementary) == len(ev), "direction_r5_mixed_population")
            identity = identity | dict(
                r5Baselines=dict(
                    training_majority=measure(
                        labels, [float(p["baselineScore"] >= 0.5) for p in ev]
                    ),
                    momentum=measure(labels, [p["r5Evidence"]["momentumScore"] for p in ev]),
                )
            )
        groups.append(
            identity
            | dict(
                total=len(ps),
                pending=sum(p["state"] == "pending" for p in ps),
                evaluated=len(evaluated),
                duplicateEvaluationRows=len(evaluated) - len(ev),
                excluded=sum(p["state"] == "unavailable" for p in ps),
                metrics=diagnostic,
                baselineMetrics=baseline,
                baselineRelative={
                    k: (
                        baseline[k] - diagnostic[k]
                        if k == "brier_score"
                        else diagnostic[k] - baseline[k]
                    )
                    if baseline[k] is not None and diagnostic[k] is not None
                    else None
                    for k in ("balanced_accuracy", "pr_auc_ap", "brier_score")
                },
                independentAnchors=evidence["nonoverlapping_calendar_anchors"],
                missingAnchors=evidence["missing_calendar_anchors"],
                missingPositive=evidence["missing_positive_rows"],
                missingNegative=evidence["missing_negative_rows"],
                evidenceStatus=evidence["status"],
                qualifiedAnchors=0,
                blockers=[
                    reason
                    for reason, blocked in (
                        ("insufficient_calendar_anchors", evidence["missing_calendar_anchors"] > 0),
                        ("insufficient_positive_class", evidence["missing_positive_rows"] > 0),
                        ("insufficient_negative_class", evidence["missing_negative_rows"] > 0),
                        ("no_verified_prospective_outcomes", not real),
                        ("historical_replay_excluded", identity["mode"] == "historical_replay"),
                        (
                            "offline_fixture_excluded",
                            identity["sourceQuality"] == "offline_fixture",
                        ),
                        ("independent_chronology_unverified", True),
                        ("pit_unqualified", True),
                    )
                    if blocked
                ],
                latestEvaluationDate=max((p["outcomeSessions"][-1] for p in ev), default=None),
                performanceApproved=False,
                pitQualified=False,
                independentPredictivePower=False,
            )
        )
    comparisons = []
    for a, b in combinations(groups, 2):
        if (
            a["modelId"] == b["modelId"]
            or a["horizon"] != b["horizon"]
            or a["predictionType"] != b["predictionType"]
        ):
            continue
        same = all(
            a[k] == b[k] for k in ("mode", "sourceQuality", "labelContract", "evaluationPolicyId")
        )

        def population(m: dict[str, Any]) -> dict[tuple[Any, ...], dict[str, Any]]:
            return {
                (
                    p["ticker"],
                    p["inputTradingDate"],
                    tuple(p["outcomeSessions"]),
                    p["inputCohort"],
                ): p
                for p in predictions
                if p["modelId"] == m["modelId"]
                and p["horizon"] == m["horizon"]
                and p["mode"] == m["mode"]
                and p["sourceQuality"] == m["sourceQuality"]
                and p["state"] == "evaluated"
            }

        pa, pb = population(a), population(b)
        comparable = (
            same
            and bool(pa)
            and pa.keys() == pb.keys()
            and all(pa[k]["actualLabel"] == pb[k]["actualLabel"] for k in pa)
        )
        comparisons.append(
            dict(
                firstModel=a["modelId"],
                secondModel=b["modelId"],
                predictionType=a["predictionType"],
                horizon=a["horizon"],
                comparable=comparable,
                reason="same_verified_population_diagnostic_only"
                if comparable
                else "contract_source_or_population_mismatch",
                performanceApproved=False,
            )
        )
    return dict(
        version="prospective_direction_publication_v1",
        models=groups,
        predictions=predictions,
        comparisons=comparisons,
        pitQualified=False,
        performanceApproved=False,
        executionAuthorized=False,
    )


def retained_evidence(p: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    future = outcome.get("future")
    base, target = (future["bars"][i]["close"] for i in (0, -1)) if future else (None, None)
    return dict(
        baseTradingDate=p["base_session"],
        outcomeTradingDate=p["outcome_sessions"][-1],
        baseClose=base,
        targetClose=target,
        actualReturn=(target - base) / base if base is not None else None,
        correct=outcome.get("correct"),
        momentumScore=sigmoid(p["features"][2] / p["features"][4]),
        outcomeReason=outcome.get("reason"),
        independentlyQualified=False,
    )


def publication(reg: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    latest = {
        e["payload"]["prediction_id"]: e["payload"] for e in entries if e["kind"] == "outcome"
    }
    predictions = []
    cohorts: dict[str, int] = {}
    for e in entries:
        if e["kind"] != "prediction":
            continue
        p = e["payload"]
        cohort = cohorts.setdefault(p["input_source_pin"], len(cohorts))
        o = latest.get(p["prediction_id"], {})
        predictions.append(
            dict(
                predictionId=p["prediction_id"],
                inputCohort=cohort,
                modelId=p["model"]["experiment_id"],
                modelSha256=p["model"]["sha256"],
                horizon=p["horizon"],
                ticker=p["ticker"],
                inputTradingDate=p["input_trading_date"],
                targetTradingDate=p["outcome_sessions"][0],
                outcomeSessions=p["outcome_sessions"],
                createdAt=p["generated_at"],
                predictionType="future_close_direction",
                conditioningDirection=None,
                featureVersion=p["feature_version"],
                labelContract=p["contract"]["version"],
                evaluationPolicyId=digest(p["contract"]["evaluation"]),
                score=p["score"],
                baselineScore=p["baseline_score"],
                calibratedProbability=None,
                predictedClass=p["predicted_class"],
                threshold=0.5,
                state=o.get("state", "pending"),
                actualLabel=o.get("actual_label"),
                mode=p["mode"],
                sourceQuality=p["source_kind"],
            )
            | (dict(r5Evidence=retained_evidence(p, o)) if "lineage" in p["model"] else {})
        )
    ids = []
    for h, m in reg["models"].items():
        for mode in {"research_forward_candidate"} | {
            p["mode"] for p in predictions if p["horizon"] == int(h)
        }:
            ids.append(
                dict(
                    modelId=m["experiment_id"],
                    modelSha256=m["sha256"],
                    horizon=int(h),
                    mode=mode,
                    sourceQuality=reg["source_kind"],
                    predictionType="future_close_direction",
                    featureVersion="causal_daily_closed_observed_periods_v2",
                    labelContract=contract(int(h))["version"],
                    evaluationPolicyId=digest(contract(int(h))["evaluation"]),
                )
            )
    return summarize(
        predictions, sorted(ids, key=lambda x: (x["horizon"], x["modelId"], x["mode"]))
    )


def legacy_publication(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Already semantically verified v1/v2 envelopes; no event reinterpretation."""
    latest = {
        e["payload"]["prediction_id"]: e["payload"] for e in entries if e["kind"] == "outcome"
    }
    cohorts: dict[str, int] = {}
    rows = []
    identities = {}
    for e in entries:
        if e["kind"] != "prediction":
            continue
        p = e["payload"]
        outcome = latest.get(p["prediction_id"], {})
        identity = dict(
            modelId=p["model"]["identifier"],
            modelSha256=p["model"]["artifact_sha256"],
            horizon=5,
            mode=p["mode"],
            sourceQuality=p["source_classification"],
            predictionType="conditional_reversal_event",
            featureVersion=p["feature_version"],
            labelContract=p["event_rule"],
            evaluationPolicyId=digest(
                dict(
                    version="legacy_reversal_projection_v1",
                    event_rule=p["event_rule"],
                    threshold=p["threshold"],
                    minimum_calendar_anchors=20,
                    minimum_class_rows=10,
                )
            ),
        )
        identities[digest(identity)] = identity
        rows.append(
            identity
            | dict(
                predictionId=p["prediction_id"],
                inputCohort=cohorts.setdefault(p["input_source_pin"], len(cohorts)),
                ticker=p["ticker"],
                inputTradingDate=p["input_latest_trading_date"],
                targetTradingDate=p["target_trading_date"],
                outcomeSessions=p["outcome_sessions"],
                createdAt=p["generated_at"],
                score=p["score"],
                baselineScore=p["model"]["prior"],
                calibratedProbability=None,
                conditioningDirection=p["direction"],
                predictedClass=int(p["score"] >= p["threshold"]),
                threshold=p["threshold"],
                state=outcome.get("state", "pending"),
                actualLabel=outcome.get("actual_label"),
            )
        )
    return summarize(rows, list(identities.values()))
