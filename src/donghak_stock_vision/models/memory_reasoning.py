"""Bounded, falsifiable hypotheses over original internal research evidence."""

from __future__ import annotations

from typing import Any

from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.research_agent import validate_gemma_proposal

PROMPT = "memory_guided_hypothesis_v2"
ARMS = ("rule", "gemma_blind", "gemma_memory")
METRICS = (
    "mean_ap",
    "stability_ap",
    "overfit_gap",
    "baseline_ap_gain",
    "worst_balanced_accuracy",
    "failure_reason",
)
EXTRA = (
    "promptVersion horizon cohort problem failureCondition baseline validation "
    "falsification minimumEvidence estimatedCost evidence"
).split()
FIXED = dict(
    promptVersion=PROMPT,
    baseline="training_prior_and_existing_baselines",
    validation="frozen_purged_internal_folds",
    falsification="no_baseline_ap_gain_or_unstable_balanced_accuracy",
    minimumEvidence="20_calendar_anchors_and_10_per_class",
    estimatedCost="one_existing_python_experiment",
)


def schema(view: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    from copy import deepcopy

    value = deepcopy(base)
    props = value["properties"]
    for k, v in FIXED.items():
        props[k] = dict(type="string", enum=[v])
    props["horizon"] = dict(type="integer", enum=[view["goal"]["horizon"]])
    props["cohort"] = dict(type="string", enum=[view["cohort"]])
    for k in ("hypothesis", "expectedImprovement", "rationale", "problem", "failureCondition"):
        props[k] = dict(type="string", minLength=1, maxLength=100)
    props["evidence"] = dict(
        type="array",
        maxItems=3,
        minItems=0,
        items=dict(
            type="object",
            properties=dict(
                experimentId=dict(
                    type="string", enum=[h["experiment_id"] for h in view["history"]]
                ),
                metric=dict(type="string", enum=list(METRICS)),
            ),
            required=["experimentId", "metric"],
            additionalProperties=False,
        ),
    )
    # Empty history admits only an empty evidence list, not an invalid empty enum.
    if not view["history"]:
        props["evidence"] = dict(type="array", maxItems=0, items=dict(type="object"))
    # Scope and evaluation policy are host-owned, frozen facts rather than generated prose.
    for key in (*FIXED, "horizon", "cohort"):
        props.pop(key)
    value["required"] = list(props)
    return value


def validate(value: dict[str, Any], view: dict[str, Any]) -> str:
    old = {k: v for k, v in value.items() if k not in EXTRA}
    selected = validate_gemma_proposal(old, view)
    fields(value, " ".join(list(old) + EXTRA))
    require(all(value[k] == v for k, v in FIXED.items()), "memory_fixed_policy")
    require(
        type(value["horizon"]) is int
        and value["horizon"] == view["goal"]["horizon"]
        and value["cohort"] == view["cohort"],
        "memory_scope_substitution",
    )
    for k in ("hypothesis", "expectedImprovement", "rationale", "problem", "failureCondition"):
        require(type(value[k]) is str and 0 < len(value[k]) <= 100, "memory_hypothesis_text")
    evidence = value["evidence"]
    require(type(evidence) is list and len(evidence) <= 3, "memory_evidence_bound")
    histories = {h["experiment_id"]: h for h in view["history"]}
    require(
        len({(e.get("experimentId"), e.get("metric")) for e in evidence}) == len(evidence),
        "memory_duplicate_citation",
    )
    require(bool(evidence) or not histories, "memory_missing_reasoning_evidence")
    for e in evidence:
        fields(e, "experimentId metric")
        require(
            e["experimentId"] in histories
            and e["metric"] in METRICS
            and e["metric"] in histories[e["experimentId"]],
            "memory_metric_owner",
        )
    parent = value["parentExperimentId"]
    require(
        parent is None or parent in {e["experimentId"] for e in evidence}, "memory_parent_evidence"
    )
    return selected


def hypothesis_id(value: dict[str, Any], session: str, arm: str) -> str:
    return digest(dict(version=PROMPT, session=session, arm=arm, proposal=value))
