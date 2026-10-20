"""Factual comparison of existing verified read models; no economic calculations."""

from typing import Any

from donghak_stock_vision.api.historical_portfolio_projection import PROJECTION, RESTRICTIONS


def comparison_item(view: dict[str, Any]) -> dict[str, Any]:
    summary = view["summary"]
    valuation = summary["finalValuation"]
    missing = {"policy": "valuation_policy_not_in_published_projection"}
    timestamp, stage = valuation.get("timestamp"), valuation.get("stage")
    for key, value in (("timestamp", timestamp), ("stage", stage)):
        if value is None:
            missing[key] = "valuation_source_field_unavailable"
    positions = valuation["positions"]
    marks = [position.get("mark") for position in positions]
    fields = (
        sorted({mark["field"] for mark in marks})
        if all(isinstance(mark, dict) and isinstance(mark.get("field"), str) for mark in marks)
        else None
    )
    if fields is None:
        missing["observedMarkFields"] = "valuation_mark_field_unavailable"
    scopes = sorted(
        {
            row["evidence"]["signal_evidence_scope"]
            for row in view["trades"]
            if isinstance(row["evidence"]["signal_evidence_scope"], str)
        }
    )
    unavailable = dict(summary["unavailable"])
    if not scopes:
        unavailable["signalEvidenceScopes"] = "signal_evidence_scope_not_recorded"
    return {
        "schemaVersion": PROJECTION,
        **RESTRICTIONS,
        **summary,
        "unavailable": unavailable,
        "tickerCount": len(summary["tickers"]),
        "signalEvidenceScopes": scopes,
        "valuationBasis": {
            "timestamp": timestamp,
            "stage": stage,
            "observedMarkFields": fields,
            "policy": None,
            "unavailable": missing,
        },
    }


def compare_views(views: list[dict[str, Any]]) -> dict[str, Any]:
    items = [comparison_item(view) for view in views]
    periods_differ = any(item["period"] != items[0]["period"] for item in items[1:])
    unavailable = {"valuationPolicyComparison": "valuation_policy_not_in_published_projection"}
    notices = {"valuation_policy_not_in_published_projection", "no_ranking_or_recommendation"}
    differences: dict[str, bool | None] = {}
    for field, output in (
        ("timestamp", "valuationTimestampsDiffer"),
        ("observedMarkFields", "observedMarkFieldsDiffer"),
    ):
        values = [item["valuationBasis"][field] for item in items]
        differences[output] = (
            any(value != values[0] for value in values[1:])
            if all(value is not None for value in values)
            else None
        )
        if differences[output] is None:
            unavailable[output] = "valuation_comparison_evidence_unavailable"
    if periods_differ:
        notices.add("analysis_periods_differ")
    if differences["valuationTimestampsDiffer"]:
        notices.add("valuation_observation_times_differ")
    if differences["observedMarkFieldsDiffer"]:
        notices.add("observed_valuation_mark_fields_differ")
    if any(not item["portfolioValuationAvailable"] for item in items):
        notices.add("valuation_evidence_unavailable")
    if any(item["dataOrigin"] == "synthetic" for item in items):
        notices.add("synthetic_results_not_real_market_performance")
    if any("controlled_mechanics_test_double" in item["signalEvidenceScopes"] for item in items):
        notices.add("controlled_mechanics_not_model_inference")
    return {
        "schemaVersion": "historical_portfolio_comparison_v1",
        "items": items,
        "comparability": {
            "periodsDiffer": periods_differ,
            **differences,
            "valuationPolicyComparison": "unavailable",
            "unavailable": unavailable,
            "notices": sorted(notices),
        },
    }
