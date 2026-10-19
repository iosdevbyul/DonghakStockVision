"""Small allowlisted views of an already replay-validated portfolio result.

This module does no accounting, inference or source acquisition. Nested snake_case
fields retain the original report vocabulary and original source references.
"""

from typing import Any

from donghak_stock_vision.data.learning import digest

PROJECTION = "published_historical_portfolio_v1"
RESULT = "five_ticker_historical_result_v1"
REPORT = "five_ticker_historical_report_v1"
RESTRICTIONS = {
    "researchOnly": True,
    "operationalEligible": False,
    "executable": False,
    "pitVerified": False,
    "oosVerified": False,
    "investmentSuitabilityVerified": False,
    "brokerageEligible": False,
}


def selected(source: dict[str, Any], keys: str) -> dict[str, Any]:
    return {key: source[key] for key in keys.split()}


def projection(source: dict[str, Any], transport_hash: str) -> dict[str, Any]:
    """Called only after authoritative replay by the offline publisher."""
    if source["version"] != RESULT or source["detailed_report"]["version"] != REPORT:
        raise ValueError("unsupported_archive_version")
    report, perf, run = (source[k] for k in ("detailed_report", "performance", "run"))
    request = source["frozen_input"]["request"]
    if any(report[k] is not False for k in ("executable", "operational_eligible", "pit_verified")):
        raise ValueError("result_validation_required")
    analyses = {
        entry["decision_id"]: entry["input"]["analysis"]
        for event in run["events"]
        for entry in event.get("decisions", [])
    }
    timeline = []
    for row in report["timeline"]:
        item = selected(
            row,
            "opportunity_id trading_date ticker source_sequence opportunity_at analysis_id "
            "analysis_available_at analysis_anchor_at analysis_observed_at model_id snapshot_id "
            "model_created_at source_observed_at model_family signal_evidence_scope context score "
            "thresholds score_semantics feature_values feature_contributions linear_intercept "
            "hgb_pair hgb_provenance_hash decision_id decision_at action selected_rule "
            "requested_quantity reasons checks conditions admission reservation order_id "
            "executions source unavailable",
        )
        analysis = analyses.get(row["decision_id"])
        signal_keys = "up_score down_score up_status down_status input_status signal_state".split()
        item["signalEvidence"] = (
            {
                "values": {k: analysis.get(k) for k in signal_keys},
                "unavailable": {
                    k: "source_field_unavailable" for k in signal_keys if analysis.get(k) is None
                },
                "sourceHash": digest(analysis),
                "decisionId": row["decision_id"],
            }
            if analysis is not None
            else None
        )
        item["signal_status"] = None
        item["unavailable"] = {
            **item["unavailable"],
            "signal_status": "aggregate_signal_status_not_recorded",
        }
        # Releases carry expanded receipts; publish scalar source references only.
        item["releases"] = [
            {
                **{k: x.get(k) for k in ("release_status", "order_id", "reason")},
                "effective_at": x.get("ledger_command", {}).get("effective_at"),
                "known_at": x.get("ledger_command", {}).get("known_at"),
                "unavailable": {"reason": "source_field_unavailable"}
                if x.get("reason") is None
                else {},
                "source_hash": digest(x),
            }
            for x in row["releases"]
        ]
        timeline.append(item)
    terminals = {x["order_id"]: x for x in report["terminal_orders"]}
    trades = []
    for row in timeline:
        # Each examined opportunity is retained, including HOLD/WAIT and no candidate.
        if any(x["status"] == "filled" for x in row["executions"]):
            classification = "filled"
        elif row["order_id"] in terminals and terminals[row["order_id"]]["status"] in {
            "open",
            "partial",
        }:
            classification = "pending"
        elif terminals.get(row["order_id"], {}).get("status") == "rejected":
            classification = "rejected"
        elif row["reservation"] and row["reservation"]["status"] == "accepted":
            classification = "accepted"
        elif (
            row["admission"]
            and row["admission"]["status"] in {"blocked", "rejected"}
            and row["admission"]["reason"] != "no_order_action"
        ):
            classification = "rejected"
        elif row["reservation"] and row["reservation"]["status"] in {"blocked", "rejected"}:
            classification = "rejected"
        else:
            classification = "no_order"
        trades.append(
            {
                "eventId": digest([run["run_id"], row["opportunity_id"]]),
                "classification": classification,
                "terminalStatus": terminals.get(row["order_id"], {}).get("status"),
                "evidence": row,
            }
        )
    cash = run["final_account"]
    summary = {
        "resultSchemaVersion": RESULT,
        "reportSchemaVersion": REPORT,
        "modelFamily": request["model_family"],
        "modelId": request["model_id"],
        "modelReferences": request["model_references"],
        "tickers": request["tickers"],
        "period": selected(request, "start end"),
        "initialVirtualCash": request["initial_cash"],
        "finalTotalCash": cash["total_cash_krw"],
        "availableCash": cash["available_cash_krw"],
        "reservedCash": cash["reserved_cash_krw"],
        "finalPortfolioEquity": perf["final_equity"],
        "realizedNetPnl": perf["ledger_realized_pnl"],
        "unrealizedPnl": perf["unrealized_pnl"],
        "portfolioReturn": perf["total_return"],
        "decisionCount": sum(row["decision_id"] is not None for row in timeline),
        "acceptedOrderCount": len(cash["orders"]),
        "fillCount": perf["fill_count"],
        "completedPositionCount": perf["completed_trades"],
        "pendingOrderCount": sum(x["status"] in {"open", "partial"} for x in terminals.values()),
        "valuationCoverage": perf["valuation_coverage"],
        "portfolioValuationAvailable": report["final_valuation"]["complete"],
        "finalValuation": report["final_valuation"],
        "costs": perf["costs"],
        "provenance": report["sources"],
        "archiveTransportHash": transport_hash,
        "resultHash": digest(source),
        "reportHash": source["detailed_report_hash"],
        "runHash": source["run_hash"],
        "performanceHash": source["performance_hash"],
        "evidenceScope": report["scope"],
        "dataOrigin": report["data_origin"],
        "limitations": report["limitations"],
        "unavailable": {
            key: "valuation_evidence_unavailable"
            for key in ("finalPortfolioEquity", "unrealizedPnl", "portfolioReturn")
            if {
                "finalPortfolioEquity": perf["final_equity"],
                "unrealizedPnl": perf["unrealized_pnl"],
                "portfolioReturn": perf["total_return"],
            }[key]
            is None
        },
    }
    tickers = {}
    for ticker in request["tickers"]:
        tickers[ticker] = {
            "ticker": ticker,
            "modelId": request["model_id"],
            "modelReferences": request["model_references"],
            "positions": [
                selected(
                    x,
                    "position_id ticker status quantity acquired_at exited_at "
                    "holding_duration verified_trading_sessions acquisition acquisition_fill sales "
                    "realized_net_pnl realized_source valuation "
                    "previous_completed_position_id source",
                )
                for x in report["positions"]
                if x["ticker"] == ticker
            ],
            "realizedNetPnl": report["realized_pnl_by_ticker"].get(ticker),
            "valuation": next((x for x in perf["final_positions"] if x["ticker"] == ticker), None),
            "unavailable": {"valuation": "no_final_position_mark"}
            if not any(x["ticker"] == ticker for x in perf["final_positions"])
            else {},
        }
    for item in tickers.values():
        if item["realizedNetPnl"] is None:
            item["unavailable"]["realizedNetPnl"] = "ticker_realized_pnl_not_recorded"
    return {"schemaVersion": PROJECTION, "summary": summary, "tickers": tickers, "trades": trades}
