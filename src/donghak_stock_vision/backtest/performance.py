"""Decimal research accounting from verified execution history, never a strategy."""

from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.ledger import (
    LedgerCommand,
    LedgerState,
    apply,
    checkpoint,
    initialize,
    restore,
)
from donghak_stock_vision.backtest.position_history import (
    build_position_history,
    validate_position_history,
)
from donghak_stock_vision.backtest.validation import fields, integer, money, require, text, utc
from donghak_stock_vision.data.research_calendar import calendar_policy
from donghak_stock_vision.data.schema import SEOUL

ROUNDING = {"half_up": ROUND_HALF_UP, "down": ROUND_DOWN, "up": ROUND_UP}


def policy_values(policy: FrozenJSON) -> dict[str, Any]:
    p = fields(
        policy.to_dict(),
        "version field selection max_age_seconds money_quantum "
        "money_rounding ratio_quantum ratio_rounding schedule external_cash_flow_krw"
        + (" historical_marks" if "historical_marks" in policy.to_dict() else ""),
    )
    if "historical_marks" in p:
        require(p["historical_marks"] == "verified_daily_research", "unsupported_historical_marks")
    text(p["version"])
    require(p["field"] in {"open", "high", "low", "close"}, "unsupported_valuation_field")
    require(p["selection"] == "latest_public_sequence", "unsupported_mark_selection")
    require(p["schedule"] == "start_events_end", "unsupported_valuation_schedule")
    integer(p["max_age_seconds"])
    for key in ("money", "ratio"):
        p[key + "_quantum"] = money(p[key + "_quantum"], positive=True)
        require(Decimal(p[key + "_quantum"]).as_tuple().digits == (1,), "invalid_quantum")
        require(p[key + "_rounding"] in ROUNDING, "unsupported_rounding")
    p["external_cash_flow_krw"] = money(p["external_cash_flow_krw"], signed=True)
    require(Decimal(p["external_cash_flow_krw"]) == 0, "external_cash_flows_unsupported")
    return p


def rounded(value: Decimal, p: dict[str, Any], kind: str) -> str:
    return money(
        value.quantize(Decimal(p[kind + "_quantum"]), rounding=ROUNDING[p[kind + "_rounding"]]),
        signed=True,
    )


def valuation(
    state: LedgerState, tape: FrozenTape, clock: VirtualClock, p: dict[str, Any], stage: str
) -> dict[str, Any]:
    calendar = calendar_policy(tape.quality.to_dict())
    if calendar is not None and stage == "event":
        session = utc(clock.cutoff).astimezone(SEOUL).date() - timedelta(days=1)
        require(not calendar.excludes(session), "known_incomplete_trading_date")
    view = tape.view(clock).to_dict()
    positions = []
    known_value, known_unrealized = Decimal(0), Decimal(0)
    for ticker, pos in sorted(state.to_dict()["positions"].items()):
        if not pos["quantity"]:
            continue
        candidates = [
            e for e in view["items"] if e["ticker"] == ticker and p["field"] in e["public_fields"]
        ]
        row: dict[str, Any] = {
            **pos,
            "market_value": None,
            "unrealized_pnl": None,
            "mark": None,
            "status": "unvalued",
            "reason": "missing_mark",
        }
        if candidates:
            mark = max(candidates, key=lambda e: e["sequence"])
            release = next(
                r
                for r in tape.release_plan.to_dict()["releases"]
                if r["sequence"] == mark["sequence"]
            )
            published = release["field_times"][p["field"]]
            event = next(
                e.to_dict() for e in tape.events if e.to_dict()["sequence"] == mark["sequence"]
            )
            age = max(
                (utc(clock.cutoff) - utc(t)).total_seconds() for t in (mark["event_at"], published)
            )
            if age < 0 or age > p["max_age_seconds"]:
                row["reason"] = "stale_mark"
            elif (
                mark["adjustment"] != "unadjusted"
                or mark["quality_flags"]
                or utc(event["quality_available_at"]) > utc(clock.cutoff)
                or (
                    not (
                        p.get("historical_marks") == "verified_daily_research"
                        and event["quality_status"] == "verified"
                        and event["session"] == "closed"
                        and event["listing_status"] == "unknown"
                        and event["halt_status"] == "unknown"
                        and event["public_fields"]["volume"] > 0
                    )
                    and (event["listing_status"] != "listed" or event["halt_status"] != "trading")
                )
            ):
                row["reason"] = "mark_quality_unavailable"
            else:
                price = Decimal(money(mark["public_fields"][p["field"]], positive=True))
                value = Decimal(rounded(price * pos["quantity"], p, "money"))
                pnl = value - Decimal(pos["cost_basis_krw"])
                row.update(
                    status="valued",
                    reason=None,
                    market_value=str(value),
                    unrealized_pnl=money(pnl, signed=True),
                    mark={
                        "price": str(price),
                        "field": p["field"],
                        "sequence": mark["sequence"],
                        "event_at": mark["event_at"],
                        "available_at": published,
                    },
                )
                known_value += value
                known_unrealized += pnl
        positions.append(row)
    missing = sum(x["status"] != "valued" for x in positions)
    cash = Decimal(state.to_dict()["total_cash_krw"])
    return {
        "timestamp": clock.cutoff,
        "sequence": clock.sequence,
        "stage": stage,
        "cash": str(cash),
        "known_position_value": str(known_value),
        "total_equity": str(cash + known_value) if not missing else None,
        "unrealized_pnl": money(known_unrealized, signed=True) if not missing else None,
        "known_unrealized_pnl": money(known_unrealized, signed=True),
        "complete": not missing,
        "positions": positions,
        "coverage": {
            "valued_positions": len(positions) - missing,
            "open_positions": len(positions),
            "unvalued_positions": missing,
        },
    }


def verified_frames(
    run: dict[str, Any], tape: FrozenTape, final: LedgerState
) -> tuple[LedgerState, list[tuple[VirtualClock, LedgerState]]]:
    state = initialize(
        final.manifest,
        final.tape_hash,
        final.policy,
        final.events[0],
        cutoff=final.events[0].document.to_dict()["known_at"],
    )
    initial = state
    events = [
        e.to_dict()
        for e in tape.events
        if utc(run["start_at"]) <= utc(e.to_dict()["available_at"]) <= utc(run["end_at"])
    ]
    require(
        len(events) == len(run["events"]) == run["processed_event_count"],
        "run_event_coverage_mismatch",
    )
    frames = []
    previous: VirtualClock | None = None
    for event, report in zip(events, run["events"], strict=True):
        clock = VirtualClock.at(report["cutoff"], report["sequence"])
        require(
            clock.sequence == event["sequence"] and utc(clock.cutoff) == utc(event["available_at"]),
            "run_clock_mismatch",
        )
        if previous is not None:
            previous.advance(clock.cutoff, clock.sequence)
        previous = clock
        commands = []
        if report["status"] == "processed":
            for x in report["executions"]:
                if x["status"] == "filled":
                    commands.append(LedgerCommand(FrozenJSON.freeze(x["ledger_command"])))
            for a in report["admissions"]:
                if a["status"] == "accepted":
                    commands.append(
                        LedgerCommand(FrozenJSON.freeze(a["reservation_checkpoint"]["events"][-1]))
                    )
        else:
            require(report["status"] == "failed", "unknown_event_status")
        for command in sorted(commands, key=lambda e: e.document.to_dict()["sequence"]):
            require(command in final.events, "unverified_run_event")
            state = apply(state, command, cutoff=clock.cutoff)
        require(
            checkpoint(state).identifier == report["checkpoint_hash"], "event_checkpoint_mismatch"
        )
        frames.append((clock, state))
    require(state == final, "final_run_state_mismatch")
    return initial, frames


def episodes(
    history: dict[str, Any], final: LedgerState, p: dict[str, Any]
) -> list[dict[str, Any]]:
    records = []
    fills = final.to_dict()["fills"]
    for episode in history["episodes"]:
        if episode["status"] != "confirmed_full_exit":
            continue
        acquisition = episode["acquisition"]
        if acquisition["kind"] == "initial_account":
            position = next(
                row
                for row in final.manifest.to_dict()["initial_account"]["positions"]
                if row["ticker"] == episode["ticker"]
            )
            basis = Decimal(position["cost_basis_krw"])
            entry_at = None  # Acquisition time is not known for an initial holding.
        else:
            f = fills[acquisition["fill_id"]]
            basis = sum((Decimal(f[k]) for k in ("notional_krw", "fee_krw", "tax_krw")), Decimal(0))
            entry_at = f["fill_at"]
        proceeds = sum(
            (
                Decimal(fills[x["fill_id"]]["notional_krw"])
                - Decimal(fills[x["fill_id"]]["fee_krw"])
                - Decimal(fills[x["fill_id"]]["tax_krw"])
                for x in episode["exits"]
            ),
            Decimal(0),
        )
        pnl = proceeds - basis
        records.append(
            {
                "position_id": episode["position_id"],
                "ticker": episode["ticker"],
                "entry_reference": acquisition,
                "entry_at": entry_at,
                "exit_references": episode["exits"],
                "exit_at": episode["exits"][-1]["fill_at"],
                "economic_basis": str(basis),
                "net_exit_proceeds": str(proceeds),
                "realized_pnl": money(pnl, signed=True),
                "return_ratio": rounded(pnl / basis, p, "ratio") if basis > 0 else None,
                "return_limitation": None if basis > 0 else "nonpositive_episode_basis",
                "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
            }
        )
    return records


def drawdown(curve: list[dict[str, Any]], p: dict[str, Any]) -> dict[str, Any]:
    peak: Decimal | None = None
    peak_point = None
    worst = Decimal(0)
    worst_peak = worst_trough = None
    unavailable = 0
    for point in curve:
        if not point["complete"]:
            unavailable += 1
            continue
        equity = Decimal(point["total_equity"])
        if peak is None or equity > peak:
            peak, peak_point = equity, point
        if peak <= 0:
            unavailable += 1
            continue
        loss = (peak - equity) / peak
        if loss > worst:
            worst, worst_peak, worst_trough = loss, peak_point, point
    observed = rounded(worst, p, "ratio") if peak is not None and peak > 0 else None
    lower_bound = (
        money(worst.quantize(Decimal(p["ratio_quantum"]), rounding=ROUND_DOWN))
        if observed is not None
        else None
    )
    return {
        "ratio": observed if not unavailable else None,
        "observed_lower_bound": lower_bound,
        "complete": unavailable == 0 and observed is not None,
        "peak": None
        if worst_peak is None
        else {k: worst_peak[k] for k in ("timestamp", "sequence", "stage")},
        "trough": None
        if worst_trough is None
        else {k: worst_trough[k] for k in ("timestamp", "sequence", "stage")},
        "unavailable_points": unavailable,
    }


def calculate_performance(
    run_result: FrozenJSON,
    tape: FrozenTape,
    valuation_policy: FrozenJSON,
    *,
    historical_input: FrozenJSON | None = None,
) -> FrozenJSON:
    """Return immutable net research performance; require complete, verified fill lineage."""
    p = policy_values(valuation_policy)
    run = run_result.to_dict()
    historical = tape.manifest.to_dict()["data_origin"] == "real"
    if historical:
        from donghak_stock_vision.backtest.historical_execution import MODE, validate_input

        validate_input(tape, historical_input)
        assert historical_input is not None
        require(
            run.get("historical_input_hash") == historical_input.identifier,
            "performance_historical_input_mismatch",
        )
        require(run.get("execution_assumption") == MODE, "historical_execution_required")
        require(
            p.get("historical_marks") == "verified_daily_research",
            "historical_valuation_assumption_required",
        )
    else:
        require("historical_marks" not in p, "historical_marks_require_real_input")
    require(run["run_id"] == tape.manifest.identifier, "performance_run_mismatch")
    require(
        run["information_mode"] == "historical_research"
        and run["usage_restriction"] == ("research_only" if historical else "synthetic_test_only")
        and run["executable"] is False
        and run["operational_eligible"] is False,
        "research_performance_only",
    )
    require(
        tape.manifest.to_dict()["data_origin"] == ("real" if historical else "synthetic")
        and tape.manifest.to_dict()["information_mode"] == "historical_research"
        and tape.manifest.to_dict()["execution_mode"] == "backtest",
        "research_performance_only",
    )
    final = restore(FrozenJSON.freeze(run["final_checkpoint"]))
    require(
        final.manifest == tape.manifest and final.tape_hash == tape.identifier,
        "performance_tape_mismatch",
    )
    require(
        final.to_dict() == run["final_account"]
        and checkpoint(final).identifier == run["final_checkpoint_hash"],
        "final_account_mismatch",
    )
    require(utc(run["start_at"]) <= utc(run["end_at"]), "invalid_performance_period")
    if final.to_dict()["fills"]:
        require("position_history" in run, "verified_position_history_required")
        history = validate_position_history(
            FrozenJSON.freeze(run["position_history"]), final, tape, cutoff=run["end_at"]
        )
        require(
            FrozenJSON.freeze(history).identifier == run["position_history_hash"],
            "position_history_hash_mismatch",
        )
    else:
        history = build_position_history(final, tape, (), cutoff=run["end_at"]).to_dict()
    initial, frames = verified_frames(run, tape, final)
    require(utc(initial.to_dict()["known_at"]) <= utc(run["start_at"]), "future_initial_account")
    require(
        run["status"]
        == (
            "completed_with_failures"
            if any(e["status"] == "failed" for e in run["events"])
            else "completed"
        ),
        "run_status_mismatch",
    )

    def cursor(stamp: str, *, initial_point: bool = False) -> VirtualClock:
        at_boundary = [
            e.to_dict()["sequence"]
            for e in tape.events
            if utc(e.to_dict()["available_at"]) == utc(stamp)
        ]
        if initial_point and at_boundary:
            return VirtualClock.at(stamp, min(at_boundary))
        seq = max(
            (
                e.to_dict()["sequence"]
                for e in tape.events
                if utc(e.to_dict()["available_at"]) <= utc(stamp)
            ),
            default=0,
        )
        return VirtualClock.at(stamp, seq)

    with localcontext(Context(prec=80)):
        curve = [
            valuation(initial, tape, cursor(run["start_at"], initial_point=True), p, "initial")
        ]
        curve += [valuation(s, tape, clock, p, "event") for clock, s in frames]
        curve.append(valuation(final, tape, cursor(run["end_at"]), p, "final"))
        first, last = curve[0], curve[-1]
        require(
            first["total_equity"] is None or Decimal(first["total_equity"]) > 0,
            "nonpositive_initial_equity",
        )
        trades = episodes(history, final, p)
        realized = sum((Decimal(t["realized_pnl"]) for t in trades), Decimal(0))
        ledger_realized = Decimal(final.to_dict()["realized_pnl_krw"])
        costs = {"fees": Decimal(0), "sell_taxes": Decimal(0), "slippage_impact": Decimal(0)}
        counts = {"BUY": 0, "SELL": 0}
        for packet in history["evidence"]:
            f, side = packet["execution"]["fill"], packet["decision_result"]["action"]
            counts[side] += 1
            costs["fees"] += Decimal(f["fee_krw"])
            costs["sell_taxes"] += Decimal(f["tax_krw"]) if side == "SELL" else Decimal(0)
            costs["slippage_impact"] += Decimal(f["slippage_krw"])
        costs["total_explicit_trading_costs"] = costs["fees"] + costs["sell_taxes"]
        wins = sum(t["outcome"] == "win" for t in trades)
        losses = sum(t["outcome"] == "loss" for t in trades)
        complete = first["complete"] and last["complete"]
        total_return = (
            (
                Decimal(last["total_equity"])
                - Decimal(first["total_equity"])
                - Decimal(p["external_cash_flow_krw"])
            )
            / Decimal(first["total_equity"])
            if complete
            else None
        )
        mdd = drawdown(curve, p)
        limitations = [
            (
                "historical_research_not_pit_or_oos"
                if historical
                else "synthetic_historical_research_not_pit_or_oos"
            ),
            "mdd_on_declared_observation_points_not_intraday",
            "no_external_cash_flows_or_forced_liquidation",
        ]
        if historical:
            limitations += run["limitations"] + ["daily_marks_listing_and_halt_status_unverified"]
        if not first["complete"]:
            limitations.append("initial_valuation_incomplete")
        if not last["complete"]:
            limitations.append("final_valuation_incomplete")
        if not mdd["complete"]:
            limitations.append("incomplete_curve_mdd_is_only_observed_lower_bound")
        if run["status"] != "completed":
            limitations.append("run_contains_failed_events")
        return FrozenJSON.freeze(
            {
                "version": 1,
                "run_id": run["run_id"],
                "run_hash": run_result.identifier,
                "start_at": run["start_at"],
                "end_at": run["end_at"],
                "scope": "historical_research" if historical else "synthetic_historical_research",
                "executable": False,
                "operational_eligible": False,
                "valuation_policy": p,
                "initial_valuation": first,
                "final_valuation": last,
                "initial_equity": first["total_equity"],
                "final_equity": last["total_equity"],
                "total_return": rounded(total_return, p, "ratio")
                if total_return is not None
                else None,
                "external_cash_flow": p["external_cash_flow_krw"],
                "realized_pnl": money(realized, signed=True),
                "unrealized_pnl": last["unrealized_pnl"],
                "ledger_realized_pnl": str(ledger_realized),
                "open_episode_realized_pnl": money(ledger_realized - realized, signed=True),
                "mdd": mdd,
                "completed_trades": len(trades),
                "wins": wins,
                "losses": losses,
                "breakeven": len(trades) - wins - losses,
                "win_rate": rounded(Decimal(wins) / len(trades), p, "ratio") if trades else None,
                "fill_count": sum(counts.values()),
                "buy_fill_count": counts["BUY"],
                "sell_fill_count": counts["SELL"],
                "rejected_execution_count": sum(
                    x["status"] != "filled" for e in run["events"] for x in e.get("executions", [])
                ),
                "costs": {k: money(v) for k, v in costs.items()},
                "final_cash": last["cash"],
                "final_positions": last["positions"],
                "valuation_coverage": {
                    **last["coverage"],
                    "complete_points": sum(x["complete"] for x in curve),
                    "total_points": len(curve),
                },
                "equity_curve": curve,
                "trade_episodes": trades,
                "limitations": limitations,
            }
        )
