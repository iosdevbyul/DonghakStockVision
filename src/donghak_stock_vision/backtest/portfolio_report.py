"""Versioned five-ticker factual report; existing accounting is the source of P&L."""

from decimal import Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.backtest.account import account_view
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.ledger import restore
from donghak_stock_vision.backtest.performance import calculate_performance, verified_frames
from donghak_stock_vision.backtest.portfolio import canonical_frozen, frozen_runner
from donghak_stock_vision.backtest.trading_report import (
    condition_reasons,
    duration,
    explanation,
    fill_view,
    session_duration,
)
from donghak_stock_vision.backtest.validation import require, utc
from donghak_stock_vision.data.learning import digest

VERSION = "five_ticker_historical_report_v1"


def replay_five_ticker(document: FrozenJSON) -> FrozenJSON:
    """Deterministic offline entry point. No source DB, model load, training or network."""
    document = canonical_frozen(document)
    runner = frozen_runner(document)
    source = document.to_dict()
    run = runner.run()
    performance = calculate_performance(
        run,
        runner.tape,
        FrozenJSON.freeze(source["valuation_policy"]),
        historical_input=runner.historical_input,
    )
    r, p = run.to_dict(), performance.to_dict()
    final = restore(FrozenJSON.freeze(r["final_checkpoint"]))
    end_clock = VirtualClock.at(
        r["end_at"], max((e.to_dict()["sequence"] for e in runner.tape.events), default=0)
    )
    account = account_view(
        final,
        runner.tape,
        tuple(FrozenJSON.freeze(v) for v in r["position_history"]["evidence"]),
        end_clock,
        FrozenJSON.freeze(source["valuation_policy"]),
        historical_context=runner.historical_input,
    ).to_dict()
    assert runner.historical_input is not None
    historical = runner.historical_input.to_dict()
    request = source["request"]
    decisions = {}
    admissions = {}
    reservations = {}
    executions: dict[str, list[dict[str, Any]]] = {}
    releases: dict[str, list[dict[str, Any]]] = {}
    for event in r["events"]:
        for item in event.get("decisions", []):
            decisions[item["decision_id"]] = item
        for item in event.get("candidates", []):
            admissions[item["decision_id"]] = item
        eligible = [a for a in event.get("candidates", []) if a["virtual_order_eligible"]]
        require(len(eligible) == len(event.get("admissions", [])), "portfolio_reservation_coverage")
        for candidate, accepted in zip(eligible, event.get("admissions", []), strict=True):
            reservations[candidate["decision_id"]] = accepted
        for item in event.get("executions", []):
            if "order_id" in item:
                executions.setdefault(item["order_id"], []).append(item)
        for item in event.get("releases", []):
            if "order_id" in item:
                releases.setdefault(item["order_id"], []).append(item)
    by_analysis = {
        (item["result"]["ticker"], item["result"]["analysis_id"]): item
        for item in decisions.values()
    }
    public, observations = {}, {}
    for artifact in runner.analyses:
        bundle = artifact.bundle.to_dict()
        public[artifact.analysis_id] = bundle["signal"]
        observations[artifact.analysis_id] = bundle["created_at"]["signal"]
    source_events = {e.identifier: e.to_dict() for e in runner.tape.events}
    points = {(row["trading_date"], row["ticker"]): row for row in historical["decision_points"]}
    timeline = []
    for day in request["config"]["decision_dates"]:
        for ticker in request["config"]["admission_policy"]["ordered_tickers"]:
            point = points.get((day, ticker))
            reason = point["reason"] if point else "missing_historical_data"
            aid = point["analysis_id"] if point else None
            item = by_analysis.get((ticker, aid))
            decision = item["result"] if item else None
            signal = (
                item["input"]["analysis"]
                if item
                else (public.get(aid, {}) if aid is not None else {})
            )
            admission = admissions.get(item["decision_id"]) if item else None
            reservation = reservations.get(item["decision_id"]) if item else None
            oid = (
                reservation["order_id"]
                if reservation and reservation["status"] == "accepted"
                else None
            )
            direction = {"prior_decline": "up", "prior_rise": "down"}.get(
                signal.get("context") or ""
            )
            contributions = signal.get("reasons", {}).get(direction, {}) if direction else {}
            blocked = [
                v["reason"]
                for event in r["events"]
                for v in event.get("blocked", [])
                if v["ticker"] == ticker
                and point
                and point["available_at"] is not None
                and utc(event["cutoff"]) == utc(point["available_at"])
            ]
            codes = (
                decision["reason_codes"]
                if decision
                else blocked
                or [
                    reason
                    if not point or point["status"] != "available"
                    else "decision_not_recorded"
                ]
            )
            timeline.append(
                {
                    "opportunity_id": digest([day, ticker, aid]),
                    "trading_date": day,
                    "ticker": ticker,
                    "source_sequence": point["sequence"] if point else None,
                    "opportunity_at": point["available_at"] if point else None,
                    "analysis_id": aid,
                    "analysis_available_at": point["available_at"] if aid and point else None,
                    "analysis_anchor_at": signal.get("anchor_at"),
                    "analysis_observed_at": observations.get(aid) if aid else None,
                    "model_id": signal.get("model_version"),
                    "snapshot_id": signal.get("snapshot_id"),
                    "model_created_at": signal.get("model_created_at"),
                    "source_observed_at": signal.get("latest_collected_at"),
                    "model_family": request["model_family"],
                    "signal_evidence_scope": signal.get("reasons", {})
                    .get("fixture", {})
                    .get("kind", "public_model_inference")
                    if aid
                    else None,
                    "context": signal.get("context"),
                    "score": signal.get(f"{direction}_score") if direction else None,
                    "thresholds": runner.decision_policy.to_dict()["thresholds"],
                    "score_semantics": "conditional_uncalibrated_score",
                    "feature_values": signal.get("feature_values"),
                    "feature_contributions": contributions.get("contributions"),
                    "linear_intercept": contributions.get("intercept"),
                    "hgb_pair": signal.get("hgb_provenance", {}).get("model_pair"),
                    "hgb_provenance_hash": digest(signal["hgb_provenance"])
                    if "hgb_provenance" in signal
                    else None,
                    "decision_id": item["decision_id"] if item else None,
                    "decision_at": decision["decision_as_of"] if decision else None,
                    "action": decision["action"] if decision else None,
                    "selected_rule": decision["selected_rule"] if decision else None,
                    "requested_quantity": decision["proposed_quantity"] if decision else None,
                    "reasons": [explanation(code) for code in codes],
                    "checks": decision["checks"] if decision else None,
                    "conditions": condition_reasons(
                        decision, signal, runner.decision_policy.to_dict()
                    )
                    if decision
                    else None,
                    "account_at_decision": item["input"]["account"] if item else None,
                    "admission": {
                        k: admission.get(k)
                        for k in ("status", "reason", "order_id", "admission_at", "budget_krw")
                    }
                    if admission
                    else None,
                    "reservation": {
                        "status": reservation["status"],
                        "reason": reservation.get("reason"),
                        "order_id": oid,
                        "accepted_at": reservation.get("accepted_order", {}).get("accepted_at"),
                        "source_hash": digest(reservation),
                    }
                    if reservation
                    else None,
                    "order_id": oid,
                    "executions": [
                        {
                            "status": x["status"],
                            "reason": x.get("reason"),
                            "source_hash": digest(x),
                            "source_event": {
                                k: source_events[x["fill"]["source_event_id"]][k]
                                for k in (
                                    "event_at",
                                    "available_at",
                                    "sequence",
                                    "trading_date",
                                    "source_received_at",
                                )
                            }
                            if x["status"] == "filled"
                            else None,
                            "exchange_execution_at": None,
                            "exchange_time_unavailable_reason": (
                                "daily_bar_has_no_verified_exchange_execution_timestamp"
                            ),
                            "fill": fill_view(x["fill"], x["fill_hash"])
                            if x["status"] == "filled"
                            else None,
                        }
                        for x in (executions.get(oid, []) if oid is not None else [])
                    ],
                    "releases": (releases.get(oid, []) if oid is not None else []),
                    "source": {
                        "run_hash": run.identifier,
                        "historical_input_hash": historical and runner.historical_input.identifier,
                        "point_hash": digest(point) if point else None,
                    },
                    "unavailable": {
                        "feature_contributions": "hgb_attribution_not_provided"
                        if request["model_family"] == "hgb"
                        else "not_present_in_public_signal"
                    }
                    if contributions.get("contributions") is None
                    else {},
                }
            )
    for row in timeline:
        for key, value in row.items():
            if value is None:
                row["unavailable"].setdefault(key, "source_field_unavailable")
    positions = []
    completed = {x["position_id"]: x for x in p["trade_episodes"]}
    marks = {x["ticker"]: x for x in p["final_positions"]}
    previous: dict[str, str] = {}
    with localcontext(Context(prec=80)):
        closed_by_ticker = {
            t: sum(
                (Decimal(x["realized_pnl"]) for x in p["trade_episodes"] if x["ticker"] == t),
                Decimal(0),
            )
            for t in request["tickers"]
        }
        for episode in r["position_history"]["episodes"]:
            acquisition = episode["acquisition"]
            entry = r["final_account"]["fills"].get(acquisition["fill_id"])
            sales = [
                {
                    **ref,
                    "fill": fill_view(r["final_account"]["fills"][ref["fill_id"]], ref["fill_id"]),
                }
                for ref in episode["exits"]
            ]
            entry_at = entry["fill_at"] if entry else None
            exit_at = sales[-1]["fill_at"] if episode["status"] == "confirmed_full_exit" else None
            trade = completed.get(episode["position_id"])
            realized = (
                trade["realized_pnl"]
                if trade
                else str(
                    Decimal(account["realized_pnl_by_ticker"].get(episode["ticker"], "0"))
                    - closed_by_ticker[episode["ticker"]]
                )
            )
            positions.append(
                {
                    **episode,
                    "acquired_at": entry_at,
                    "exited_at": exit_at,
                    "holding_duration": duration(entry_at, exit_at),
                    "verified_trading_sessions": session_duration(
                        entry_at,
                        exit_at,
                        list(decisions.values()),
                        runner.tape.quality.to_dict(),
                        data_origin=runner.tape.manifest.to_dict()["data_origin"],
                    ),
                    "acquisition_fill": fill_view(entry, acquisition["fill_id"]) if entry else None,
                    "sales": sales,
                    "realized_net_pnl": realized,
                    "realized_source": "performance.trade_episodes"
                    if trade
                    else "ledger_ticker_delta_less_completed_episodes",
                    "valuation": marks.get(episode["ticker"]) if episode["quantity"] else None,
                    "previous_completed_position_id": previous.get(episode["ticker"]),
                    "source": {
                        "history_hash": r["position_history_hash"],
                        "performance_hash": performance.identifier,
                    },
                }
            )
            if trade:
                previous[episode["ticker"]] = episode["position_id"]
    initial, frames = verified_frames(r, runner.tape, final)
    states = [initial, *[s for _, s in frames], final]
    require(len(states) == len(p["equity_curve"]), "portfolio_curve_coverage")
    curve = [
        {
            **point,
            "available_cash_krw": state.to_dict()["available_cash_krw"],
            "reserved_cash_krw": state.to_dict()["reserved_cash_krw"],
            "positions": point["positions"],
            "ledger_hash": state.identifier,
        }
        for point, state in zip(p["equity_curve"], states, strict=True)
    ]
    details = FrozenJSON.freeze(
        {
            "version": VERSION,
            "run_id": r["run_id"],
            "tickers": request["tickers"],
            "priority": request["config"]["admission_policy"]["ordered_tickers"],
            "scope": request["evidence_scope"],
            "information_mode": "historical_research",
            "data_origin": runner.tape.manifest.to_dict()["data_origin"],
            "usage_restriction": r["usage_restriction"],
            "pit_verified": False,
            "oos_verified": False,
            "model_training_cutoff_verified": False,
            "revision_history_available": False,
            "liquidity_verified": False,
            "full_fill_assumption": True,
            "operational_eligible": False,
            "executable": False,
            "sources": {
                "frozen_run_hash": document.identifier,
                "historical_input_hash": runner.historical_input.identifier,
                "run_hash": run.identifier,
                "performance_hash": performance.identifier,
                "position_history_hash": r["position_history_hash"],
                "request_hash": FrozenJSON.freeze(request).identifier,
            },
            "timeline": timeline,
            "positions": positions,
            "terminal_orders": [
                {
                    "order_id": oid,
                    **row["current"],
                    "executions": (executions.get(oid, []) if oid is not None else []),
                    "releases": (releases.get(oid, []) if oid is not None else []),
                    "unavailable_reason": "no_later_eligible_fill_confirmed"
                    if row["current"]["status"] in {"open", "partial"}
                    else None,
                }
                for oid, row in sorted(r["final_account"]["orders"].items())
            ],
            "unaccepted_order_requests": [
                row["opportunity_id"]
                for row in timeline
                if row["order_id"] is None
                and (
                    row["action"] in {"BUY", "SELL"}
                    or (
                        row["admission"] is not None
                        and row["admission"]["reason"] != "no_order_action"
                    )
                )
            ],
            "final_account": r["final_account"],
            "realized_pnl_by_ticker": account["realized_pnl_by_ticker"],
            "account_timeline": curve,
            "final_valuation": account["valuation"],
            "totals": {
                k: p[k] for k in ("ledger_realized_pnl", "unrealized_pnl", "final_equity", "costs")
            },
            "unavailable_inputs": source["unavailable"],
            "limitations": sorted(
                set(
                    p["limitations"]
                    + [
                        "explicit_five_security_selection_not_historical_blind_selection",
                        "conditional_scores_not_calibrated_probabilities",
                        "feature_contributions_not_economic_causation",
                        "expanded_legacy_trade_receipts_can_dominate_memory",
                        "calendar_durations_use_recorded_fill_times",
                        "daily_event_time_is_publication_assumption_not_exchange_open",
                    ]
                )
            ),
        }
    )
    return FrozenJSON.freeze(
        {
            "version": "five_ticker_historical_result_v1",
            "frozen_input": source,
            "run": r,
            "run_hash": run.identifier,
            "performance": p,
            "performance_hash": performance.identifier,
            "detailed_report": details.to_dict(),
            "detailed_report_hash": details.identifier,
            "report": render_five_ticker(details),
        }
    )


def render_five_ticker(report: FrozenJSON) -> str:
    d = report.to_dict()
    return (
        f"Scope: {d['scope']}\nMode: historical_research; PIT/OOS verified: false\n"
        f"Tickers: {', '.join(d['tickers'])}\n"
        f"Shared account equity: {d['totals']['final_equity']}\n"
        f"Realized net P&L: {d['totals']['ledger_realized_pnl']}\n"
        + "\n".join(
            f"{row['trading_date']} {row['ticker']} {row['action'] or 'UNAVAILABLE'}: "
            + "; ".join(x["text"] for x in row["reasons"])
            for row in d["timeline"]
        )
        + "\n"
    )


def validate_five_ticker_report(report: FrozenJSON, frozen: FrozenJSON) -> None:
    expected = replay_five_ticker(frozen).to_dict()
    require(report.to_dict() == expected["detailed_report"], "portfolio_report_projection_conflict")
