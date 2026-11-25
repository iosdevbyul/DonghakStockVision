"""Deterministic evidence selection and pre-outcome value policy; no execution authority."""

from __future__ import annotations

import math
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import memory_reasoning as legacy

ARMS = ("rule", "phase_c", "verified", "compact", "value_ranked")
VERSION = "grounded_evidence_pack_v1"
POLICY: dict[str, Any] = dict(
    version="grounded_value_policy_v1",
    evidence_limit=3,
    full_context_bytes=12000,
    compact_context_bytes=4500,
    output_tokens=128,
    repeated_failure_count=2,
    minimum_novel_dimensions=2,
    family_cost_order=dict(linear=0, random_forest=1, hist_gradient_boosting=2),
    missing_evaluations="UNAVAILABLE_not_zero",
    outcomes_used_for_ranking="permitted_prior_and_own_only",
)
CLAIMS = {
    "BASELINE_FAILURE": ("baseline_ap_gain", "nonpositive"),
    "CALIBRATION_REGRESSION": ("calibration_delta", "positive"),
    "OVERFIT": ("overfit_gap", "above_gap"),
    "WEAK_PERIOD": ("worst_balanced_accuracy", "below_half"),
    "BASELINE_REFERENCE": ("baseline_ap_gain", "nonnegative"),
}
INTENTS = ("mask_contrast", "family_contrast", "parameter_contrast")
FAILURES = {
    "memory_evidence_retrieval": "Evidence Retrieval Failure",
    "memory_grounded_citation": "Invalid Citation",
    "memory_grounded_parent": "Parent Experiment Mismatch",
    "memory_grounded_metric_owner": "Metric Ownership Mismatch",
    "memory_insufficient_evidence": "Insufficient Evidence",
    "memory_grounded_schema": "Schema Failure",
    "memory_duplicate_request": "Duplicate Candidate",
    "memory_low_value_hypothesis": "Low-Value Hypothesis",
    "llm_inference_timeout": "Inference Timeout",
    "llm_transport_failed": "Resource Unavailable",
}


def failure_class(reason: str) -> str:
    if reason in FAILURES:
        return FAILURES[reason]
    if any(
        s in reason
        for s in (
            "resource",
            "memory_pressure",
            "thermal",
            "battery",
            "busy",
            "unload",
            "headroom",
            "preexisting_model",
        )
    ):
        return "Resource Unavailable"
    if "parent" in reason:
        return "Parent Experiment Mismatch"
    if "metric_owner" in reason:
        return "Metric Ownership Mismatch"
    return "Schema Failure"


def claims(row: dict[str, Any]) -> list[str]:
    result = []
    for name, (key, test) in CLAIMS.items():
        v = row.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            continue
        if (
            test == "nonpositive"
            and v <= 0
            or test == "positive"
            and v > 0
            or test == "above_gap"
            and v > 0.15
            or test == "below_half"
            and v <= 0.5
            or test == "nonnegative"
            and v >= 0
        ):
            result.append(name)
    return result


def category(row: dict[str, Any]) -> str:
    if row["state"] == "failed" or row.get("baseline_ap_gain", 0) < 0:
        return "failure"
    if row.get("baseline_ap_gain", 0) > 0 and row.get("calibration_delta", 1) <= 0:
        return "success"
    return "neutral"


def differences(candidate: dict[str, Any], parent: dict[str, Any]) -> list[str]:
    result = []
    if candidate["mask"] != parent["mask"]:
        result.append("mask_contrast")
    if candidate["candidate"]["family"] != parent["candidate"]["family"]:
        result.append("family_contrast")
    if candidate["candidate"] != parent["candidate"]:
        result.append("parameter_contrast")
    return result


def value_rows(
    candidates: list[dict[str, Any]], history: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pre-outcome information/cost proxies; no validation predictions or peer-cache probes."""
    rows = []
    for c in candidates:
        cfg = c["config"]
        exact = any(h["config"] == cfg for h in history)
        mask_new = all(h["config"]["mask"] != cfg["mask"] for h in history)
        family_new = all(
            h["config"]["candidate"]["family"] != cfg["candidate"]["family"] for h in history
        )
        profile_new = all(h["config"]["candidate"] != cfg["candidate"] for h in history)
        repeated = sum(
            category(h) == "failure"
            and h["config"]["mask"] == cfg["mask"]
            and h["config"]["candidate"]["family"] == cfg["candidate"]["family"]
            for h in history
        )
        novelty = int(mask_new) + int(family_new) + int(profile_new)
        informative = (
            bool(history)
            and not exact
            and not (
                repeated >= POLICY["repeated_failure_count"]
                and novelty < POLICY["minimum_novel_dimensions"]
            )
        )
        cost = POLICY["family_cost_order"][cfg["candidate"]["family"]]
        rows.append(
            dict(
                experiment_id=c["experiment_id"],
                exact_duplicate=exact,
                novel_dimensions=novelty,
                similar_failures=repeated,
                information_value=int(informative),
                cost_proxy=cost,
                score=4 * int(informative) + novelty - cost,
                peer_cache_knowledge="UNKNOWN_UNSELECTED_PEERS",
                prospective_performance="UNKNOWN",
                heuristic=True,
            )
        )
    return rows


def select(
    history: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for h in history:
        key = h["experiment_id"]
        require(key not in unique or unique[key] == h, "memory_evidence_retrieval")
        unique[key] = h

    def relevance(h: dict[str, Any]) -> tuple[int, str]:
        similarity = max(
            (
                sum(
                    (
                        h["config"]["mask"] == c["config"]["mask"],
                        h["config"]["candidate"]["family"] == c["config"]["candidate"]["family"],
                    )
                )
                for c in candidates
            ),
            default=0,
        )
        return (-similarity, h["experiment_id"])

    ordered = sorted(unique.values(), key=relevance)
    chosen = []
    for kind in ("failure", "success", "neutral"):
        group = [h for h in ordered if category(h) == kind and claims(h)]
        if group:
            chosen.append(group[0])
    for h in ordered:
        if len(chosen) >= POLICY["evidence_limit"]:
            break
        if h not in chosen and claims(h):
            chosen.append(h)
    coverage = {
        k: dict(
            available=sum(category(h) == k for h in ordered),
            selected=sum(category(h) == k for h in chosen),
        )
        for k in ("failure", "success", "neutral")
    }
    return chosen, dict(
        categories=coverage,
        omitted=len(ordered) - len(chosen),
        omitted_reasons="bounded_relevance_and_representation",
        selection_is_causal=False,
    )


def pack(view: dict[str, Any], mode: str) -> dict[str, Any]:
    require(mode in ("verified", "compact", "value_ranked"), "memory_grounded_mode")
    for h in view["history"]:
        require(
            h["scope"] == view["evidence_scope"]
            and digest({k: v for k, v in h.items() if k != "content_hash"}) == h["content_hash"],
            "memory_evidence_retrieval",
        )
    selected, coverage = select(view["history"], view["candidates"])
    require(bool(selected), "memory_insufficient_evidence")
    citations = {}
    for h in selected:
        require(
            h["scope"] == view["evidence_scope"]
            and digest({k: v for k, v in h.items() if k != "content_hash"}) == h["content_hash"],
            "memory_evidence_retrieval",
        )
        key = (
            "e_"
            + digest(
                dict(experiment=h["experiment_id"], content=h["content_hash"], scope=h["scope"])
            )[:12]
        )
        require(key not in citations, "memory_grounded_citation")
        citations[key] = h
    candidates = {"c_" + c["experiment_id"][:12]: c for c in view["candidates"]}
    require(len(candidates) == len(view["candidates"]), "memory_grounded_schema")
    values = value_rows(
        view["candidates"], list({h["experiment_id"]: h for h in view["history"]}.values())
    )
    return dict(
        version=VERSION,
        mode=mode,
        scope=view["evidence_scope"],
        citations=citations,
        candidates=candidates,
        values=values,
        coverage=coverage,
        policy=POLICY,
    )


def context(pack: dict[str, Any]) -> tuple[dict[str, Any], str]:
    import json

    full = pack["mode"] == "verified"
    evidence = {}
    for key, h in pack["citations"].items():
        evidence[key] = dict(
            family=h["config"]["candidate"]["family"],
            mask=h["config"]["mask"],
            ap=h.get("mean_ap"),
            ba=h.get("mean_ba"),
            brier=h.get("mean_brier"),
            gain=h.get("baseline_ap_gain"),
            calibration=h.get("calibration_delta"),
            gap=h.get("overfit_gap"),
            worst=h.get("worst_balanced_accuracy"),
            claims=claims(h),
            **(
                dict(
                    originalExperimentId=h["experiment_id"],
                    periods=h["internal_folds"],
                    sourceContentHash=h["content_hash"],
                )
                if full
                else {}
            ),
        )
    candidates = {
        k: dict(
            f=c["config"]["candidate"]["family"], m=c["config"]["mask"], p=c["config"]["candidate"]
        )
        for k, c in pack["candidates"].items()
    }
    summary = dict(
        scope=dict(cohort=pack["scope"]["cohort"], horizon=pack["scope"]["horizon"]),
        evidence=evidence,
        candidates=candidates,
        missing=pack["coverage"],
    )
    if pack["mode"] == "value_ranked":
        by = {v["experiment_id"]: v for v in pack["values"]}
        columns = (
            "information_value",
            "novel_dimensions",
            "similar_failures",
            "cost_proxy",
            "score",
        )
        summary["valueColumns"] = list(columns)
        summary["values"] = {
            k: [by[c["experiment_id"]][n] for n in columns] for k, c in pack["candidates"].items()
        }

    prompt = (
        "Select allowed keys only. Claims are source facts, not causes. Rank choices; "
        "choice must be first. Test an actual configuration contrast. "
        "Abstain if evidence cannot support it. No code or investment authority.\n"
        + json.dumps(summary, separators=(",", ":"))
    )
    maximum = POLICY["full_context_bytes"] if full else POLICY["compact_context_bytes"]
    require(len(prompt.encode()) <= maximum, "memory_grounded_context_budget")
    return summary, prompt


def schema(p: dict[str, Any]) -> dict[str, Any]:
    return dict(
        type="object",
        properties=dict(
            choice=dict(type="string", enum=[*p["candidates"], "ABSTAIN"]),
            cite=dict(type="string", enum=[*p["citations"], "NONE"]),
            claim=dict(type="string", enum=[*CLAIMS, "NONE"]),
            test=dict(type="string", enum=[*INTENTS, "NONE"]),
            ranking=dict(
                type="array",
                minItems=0,
                maxItems=3 if p["mode"] == "value_ranked" else 1,
                items=dict(type="string", enum=list(p["candidates"])),
            ),
        ),
        required=["choice", "cite", "claim", "test", "ranking"],
        additionalProperties=False,
    )


def resolve(value: dict[str, Any], p: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    require(p == pack(view, p["mode"]), "memory_evidence_retrieval")
    fields(value, "choice cite claim test ranking")
    require(
        all(type(value[k]) is str for k in ("choice", "cite", "claim", "test")),
        "memory_grounded_schema",
    )
    if value["choice"] == "ABSTAIN":
        require(
            value["cite"] == value["claim"] == value["test"] == "NONE" and value["ranking"] == [],
            "memory_grounded_schema",
        )
        raise ValueError("memory_insufficient_evidence")
    require(value["choice"] in p["candidates"], "memory_grounded_schema")
    require(value["cite"] in p["citations"], "memory_grounded_citation")
    h = p["citations"][value["cite"]]
    c = p["candidates"][value["choice"]]
    cfg = c["config"]
    require(value["claim"] in claims(h), "memory_grounded_metric_owner")
    require(value["test"] in differences(cfg, h["config"]), "memory_grounded_parent")
    rank = value["ranking"]
    require(
        type(rank) is list
        and 1 <= len(rank) <= (3 if p["mode"] == "value_ranked" else 1)
        and all(type(k) is str for k in rank)
        and len(set(rank)) == len(rank)
        and rank[0] == value["choice"]
        and all(k in p["candidates"] for k in rank),
        "memory_grounded_schema",
    )
    score = next(v for v in p["values"] if v["experiment_id"] == c["experiment_id"])
    require(not score["exact_duplicate"], "memory_duplicate_request")
    if p["mode"] == "value_ranked":
        require(score["information_value"] > 0, "memory_low_value_hypothesis")
    metric = CLAIMS[value["claim"]][0]
    canonical = dict(
        proposalVersion="gemma_research_proposal_v1",
        experimentId=c["experiment_id"],
        hypothesis="Test " + value["test"].replace("_", " "),
        modelFamily=cfg["candidate"]["family"],
        featureSet=cfg["mask"],
        hyperparameters=dict(value=cfg["candidate"]["value"], seed=42),
        researchGoal=cfg["goal"]["label_version"],
        parentExperimentId=h["experiment_id"],
        expectedImprovement="Internal baseline gain remains unverified",
        rationale="Source fact " + value["claim"],
        horizon=view["goal"]["horizon"],
        cohort=view["cohort"],
        problem="Internal development uncertainty",
        failureCondition="No baseline gain or unstable BA",
        evidence=[dict(experimentId=h["experiment_id"], metric=metric)],
        **legacy.FIXED,
    )
    legacy.validate(canonical, view)
    return canonical
