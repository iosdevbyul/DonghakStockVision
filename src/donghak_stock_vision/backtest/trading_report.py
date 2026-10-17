"""Versioned factual projections of the existing research runner and accounting.

The public entry point computes/validates the existing run and Performance once.
Projection never loads a DB, fits a model, changes a decision, or calculates P&L.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.ledger import restore
from donghak_stock_vision.backtest.performance import calculate_performance
from donghak_stock_vision.backtest.position_history import build_position_history
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.validation import require, utc
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL

VERSION = "historical_trading_report_v1"
MESSAGES = {
    "entry_condition": "The applicable up score met the configured BUY threshold.",
    "exit_condition": "The applicable down score met the configured SELL threshold.",
    "existing_position": "A long position already exists; additional purchases are unsupported.",
    "no_position": "There is no long position to sell.",
    "entry_context_mismatch": "The research context has no applicable entry signal.",
    "exit_context_mismatch": (
        "There is no applicable SELL signal in this context; no profit-taking rule is implied."
    ),
    "entry_threshold_not_met": "The applicable up score did not meet the BUY threshold.",
    "exit_threshold_not_met": "The applicable down score did not meet the SELL threshold.",
    "insufficient_cash": "Available cash was insufficient for the proposed purchase.",
    "pending_order": "An existing pending order prevented a new candidate.",
    "concurrent_reservation_unsupported": (
        "An existing reservation prevented another order reservation."
    ),
    "quality_unaccepted": "The configured analysis quality requirement was not met.",
    "market_quality_unavailable": "The market quality requirement was not met.",
    "no_order_action": "HOLD/WAIT did not produce an order candidate.",
    "missing_decision_bar": "No market bar exists for this requested decision date.",
    "missing_historical_data": "Historical market data for this ticker is unavailable.",
    "accepted_not_filled": "The virtual order was accepted but has no confirmed fill.",
    "no_later_market_event_in_run": (
        "No later market event for this ticker exists inside the run period."
    ),
    "missing_mark": "The required valuation mark was unavailable.",
    "stale_mark": "The valuation mark exceeded the configured freshness limit.",
    "insufficient_history": "Research features were unavailable because history was insufficient.",
    "missing_model": "The required research model artifact was unavailable.",
    "insufficient_model_data": "The applicable direction has no fitted model parameters.",
    "analysis_not_released": "The analysis was not available at the opportunity cutoff.",
    "reservation_shortfall": "The later fill cost exceeded the precommitted reservation.",
    "order_quantity_limit": "The requested quantity exceeded the admission quantity limit.",
    "not_evaluated": "There is insufficient evaluated evidence to explain this condition.",
}


def explanation(code: str) -> dict[str, str]:
    if code.startswith("analysis_unavailable:"):
        message = "Research inputs were unavailable: " + code.split(":", 1)[1] + "."
    else:
        message = MESSAGES.get(code, "The source record reports: " + code + ".")
    return {"code": code, "text": message}


def condition_reasons(
    decision: dict[str, Any], signal: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Describe checked conditions, never supply an alternative trading rule."""
    if decision["status"] != "virtual":
        codes = decision["blocking_reasons"] or ["not_evaluated"]
        return {"entry": explanation(codes[0]), "exit": explanation("not_evaluated")}
    held = decision["position_state"] == "long"
    context = signal.get("context")
    entry = "existing_position" if held else "entry_context_mismatch"
    exit_reason = "no_position" if not held else "exit_context_mismatch"
    if decision["action"] == "BUY":
        entry = "entry_condition"
    elif not held and context == "prior_decline":
        entry = "entry_threshold_not_met"
    if decision["action"] == "SELL":
        exit_reason = "exit_condition"
    elif held and context == "prior_rise":
        exit_reason = "exit_threshold_not_met"
    return {"entry": explanation(entry), "exit": explanation(exit_reason)}


def duration(start: str | None, end: str | None) -> dict[str, Any]:
    if start is None or end is None:
        return {
            "calendar_days": None,
            "elapsed_microseconds": None,
            "reason": "acquisition_time_unknown" if start is None else "exit_not_confirmed",
        }
    a, b = utc(start), utc(end)
    require(a <= b, "report_holding_time_reversed")
    delta = b - a
    return {
        "calendar_days": (b.astimezone(SEOUL).date() - a.astimezone(SEOUL).date()).days,
        "elapsed_microseconds": (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds,
        "reason": None,
    }


def session_duration(
    start: str | None,
    end: str | None,
    decisions: list[dict[str, Any]],
    tape_quality: dict[str, Any],
    *,
    data_origin: str = "synthetic",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": None,
        "basis": "after_entry_through_exit",
        "reason": "verified_session_evidence_unavailable",
        "source": None,
    }
    result["evidence_scope"] = "synthetic_virtual_calendar_contract"
    if data_origin != "synthetic":
        result.update(
            reason="independent_calendar_attestation_unavailable",
            evidence_scope="independent_real_calendar_unavailable",
        )
        return result
    if start is None or end is None:
        result["reason"] = "holding_timestamps_unavailable"
        return result
    if tape_quality.get("research_calendar") is not None:
        result["reason"] = "research_exclusions_are_not_verified_sessions"
        return result
    first, last = utc(start).astimezone(SEOUL).date(), utc(end).astimezone(SEOUL).date()
    for item in decisions:
        market = item["input"]["market"]
        coverage, sessions = market.get("calendar_coverage"), market.get("sessions")
        if market.get("calendar_verified") is not True or not coverage or not sessions:
            continue
        days = [date.fromisoformat(day) for day in sessions]
        require(
            days == sorted(set(days)) and all(d.weekday() < 5 for d in days),
            "report_invalid_verified_sessions",
        )
        if (
            date.fromisoformat(coverage["start"])
            <= first
            <= last
            <= date.fromisoformat(coverage["end"])
        ):
            # This is the existing declared virtual calendar contract, not PIT approval.
            result.update(
                count=sum(first < day <= last for day in days),
                reason=None,
                source={"decision_id": item["decision_id"], "market_hash": digest(market)},
            )
            break
    return result


def fill_view(fill: dict[str, Any], identifier: str) -> dict[str, Any]:
    keys = (
        "ticker quantity price_krw notional_krw fee_krw tax_krw slippage_krw "
        "benchmark_price_krw fill_at fill_known_at source_event_id order_id sequence"
    ).split()
    return {
        "fill_id": identifier,
        **{k: fill.get(k) for k in keys},
        "unavailable": {k: "source_field_unavailable" for k in keys if fill.get(k) is None},
    }


@dataclass(frozen=True)
class ReportedBacktest:
    run: FrozenJSON
    performance: FrozenJSON
    details: FrozenJSON


def report_backtest(runner: BacktestRunner, valuation_policy: FrozenJSON) -> ReportedBacktest:
    """Run existing engines once, validate Performance, then project compact facts."""
    run = runner.run()
    performance = calculate_performance(
        run, runner.tape, valuation_policy, historical_input=runner.historical_input
    )
    return ReportedBacktest(run, performance, _project(runner, run, performance))


def validate_report(
    details: FrozenJSON, runner: BacktestRunner, valuation_policy: FrozenJSON
) -> None:
    """Explicit integrity/replay check; hashes alone are not external evidence approval."""
    require(
        details == report_backtest(runner, valuation_policy).details, "report_projection_conflict"
    )


def _project(runner: BacktestRunner, run: FrozenJSON, performance: FrozenJSON) -> FrozenJSON:
    run_hash, performance_hash = run.identifier, performance.identifier
    historical_hash = runner.historical_input.legacy_identifier if runner.historical_input else None
    r, p, config = run.to_dict(), performance.to_dict(), runner.config.to_dict()
    require(len(config["tickers"]) == 1, "single_ticker_detailed_report_required")
    policy = r["decision_policy"]
    releases = {
        a["analysis_id"]: a for a in runner.tape.release_plan.to_dict()["analysis_releases"]
    }
    events = {e.to_dict()["sequence"]: e.to_dict() for e in runner.tape.events}
    reports = {e["sequence"]: e for e in r["events"]}
    account = r["final_account"]
    history = r.get("position_history")
    if history is None:
        history = build_position_history(
            restore(FrozenJSON.freeze(r["final_checkpoint"])), runner.tape, (), cutoff=r["end_at"]
        ).to_dict()
    all_decisions = [d for e in r["events"] for d in e.get("decisions", [])]
    acceptance_index: dict[str, dict[str, Any]] = {}
    executions: dict[str, list[dict[str, Any]]] = {}
    active: str | None = None
    for event in r["events"]:
        for execution in event.get("executions", []):
            order_id = execution.get("order_id") or active
            require(order_id is not None, "report_execution_order_unknown")
            assert isinstance(order_id, str)
            executions.setdefault(order_id, []).append(
                {
                    "attempt_sequence": event["sequence"],
                    "attempt_at": event["cutoff"],
                    "status": execution["status"],
                    "reason": execution.get("reason"),
                    "fill": fill_view(execution["fill"], execution["fill_hash"])
                    if execution["status"] == "filled"
                    else None,
                    "execution_session": execution.get("execution_session"),
                    "source": {"run_hash": run_hash, "event_sequence": event["sequence"]},
                }
            )
            if execution["status"] == "filled":
                active = None
        for acceptance in event.get("admissions", []):
            if acceptance["status"] == "accepted":
                acceptance_index[acceptance["order_id"]] = acceptance
                active = acceptance["order_id"]

    timeline = []
    for step in config["decision_steps"]:
        sequence, ticker = step["sequence"], step["ticker"]
        event = reports.get(sequence)
        if event is None:  # Opportunity is outside the selected run period.
            continue
        decisions = [d for d in event.get("decisions", []) if d["result"]["ticker"] == ticker]
        require(len(decisions) <= 1, "report_ambiguous_decision")
        item = decisions[0] if decisions else None
        d = item["result"] if item else None
        signal: dict[str, Any] = item["input"]["analysis"] if item else {}
        candidates = [
            c
            for c in event.get("candidates", [])
            if item and c["decision_id"] == item["decision_id"]
        ]
        require(len(candidates) <= 1, "report_ambiguous_admission")
        candidate = candidates[0] if candidates else None
        reservations = (
            event.get("admissions", []) if candidate and candidate["virtual_order_eligible"] else []
        )
        require(len(reservations) <= 1, "report_ambiguous_reservation")
        reservation = reservations[0] if reservations else None
        opened = reservation.get("accepted_order") if reservation else None
        oid = reservation["order_id"] if reservation and opened else None
        blocked = [x["reason"] for x in event.get("blocked", []) if x["ticker"] == ticker]
        if event["status"] == "failed":
            blocked.append(event["reason"])
        direction = {"prior_decline": "up", "prior_rise": "down"}.get(signal.get("context") or "")
        costs = signal.get("reasons", {}).get(direction, {}) if direction else {}
        reasons = d["reason_codes"] if d else blocked or ["decision_not_recorded"]
        timeline.append(
            {
                "opportunity_id": digest([sequence, ticker, step["analysis_id"]]),
                "sequence": sequence,
                "ticker": ticker,
                "trading_date": events[sequence]["trading_date"],
                "opportunity_cutoff": event["cutoff"],
                "opportunity_time_basis": "runner_event_cutoff",
                "decision_cutoff": d["decision_as_of"] if d else None,
                "analysis_id": step["analysis_id"],
                **(
                    {
                        "hgb_provenance": signal["hgb_provenance"],
                        "feature_values": signal["feature_values"],
                        "evidence_observed_at": item["input"]["provenance"]["evidence_observed_at"]
                        if item
                        else None,
                    }
                    if "hgb_provenance" in signal
                    else {}
                ),
                "model_id": signal.get("model_version"),
                "snapshot_id": signal.get("snapshot_id"),
                "analysis_anchor_at": signal.get("anchor_at"),
                "analysis_available_at": releases[step["analysis_id"]]["available_at"],
                "model_created_at": signal.get("model_created_at"),
                "latest_source_collected_at": signal.get("latest_collected_at"),
                "analysis_input_hash": signal.get("input_hash"),
                "context": signal.get("context"),
                "applicable_direction": direction,
                "score": signal.get(f"{direction}_score") if direction else None,
                "thresholds": policy["thresholds"],
                "score_semantics": "conditional_uncalibrated_score",
                "feature_contributions": costs.get("contributions"),
                "linear_intercept": costs.get("intercept"),
                "action": d["action"] if d else None,
                "decision_status": d["status"] if d else "not_recorded",
                "decision_id": item["decision_id"] if item else None,
                "selected_rule": d["selected_rule"] if d else None,
                "reasons": [explanation(code) for code in reasons],
                "runner_blocking_reasons": blocked,
                "conditions": condition_reasons(d, signal, policy)
                if d
                else {"entry": explanation("not_evaluated"), "exit": explanation("not_evaluated")},
                "checks": d["checks"] if d else {},
                "requested_quantity": d["proposed_quantity"] if d else None,
                "estimated_cost": d["estimated_cost"] if d else None,
                "account_at_decision": {
                    k: item["input"]["account"].get(k)
                    for k in ("quantity", "available_cash_krw", "reserved_cash_krw", "equity_krw")
                }
                if item
                else None,
                "admission": {
                    "status": candidate["status"],
                    "reason": candidate["reason"],
                    "candidate_id": candidate.get("order_id"),
                    "at": candidate["admission_at"],
                }
                if candidate
                else {"status": "not_recorded", "reason": "admission_not_recorded"},
                "reservation": {
                    "status": reservation["status"],
                    "reason": reservation.get("reason"),
                    "at": opened.get("accepted_at") if opened else None,
                    "event_id": reservation["reservation_checkpoint"]["events"][-1]["event_id"]
                    if opened
                    else None,
                }
                if reservation
                else {
                    "status": "not_attempted",
                    "reason": blocked[0] if blocked else "no_accepted_candidate",
                },
                "order_id": oid,
                "submitted_at": opened.get("submitted_at") if opened else None,
                "executions": executions.get(oid, []) if oid else [],
                "unavailable": {
                    "feature_contributions": "hgb_attribution_not_provided"
                    if "hgb_provenance" in signal
                    else "not_present_in_public_signal"
                }
                if costs.get("contributions") is None
                else {},
                "source": {
                    "run_hash": run_hash,
                    "event_sequence": sequence,
                    "input_bundle_id": d["input_bundle_id"] if d else None,
                    "policy_id": d["policy_id"] if d else None,
                    "ledger_checkpoint_after_event": event["checkpoint_hash"],
                },
            }
        )

    # Include unavailable historical opportunities that never entered the Decision Engine.
    if runner.historical_input is not None:
        original = runner.historical_input.to_dict()
        known = {(row["sequence"], row["ticker"]) for row in timeline}
        for point in original["decision_points"]:
            if (point["sequence"], point["ticker"]) in known:
                continue
            timeline.append(
                {
                    "opportunity_id": digest(point),
                    **{k: point[k] for k in ("sequence", "ticker", "trading_date", "analysis_id")},
                    "opportunity_cutoff": point["available_at"],
                    "opportunity_time_basis": "historical_input_release_assumption",
                    "decision_cutoff": None,
                    "action": None,
                    "decision_status": "not_evaluated",
                    "executions": [],
                    "reasons": [
                        explanation(
                            point["reason"]
                            if point["status"] != "available"
                            else "decision_not_scheduled_or_context_unavailable"
                        )
                    ],
                    "unavailable": {"decision": "no_decision_record"},
                    "source": {"historical_input_document_hash": historical_hash},
                }
            )
        for ticker in original["missing_tickers"]:
            timeline.append(
                {
                    "opportunity_id": digest([ticker, "missing_historical_data"]),
                    "ticker": ticker,
                    "sequence": None,
                    "decision_cutoff": None,
                    "action": None,
                    "decision_status": "not_evaluated",
                    "executions": [],
                    "reasons": [explanation("missing_historical_data")],
                    "opportunity_cutoff": None,
                    "opportunity_time_basis": None,
                    "unavailable": {"decision_cutoff": "no_market_event"},
                    "source": {"historical_input_document_hash": historical_hash},
                }
            )
    timeline.sort(
        key=lambda row: (
            utc(row["opportunity_cutoff"]) if row["opportunity_cutoff"] else utc(r["end_at"]),
            row["sequence"] or 0,
            row["ticker"],
            row["opportunity_id"],
        )
    )
    fields = (
        "trading_date analysis_id model_id snapshot_id analysis_anchor_at analysis_input_hash "
        "analysis_available_at model_created_at latest_source_collected_at "
        "context applicable_direction score thresholds score_semantics feature_contributions "
        "linear_intercept action decision_id selected_rule checks requested_quantity "
        "estimated_cost account_at_decision admission reservation order_id submitted_at "
        "conditions runner_blocking_reasons"
    ).split()
    for row in timeline:
        for field in fields:
            row.setdefault(field, None)
        for field, value in row.items():
            if value is None:
                row["unavailable"].setdefault(field, "source_field_unavailable")
    completed = {e["position_id"]: e for e in p["trade_episodes"]}
    final_marks = {v["ticker"]: v for v in p["final_positions"]}
    positions = []
    previous: dict[str, str] = {}
    source_episodes = history.get("episodes", [])
    for episode in source_episodes:
        acquisition = episode["acquisition"]
        entry = account["fills"].get(acquisition["fill_id"])
        exits = [
            fill_view(account["fills"][ref["fill_id"]], ref["fill_id"])
            | {"decision_id": ref["decision_id"]}
            for ref in episode["exits"]
        ]
        start = entry.get("fill_at") if entry else None
        for sale in exits:
            sale["holding_duration"] = duration(start, sale["fill_at"])
        end = exits[-1]["fill_at"] if episode["status"] == "confirmed_full_exit" else None
        mark = final_marks.get(episode["ticker"]) if episode["quantity"] else None
        trade = completed.get(episode["position_id"])
        positions.append(
            {
                "position_id": episode["position_id"],
                "ticker": episode["ticker"],
                "status": episode["status"],
                "quantity": episode["quantity"],
                "previous_completed_position_id": previous.get(episode["ticker"]),
                "acquisition": {
                    **acquisition,
                    "fill": fill_view(entry, acquisition["fill_id"]) if entry else None,
                },
                "sales": exits,
                "acquired_at": start,
                "exited_at": end,
                "holding_duration": duration(start, end),
                "observed_duration_to_run_end": duration(start, r["end_at"])
                if end is None
                else None,
                "verified_trading_sessions": session_duration(
                    start,
                    end,
                    all_decisions,
                    runner.tape.quality.to_dict(),
                    data_origin=runner.tape.manifest.to_dict()["data_origin"],
                ),
                "realized_net_pnl": trade["realized_pnl"]
                if trade
                else p["open_episode_realized_pnl"],
                "realized_pnl_source": "performance.trade_episodes"
                if trade
                else "performance.open_episode_realized_pnl",
                "unrealized_pnl": mark["unrealized_pnl"] if mark else None,
                "valuation": mark,
                "unavailable": {"unrealized_pnl": "closed_position_not_marked"}
                if not mark
                else ({"unrealized_pnl": mark["reason"]} if mark["unrealized_pnl"] is None else {}),
                "source": {
                    "position_history_hash": r.get("position_history_hash"),
                    "performance_hash": performance_hash,
                    "ledger_hash": history.get("ledger_hash"),
                    "initial_account_hash": digest(
                        runner.tape.manifest.to_dict()["initial_account"]
                    )
                    if entry is None
                    else None,
                },
            }
        )
        if episode["status"] == "confirmed_full_exit":
            previous[episode["ticker"]] = episode["position_id"]

    linked_positions: dict[str, list[str]] = {}
    for episode in source_episodes:
        for ref in [episode["acquisition"], *episode["exits"]]:
            if ref.get("decision_id"):
                linked_positions.setdefault(ref["decision_id"], []).append(episode["position_id"])
    for row in timeline:
        row["position_ids"] = sorted(set(linked_positions.get(row["decision_id"], [])))
        if not row["position_ids"]:
            row["unavailable"]["position_ids"] = "no_explicit_trade_episode_for_this_decision"
    last_market_event: dict[str, Any] = {}
    for event in events.values():
        if utc(event["available_at"]) <= utc(r["end_at"]):
            last_market_event[event["ticker"]] = max(
                utc(event["available_at"]),
                last_market_event.get(event["ticker"], utc(event["available_at"])),
            )
    orders = []
    for oid, record in account["orders"].items():
        order = record["current"]
        last_at = last_market_event.get(order["ticker"])
        later = last_at is not None and last_at > utc(order["accepted_at"])
        attempts = executions.get(oid, [])
        reason = (
            None
            if order["status"] == "filled"
            else ("no_later_market_event_in_run" if not later else "accepted_not_filled")
        )
        orders.append(
            {
                "order_id": oid,
                "decision_id": record["original"]["decision_id"],
                "ticker": order["ticker"],
                "side": order["side"],
                "status": order["status"],
                "quantity": order["quantity"],
                "remaining_quantity": order["remaining_quantity"],
                "reserved_cash_krw": order["reserved_cash_krw"],
                "reserved_quantity": order["reserved_quantity"],
                "accepted_at": order["accepted_at"],
                "outcome_reason": explanation(reason) if reason else None,
                "executions": attempts,
                "reservation_hash": digest(acceptance_index[oid]["reservation_checkpoint"]),
                "source": {"run_hash": run_hash, "ledger_order_id": oid},
            }
        )
    orders.sort(key=lambda o: (utc(o["accepted_at"]), o["order_id"]))
    return FrozenJSON.freeze(
        {
            "schema_version": VERSION,
            "run_id": r["run_id"],
            "data_origin": runner.tape.manifest.to_dict()["data_origin"],
            "information_mode": r["information_mode"],
            "usage_restriction": r["usage_restriction"],
            "operational_eligible": False,
            "executable": False,
            "pit_verified": False,
            "oos_verified": False,
            "sources": {
                "run_hash": run_hash,
                "performance_hash": performance_hash,
                "tape_hash": runner.tape.identifier,
                "runner_config_hash": runner.config.identifier,
            },
            "timeline": timeline,
            "unaccepted_order_requests": [
                {
                    "opportunity_id": row["opportunity_id"],
                    "decision_id": row["decision_id"],
                    "requested_quantity": row["requested_quantity"],
                    "order_id": None,
                    "stage": "reservation"
                    if row["reservation"] and row["reservation"]["status"] == "rejected"
                    else "admission",
                    "outcome": row["reservation"]
                    if row["reservation"] and row["reservation"]["status"] == "rejected"
                    else row["admission"],
                }
                for row in timeline
                if row["action"] in {"BUY", "SELL"} and row["order_id"] is None
            ],
            "positions": positions,
            "terminal_orders": orders,
            "account_timeline": p["equity_curve"],
            "final_account": {
                k: account[k]
                for k in (
                    "available_cash_krw",
                    "reserved_cash_krw",
                    "total_cash_krw",
                    "realized_pnl_krw",
                    "positions",
                )
            },
            "totals": {
                k: p[k]
                for k in (
                    "realized_pnl",
                    "unrealized_pnl",
                    "ledger_realized_pnl",
                    "costs",
                    "final_equity",
                )
            },
            "limitations": p["limitations"]
            + [
                "conditional_scores_are_not_calibrated_probabilities",
                "feature_contributions_are_statistical_not_economic_causation",
                "post_event_available_cash_and_reservations_not_in_equity_curve",
                "calendar_duration_uses_recorded_fill_times_not_inferred_exchange_open",
            ],
        }
    )
