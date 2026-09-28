"""Verified virtual position lineage, derived only from replayed ledger and trade receipts."""

from typing import Any

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import VirtualFill, VirtualOrder
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.execution import execute
from donghak_stock_vision.backtest.ledger import LedgerState, apply, checkpoint, initialize, restore
from donghak_stock_vision.backtest.validation import fields, require, utc
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy, DecisionResult
from donghak_stock_vision.strategy.engine import decide


def verify_trade(
    packet: dict[str, Any],
    before: LedgerState,
    after: LedgerState,
    tape: FrozenTape,
    prefixes: dict[str, LedgerState],
    last_exits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    fields(
        packet,
        "decision_input decision_policy decision_result decision_ledger_hash "
        "analysis_bundle acceptance execution",
    )
    inputs = DecisionInput.from_dict(packet["decision_input"])
    policy = DecisionPolicy.from_dict(packet["decision_policy"])
    decision = DecisionResult.from_dict(packet["decision_result"])
    require(decide(inputs, policy) == decision, "history_decision_conflict")
    d, b = decision.to_dict(), inputs.to_dict()
    a, x = packet["acceptance"], packet["execution"]
    require(x["status"] == "filled", "history_unconfirmed_fill")
    fill = VirtualFill.from_dict(x["fill"])
    f = fill.to_dict()
    candidate = VirtualOrder.from_dict(a["admission"]["candidate"])
    opened = VirtualOrder.from_dict(a["accepted_order"])
    o = opened.to_dict()
    require(
        candidate.identifier == a["candidate_id"] == x["candidate_id"], "history_candidate_conflict"
    )
    require(opened.identifier == f["order_id"] == x["order_id"], "history_order_conflict")
    require(
        d["status"] == "virtual"
        and d["decision_scope"] == "research"
        and d["action"] == o["side"]
        and d["proposed_quantity"] == o["quantity"],
        "history_decision_order_conflict",
    )
    require(d["ticker"] == o["ticker"] == f["ticker"], "history_ticker_conflict")
    require(
        decision.identifier == o["decision_id"] == candidate.to_dict()["decision_id"],
        "history_decision_reference_conflict",
    )
    require(f["quantity"] == o["quantity"], "history_partial_fill_unsupported")
    require(
        fill.identifier in after.to_dict()["fills"]
        and after.to_dict()["fills"][fill.identifier] == f,
        "history_fill_reference_missing",
    )
    analysis = FrozenAnalysis(
        tape.manifest, tape.release_plan, FrozenJSON.freeze(packet["analysis_bundle"])
    )
    cursor = VirtualClock(**a["decision_clock"])
    require(analysis.view(cursor).to_dict()["status"] != "blocked", "history_future_analysis")
    require(
        analysis.bundle.to_dict()["signal"] == b["analysis"]
        and analysis.bundle.to_dict()["analysis_id"] == b["request"]["analysis_id"],
        "history_analysis_conflict",
    )
    require(packet["decision_ledger_hash"] in prefixes, "history_account_reference_missing")
    decision_state = prefixes[packet["decision_ledger_hash"]]
    ds = decision_state.to_dict()
    require(o["account_revision"] == ds["revision"], "history_account_revision_conflict")
    require(b["account"]["account_ref"] == ds["account_id"], "history_account_conflict")
    require(
        b["orders"]["history_complete"] is True
        and b["orders"]["last_exit"] == last_exits.get(o["ticker"]),
        "history_prior_exit_conflict",
    )
    pos = ds["positions"].get(o["ticker"], {"quantity": 0, "sellable_quantity": 0})
    for key in ("quantity", "sellable_quantity"):
        require(b["account"][key] == pos[key], "history_position_conflict")
    for key in ("available_cash_krw", "reserved_cash_krw"):
        require(b["account"][key] == ds[key], "history_cash_conflict")
    reserved = restore(FrozenJSON.freeze(a["reservation_checkpoint"]))
    require(tuple(reserved.events[:-1]) == decision_state.events, "history_reservation_conflict")
    events = [e for e in tape.events if e.identifier == f["source_event_id"]]
    require(len(events) == 1, "history_market_reference_missing")
    replay, replay_state = execute(
        FrozenJSON.freeze(a),
        before,
        tape,
        events[0],
        VirtualClock.at(f["fill_known_at"], x["market_sequence"]),
        expected_revision=reserved.to_dict()["revision"],
        ledger_sequence=f["sequence"],
        quantity=f["quantity"],
    )
    require(replay.to_dict() == x and replay_state == after, "history_execution_conflict")
    return {
        "decision_id": decision.identifier,
        "analysis_id": b["request"]["analysis_id"],
        "candidate_id": candidate.identifier,
        "order_id": opened.identifier,
        "reservation_id": reserved.events[-1].identifier,
        "fill_id": fill.identifier,
        "execution_id": x["fill_id"],
        "quantity": f["quantity"],
        "fill_at": f["fill_at"],
        "known_at": f["fill_known_at"],
        "side": o["side"],
        "ticker": o["ticker"],
    }


def build_position_history(
    ledger: LedgerState,
    tape: FrozenTape,
    evidence: tuple[FrozenJSON, ...],
    *,
    cutoff: str,
) -> FrozenJSON:
    """Complete verified history or an explicit validation error; never infer missing exits."""
    require(
        ledger.manifest == tape.manifest and ledger.tape_hash == tape.identifier,
        "history_run_mismatch",
    )
    require(utc(ledger.to_dict()["known_at"]) <= utc(cutoff), "history_future_state")
    require(restore(checkpoint(ledger)) == ledger, "history_ledger_conflict")
    packets: dict[str, FrozenJSON] = {}
    for packet in evidence:
        p = packet.to_dict()
        key = p["execution"]["fill_id"]
        require(key not in packets or packets[key] == packet, "history_id_conflict")
        packets[key] = packet
    state = initialize(
        ledger.manifest,
        ledger.tape_hash,
        ledger.policy,
        ledger.events[0],
        cutoff=ledger.events[0].document.to_dict()["known_at"],
    )
    prefixes = {state.identifier: state}
    episodes: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    exits: dict[str, dict[str, Any]] = {}
    for ticker, position in sorted(state.to_dict()["positions"].items()):
        if position["quantity"]:
            episode = {
                "position_id": digest({"initial": ledger.manifest.identifier, "ticker": ticker}),
                "ticker": ticker,
                "acquisition": {
                    "kind": "initial_account",
                    "reference": digest(ledger.manifest.to_dict()["initial_account"]),
                    "decision_id": None,
                    "fill_id": None,
                },
                "exits": [],
                "status": "still_open",
                "quantity": position["quantity"],
            }
            active[ticker] = episode
            episodes.append(episode)
    used: set[str] = set()
    seen_fills: set[str] = set()
    for command in ledger.events[1:]:
        e = command.document.to_dict()
        require(utc(e["known_at"]) <= utc(cutoff), "history_future_event")
        before = state
        state = apply(state, command, cutoff=e["known_at"])
        prefixes[state.identifier] = state
        if e["kind"] != "fill":
            continue
        fill = VirtualFill.from_dict(e["data"]["fill"])
        if fill.identifier in seen_fills:
            continue
        key = fill.to_dict()["cost_charge_id"]
        require(key in packets, "history_fill_evidence_missing")
        ref = verify_trade(packets[key].to_dict(), before, state, tape, prefixes, exits)
        used.add(key)
        seen_fills.add(fill.identifier)
        ticker = ref["ticker"]
        remaining = state.to_dict()["positions"][ticker]["quantity"]
        if ref["side"] == "BUY":
            require(ticker not in active, "history_pyramiding_conflict")
            episode = {
                "position_id": digest({"run": ledger.manifest.identifier, "acquisition": ref}),
                "ticker": ticker,
                "acquisition": {"kind": "confirmed_buy", **ref},
                "exits": [],
                "status": "still_open",
                "quantity": remaining,
            }
            active[ticker] = episode
            episodes.append(episode)
        else:
            require(ticker in active, "history_acquisition_missing")
            episode = active[ticker]
            episode["exits"].append(ref)
            episode["quantity"] = remaining
            episode["status"] = "partial_exit" if remaining else "confirmed_full_exit"
            if not remaining:
                position = state.to_dict()["positions"][ticker]
                require(
                    position["reserved_quantity"] == position["sellable_quantity"] == 0,
                    "history_exit_reservation_conflict",
                )
                exits[ticker] = {
                    "status": "completed",
                    "completed_at": ref["fill_at"],
                    "received_at": ref["known_at"],
                    "analysis_id": ref["analysis_id"],
                    "ticker": ticker,
                    "run_id": ledger.manifest.identifier,
                    "account_id": state.to_dict()["account_id"],
                    "position_id": episode["position_id"],
                    "exit_decision_id": ref["decision_id"],
                    "exit_fill_id": ref["fill_id"],
                }
                del active[ticker]
    require(used == set(packets), "history_unlinked_evidence")
    require(state == ledger, "history_final_state_conflict")
    return FrozenJSON.freeze(
        {
            "version": 1,
            "run_id": ledger.manifest.identifier,
            "account_id": state.to_dict()["account_id"],
            "revision": state.to_dict()["revision"],
            "ledger_hash": state.identifier,
            "known_at": state.to_dict()["known_at"],
            "complete": True,
            "episodes": episodes,
            "last_exits": exits,
            "evidence": [packets[k].to_dict() for k in sorted(packets)],
        }
    )


def validate_position_history(
    history: FrozenJSON,
    ledger: LedgerState,
    tape: FrozenTape,
    *,
    cutoff: str,
) -> dict[str, Any]:
    h = history.to_dict()
    require(h["run_id"] == ledger.manifest.identifier, "history_run_mismatch")
    require(h["account_id"] == ledger.to_dict()["account_id"], "history_account_mismatch")
    require(
        h["revision"] == ledger.to_dict()["revision"] and h["ledger_hash"] == ledger.identifier,
        "history_revision_mismatch",
    )
    verified = build_position_history(
        ledger, tape, tuple(FrozenJSON.freeze(p) for p in h["evidence"]), cutoff=cutoff
    )
    require(verified == history, "history_projection_conflict")
    return h
