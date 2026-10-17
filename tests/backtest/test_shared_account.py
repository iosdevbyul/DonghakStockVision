"""Deterministic shared synthetic account; no real profits or market approvals."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.account import (
    account_view,
    execute_pending,
    release_pending,
    transition,
)
from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.decision_bridge import DecisionBundle, admit
from donghak_stock_vision.backtest.execution import accept_candidate, execute
from donghak_stock_vision.backtest.ledger import apply, checkpoint, initialize, restore
from donghak_stock_vision.backtest.position_history import build_position_history
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from tests.backtest.test_decision_bridge import scenario
from tests.backtest.test_execution import FUTURE, execution_policy, setup
from tests.backtest.test_ledger import command, fill_event, initial, order, reserve_event
from tests.backtest.test_performance import valuation_policy

T = "2026-01-05T00:00:00Z"
TICKERS = ("000075", "00088K", "005930")


def case(
    *, cash: str = "10000", sells: tuple[str, ...] = (), position_weight: str = "0.5"
) -> dict[str, Any]:
    base = setup()
    old = base["tape"]
    prototype = scenario()["analysis"].bundle.to_dict()
    bundles = []
    for ticker in TICKERS:
        b = deepcopy(prototype)
        b["signal"]["ticker"] = ticker
        if ticker in sells:
            b["signal"].update(
                context="prior_rise",
                up_score=None,
                down_score=0.8,
                up_status="context_mismatch",
                down_status="scored",
            )
        b["analysis_id"] = digest(b["signal"])
        bundles.append(b)
    sources: list[FrozenJSON] = []
    events: list[dict[str, Any]] = []
    releases: list[dict[str, Any]] = []
    for day_index in range(2):
        for ticker in TICKERS:
            e = old.events[day_index].to_dict()
            source = next(
                s.to_dict() for s in old.sources if s.identifier == e["source"]["content_hash"]
            )
            source["ticker"] = ticker
            frozen = FrozenJSON.freeze(source)
            sources.append(frozen)
            seq = len(events) + 1
            e.update(
                ticker=ticker,
                sequence=seq,
                source={
                    **e["source"],
                    "artifact_id": frozen.identifier,
                    "content_hash": frozen.identifier,
                },
            )
            events.append(e)
            r = deepcopy(old.release_plan.to_dict()["releases"][day_index])
            r["sequence"] = seq
            releases.append(r)
    q = old.quality.to_dict()
    q["coverage"] = {t: deepcopy(q["coverage"]["005930"]) for t in TICKERS}
    quality = FrozenJSON.freeze(q)
    plan = old.release_plan.to_dict()
    plan["releases"] = releases
    plan["analysis_releases"] = [
        {
            "model_id": b["model_id"],
            "snapshot_id": b["snapshot_id"],
            "analysis_id": b["analysis_id"],
            "available_at": T,
            "sequence": i + 1,
        }
        for i, b in enumerate(bundles)
    ]
    release = FrozenJSON.freeze(plan)
    p = old.policy.to_dict()
    p["availability_policy"]["content_hash"] = release.identifier
    policy = ExecutionPolicy.from_dict(p)
    m = old.manifest.to_dict()
    m.update(execution_policy_id=policy.identifier, availability_assumption_id=release.identifier)
    m["quality_manifest"]["content_hash"] = quality.identifier
    ref = m["market_snapshots"][0]
    m["market_snapshots"] = [
        {**ref, "artifact_id": s.identifier, "content_hash": s.identifier} for s in sources
    ] + [{**ref, "artifact_id": prototype["snapshot_id"], "content_hash": prototype["snapshot_id"]}]
    m["analyses"] = [
        {**m["analyses"][0], "artifact_id": b["analysis_id"], "content_hash": b["analysis_id"]}
        for b in bundles
    ]
    m["initial_account"].update(
        available_cash_krw=cash,
        positions=[
            {
                "ticker": t,
                "quantity": 5,
                "sellable_quantity": 5,
                "reserved_quantity": 0,
                "cost_basis_krw": "500",
            }
            for t in sells
        ],
    )
    manifest = RunManifest.from_dict(m)
    for e in events:
        e.update(
            run_id=manifest.identifier,
            availability_assumption_id=release.identifier,
            quality_evidence_ids=[quality.identifier],
        )
    tape = FrozenTape(
        manifest,
        policy,
        tuple(MarketEvent.from_dict(e) for e in events),
        tuple(sources),
        release,
        quality,
    )
    ledger = initialize(
        manifest, tape.identifier, base["ledger"].policy, base["ledger"].events[0], cutoff=T
    )
    analyses = tuple(FrozenAnalysis(manifest, release, FrozenJSON.freeze(b)) for b in bundles)
    decision_policy = scenario()["bundle"].policy.to_dict()
    decision_policy["limits"]["max_position_weight"] = position_weight
    runner = BacktestRunner(
        tape,
        analyses,
        ledger,
        DecisionPolicy.from_dict(decision_policy),
        execution_policy(),
        FrozenJSON.freeze({}),
    )
    return {
        "runner": runner,
        "ledger": ledger,
        "history": FrozenJSON.freeze({}),
        "packets": {},
        "acceptances": {},
    }


def reserve(
    c: dict[str, Any], ticker: str, *, expiry: str | None = None, notional: str = "950"
) -> FrozenJSON:
    runner, state = c["runner"], c["ledger"]
    index = TICKERS.index(ticker)
    analysis = runner.analyses[index]
    clock = VirtualClock.at(T, 3)
    evidence = tuple(p for p in c["packets"].values() if p.to_dict()["execution"])
    proof = build_position_history(state, runner.tape, evidence, cutoff=T)
    market = deepcopy(scenario()["bundle"].inputs.to_dict()["market"])
    market.update(ticker=ticker, observed_at=T, received_at=T, price_observed_at=T)
    policy = deepcopy(scenario()["policy"])
    policy.update(
        ordered_tickers=list(TICKERS),
        max_position_quantity={t: 10 for t in TICKERS},
        max_position_notional_krw={t: "1000" for t in TICKERS},
        marks={t: {"sequence": i + 1, "field": "open"} for i, t in enumerate(TICKERS)},
        expires_at=expiry,
    )
    step = {
        "ticker": ticker,
        "analysis_id": analysis.analysis_id,
        "admission_policy": policy,
        "market": market,
    }
    inputs = runner.project(state, step, analysis, clock, proof)
    bundle = DecisionBundle.calculate(inputs, runner.decision_policy, state)
    admission, history = admit(
        bundle,
        bundle.result.identifier,
        state,
        runner.tape,
        analysis,
        clock,
        clock,
        policy,
        c["history"],
        position_history=proof,
    )
    if not admission.to_dict()["virtual_order_eligible"]:
        return admission
    buying = bundle.result.to_dict()["action"] == "BUY"
    acceptance, updated = accept_candidate(
        admission,
        state,
        runner.tape,
        clock,
        clock,
        clock,
        runner.execution_policy,
        FrozenJSON.freeze(
            {
                "max_notional_krw": notional if buying else "0",
                "max_cost_krw": "50" if buying else "0",
            }
        ),
        ledger_sequence=state.to_dict()["last_sequence"] + 1,
    )
    if acceptance.to_dict()["status"] == "accepted":
        c.update(ledger=updated, history=history)
        c["acceptances"][ticker] = acceptance
        c["packets"][ticker] = FrozenJSON.freeze(
            {
                "decision_input": inputs.to_dict(),
                "decision_policy": runner.decision_policy.to_dict(),
                "decision_result": bundle.result.to_dict(),
                "decision_ledger_hash": state.identifier,
                "analysis_bundle": analysis.bundle.to_dict(),
                "acceptance": acceptance.to_dict(),
                "execution": None,
            }
        )
    return acceptance


def fill(c: dict[str, Any], tickers: tuple[str, ...]) -> tuple[FrozenJSON, ...]:
    state = c["ledger"]
    requests = tuple(
        (
            c["acceptances"][t],
            c["runner"].tape.events[3 + TICKERS.index(t)],
            VirtualClock.at(FUTURE, 4 + TICKERS.index(t)),
        )
        for t in tickers
    )
    outputs, updated = execute_pending(
        state,
        c["runner"].tape,
        requests,
        expected_revision=state.to_dict()["revision"],
        expected_ledger_hash=state.identifier,
    )
    c["ledger"] = updated
    for output in outputs:
        if output.to_dict()["status"] == "filled":
            ticker = output.to_dict()["fill"]["ticker"]
            packet = c["packets"][ticker].to_dict()
            packet["execution"] = output.to_dict()
            c["packets"][ticker] = FrozenJSON.freeze(packet)
    return outputs


def view(c: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return account_view(
        c["ledger"],
        c["runner"].tape,
        tuple(p for p in c["packets"].values() if p.to_dict()["execution"]),
        VirtualClock.at(FUTURE, 6),
        valuation_policy(**changes),
    ).to_dict()


def test_two_buys_one_filled_other_pending_and_complete_positions() -> None:
    c = case()
    for t in TICKERS[:2]:
        assert reserve(c, t).to_dict()["status"] == "accepted"
    s = c["ledger"].to_dict()
    assert (s["available_cash_krw"], s["reserved_cash_krw"]) == ("8000", "2000")
    first = fill(c, (TICKERS[0],))[0].to_dict()
    assert first["status"] == "filled", first
    s = c["ledger"].to_dict()
    assert s["reserved_cash_krw"] == "1000"
    assert s["positions"][TICKERS[0]]["quantity"] == 10
    assert s["total_cash_krw"] == "9089.18"
    assert fill(c, (TICKERS[1],))[0].to_dict()["status"] == "filled"
    v = view(c)
    assert v["valuation"]["complete"]
    assert v["valuation"]["coverage"]["open_positions"] == 2
    assert v["account"]["total_cash_krw"] == "8178.36"
    assert v["valuation"]["total_equity"] == "9978.36"
    assert all(v["account"]["positions"][t]["quantity"] == 10 for t in TICKERS[:2])
    assert restore(checkpoint(c["ledger"])) == c["ledger"]


@pytest.mark.parametrize("cash,accepted", [("1900", 1), ("2000", 2), ("3000", 3)])
def test_budget_admission_rejections_do_not_mutate(cash: str, accepted: int) -> None:
    # Explicit synthetic risk limit isolates available-cash admission.
    c = case(cash=cash, position_weight="1")
    for i, t in enumerate(TICKERS):
        before = c["ledger"].identifier
        output = reserve(c, t).to_dict()
        if i < accepted:
            assert output["status"] == "accepted"
        else:
            assert output["reason"] == "insufficient_cash", output
            assert c["ledger"].identifier == before
    assert Decimal(c["ledger"].to_dict()["reserved_cash_krw"]) == accepted * 1000


def test_same_time_permuted_fill_requests_have_exact_canonical_results() -> None:
    a, b = case(), case()
    for c in (a, b):
        for t in TICKERS:
            reserve(c, t)
    outputs = fill(a, TICKERS)
    reverse = fill(b, tuple(reversed(TICKERS)))
    assert all(x.to_dict()["status"] == "filled" for x in outputs)
    assert outputs == reverse and a["ledger"] == b["ledger"]
    assert view(a) == view(b)


@pytest.mark.parametrize("status", ["cancelled", "rejected", "expired"])
def test_release_only_own_cash(status: str) -> None:
    c = case()
    first = reserve(c, TICKERS[0], expiry=FUTURE if status == "expired" else None)
    second = reserve(c, TICKERS[1])
    s = c["ledger"]
    output, updated = release_pending(
        first,
        s,
        c["runner"].tape,
        VirtualClock.at(FUTURE, 6),
        expected_revision=s.to_dict()["revision"],
        expected_ledger_hash=s.identifier,
        status=status,
    )
    assert output.to_dict()["status"] == "released", output.to_dict()
    assert updated.to_dict()["reserved_cash_krw"] == "1000"
    assert updated.to_dict()["orders"][second.to_dict()["order_id"]]["current"]["status"] == "open"
    assert updated.to_dict()["total_cash_krw"] == "10000"


@pytest.mark.parametrize("sells", [(TICKERS[0],), (TICKERS[0], TICKERS[1])])
def test_mixed_buy_sell_and_independent_holdings(sells: tuple[str, ...]) -> None:
    c = case(sells=sells)
    for t in TICKERS:
        assert reserve(c, t).to_dict()["status"] == "accepted"
    before = c["ledger"].to_dict()["positions"].get(TICKERS[1])
    x = fill(c, (TICKERS[0],))[0].to_dict()
    assert x["status"] == "filled", x
    assert c["ledger"].to_dict()["positions"].get(TICKERS[1]) == before
    v = view(c)
    assert v["realized_pnl_by_ticker"][TICKERS[0]] == "-57.62"
    assert Decimal(v["account"]["realized_pnl_krw"]) == sum(
        Decimal(x) for x in v["realized_pnl_by_ticker"].values()
    )
    assert all(x.to_dict()["status"] == "filled" for x in fill(c, TICKERS[1:]))
    assert view(c)["valuation"]["complete"]


def test_cancel_sell_preserves_other_sell_and_reserved_quantity() -> None:
    c = case(sells=TICKERS[:2])
    first, second = (reserve(c, t) for t in TICKERS[:2])
    s = c["ledger"]
    out, s = release_pending(
        first,
        s,
        c["runner"].tape,
        VirtualClock.at(T, 3),
        expected_revision=s.to_dict()["revision"],
        expected_ledger_hash=s.identifier,
        status="cancelled",
    )
    assert out.to_dict()["status"] == "released"
    assert s.to_dict()["positions"][TICKERS[0]]["sellable_quantity"] == 5
    assert s.to_dict()["positions"][TICKERS[1]]["reserved_quantity"] == 5
    assert s.to_dict()["orders"][second.to_dict()["order_id"]]["current"]["status"] == "open"


@pytest.mark.parametrize(
    "damage", ["revision", "hash", "amend", "fork", "partial", "source", "policy", "duplicate"]
)
def test_stale_changed_or_missing_evidence_preserves_state(damage: str) -> None:
    c = case()
    a = reserve(c, TICKERS[0])
    reserve(c, TICKERS[1])
    s = c["ledger"]
    e = c["runner"].tape.events[3]
    kwargs = dict(
        expected_revision=s.to_dict()["revision"],
        ledger_sequence=s.to_dict()["last_sequence"] + 1,
        quantity=10,
        account_transition=transition(s.to_dict()["revision"], s.identifier),
    )
    if damage == "revision":
        kwargs["expected_revision"] -= 1
    elif damage == "hash":
        kwargs["account_transition"] = transition(s.to_dict()["revision"], "0" * 64)
    elif damage == "amend":
        cmd = command(
            "amend_cash",
            s.to_dict()["last_sequence"] + 1,
            {"max_notional_krw": "900", "max_cost_krw": "50"},
            a.to_dict()["order_id"],
        )
        s = apply(s, cmd, cutoff=T)
        kwargs.update(
            expected_revision=s.to_dict()["revision"],
            ledger_sequence=s.to_dict()["last_sequence"] + 1,
            account_transition=transition(s.to_dict()["revision"], s.identifier),
        )
    elif damage == "fork":
        other = case()
        reserve(other, TICKERS[1])
        reserve(other, TICKERS[0])
        s = other["ledger"]
        kwargs["account_transition"] = transition(s.to_dict()["revision"], s.identifier)
    elif damage == "partial":
        kwargs["quantity"] = 5
    elif damage == "source":
        v = e.to_dict()
        v["source"]["content_hash"] = "0" * 64
        e = MarketEvent.from_dict(v)
    elif damage == "policy":
        v = a.to_dict()
        v["execution_policy"]["slippage_rate"] = "0"
        v["execution_policy_hash"] = digest(v["execution_policy"])
        a = FrozenJSON.freeze(v)
    elif damage == "duplicate":
        out = fill(c, (TICKERS[0],))[0]
        assert out.to_dict()["status"] == "filled"
        s = c["ledger"]
        kwargs.update(
            expected_revision=s.to_dict()["revision"],
            ledger_sequence=s.to_dict()["last_sequence"] + 1,
            account_transition=transition(s.to_dict()["revision"], s.identifier),
        )
    output, unchanged = execute(a, s, c["runner"].tape, e, VirtualClock.at(FUTURE, 4), **kwargs)
    assert output.to_dict()["status"] == "rejected", output.to_dict()
    assert unchanged is s


def test_missing_trade_evidence_and_conflicting_report_provenance_fail_closed() -> None:
    c = case()
    reserve(c, TICKERS[0])
    fill(c, (TICKERS[0],))
    with pytest.raises(ValueError, match="history_fill_evidence_missing"):
        account_view(
            c["ledger"], c["runner"].tape, (), VirtualClock.at(FUTURE, 6), valuation_policy()
        )
    p = c["packets"][TICKERS[0]].to_dict()
    p["execution"]["account_transition"]["expected_ledger_hash"] = "0" * 64
    c["packets"][TICKERS[0]] = FrozenJSON.freeze(p)
    with pytest.raises(ValueError, match="history_execution_conflict"):
        view(c)


@pytest.mark.parametrize("max_age", [0, 1])
def test_missing_or_stale_one_mark_never_values_at_zero(max_age: int) -> None:
    c = case(sells=TICKERS[:2])
    v = account_view(
        c["ledger"],
        c["runner"].tape,
        (),
        VirtualClock.at(FUTURE, 3),
        valuation_policy(max_age_seconds=max_age),
    ).to_dict()["valuation"]
    assert not v["complete"] and v["total_equity"] is None and v["unrealized_pnl"] is None
    assert all(p["reason"] == "stale_mark" for p in v["positions"])
    v = account_view(
        c["ledger"], c["runner"].tape, (), VirtualClock.at(T, 1), valuation_policy()
    ).to_dict()["valuation"]
    assert v["coverage"] == {"valued_positions": 1, "open_positions": 2, "unvalued_positions": 1}
    assert v["total_equity"] is None
    assert v["positions"][1]["reason"] == "missing_mark"


def test_low_level_competing_sells_and_partial_remaining_reservations() -> None:
    s = initial(held=10, basis="1000")
    a = order(s, "SELL", 6, key="sell-a")
    s = apply(s, reserve_event(a, 1), cutoff=T)
    b = order(s, "SELL", 5, key="sell-b")
    with pytest.raises(ValueError, match="insufficient_shares"):
        apply(s, reserve_event(b, 2), cutoff=T)
    b = order(s, "SELL", 4, key="sell-b")
    s = apply(s, reserve_event(b, 2), cutoff=T)
    s = apply(s, fill_event(s, a, 3, n=2), cutoff=T)
    assert s.to_dict()["positions"]["005930"]["reserved_quantity"] == 8
    assert s.to_dict()["orders"][a.identifier]["current"]["reserved_quantity"] == 4
    s = apply(s, command("release", 4, {"status": "cancelled"}, a.identifier), cutoff=T)
    assert s.to_dict()["positions"]["005930"]["reserved_quantity"] == 4
    assert s.to_dict()["positions"]["005930"]["sellable_quantity"] == 4


def test_duplicate_reservation_stale_order_and_duplicate_batch() -> None:
    s = initial()
    a = order(s)
    s = apply(s, reserve_event(a, 1), cutoff=T)
    with pytest.raises(ValueError, match="duplicate_order"):
        apply(s, reserve_event(a, 2), cutoff=T)
    b = order(initial(), ticker="00088K", key="other")
    with pytest.raises(ValueError, match="stale_account_revision"):
        apply(s, reserve_event(b, 2), cutoff=T)
    c = case()
    acceptance = reserve(c, TICKERS[0])
    s = c["ledger"]
    r = (acceptance, c["runner"].tape.events[3], VirtualClock.at(FUTURE, 4))
    with pytest.raises(ValueError, match="duplicate_batch_order"):
        execute_pending(
            s,
            c["runner"].tape,
            (r, r),
            expected_revision=s.to_dict()["revision"],
            expected_ledger_hash=s.identifier,
        )


def test_failed_execution_keeps_reservation_until_explicit_rejection() -> None:
    c = case()
    first = reserve(c, TICKERS[0], notional="100")
    second = reserve(c, TICKERS[1])
    before = c["ledger"]
    failed = fill(c, (TICKERS[0],))[0].to_dict()
    assert (failed["status"], failed["reason"]) == ("rejected", "reservation_shortfall")
    assert c["ledger"] is before
    out, updated = release_pending(
        first,
        before,
        c["runner"].tape,
        VirtualClock.at(FUTURE, 6),
        expected_revision=before.to_dict()["revision"],
        expected_ledger_hash=before.identifier,
        status="rejected",
    )
    assert out.to_dict()["status"] == "released"
    assert updated.to_dict()["reserved_cash_krw"] == "1000"
    assert (
        updated.to_dict()["orders"][second.to_dict()["order_id"]]
        == before.to_dict()["orders"][second.to_dict()["order_id"]]
    )


@pytest.mark.parametrize("damage", ["revision", "hash", "premature_expiry", "clock", "tampered"])
def test_invalid_release_preserves_all_reservations(damage: str) -> None:
    c = case()
    first = reserve(c, TICKERS[0], expiry=FUTURE)
    reserve(c, TICKERS[1])
    state = c["ledger"]
    revision, identity = state.to_dict()["revision"], state.identifier
    clock, status = VirtualClock.at(T, 3), "cancelled"
    if damage == "revision":
        revision -= 1
    elif damage == "hash":
        identity = "0" * 64
    elif damage == "premature_expiry":
        status = "expired"
    elif damage == "clock":
        clock = VirtualClock.at("2026-01-04T23:59:59Z", 2)
    else:
        raw = first.to_dict()
        raw["candidate_id"] = "0" * 64
        first = FrozenJSON.freeze(raw)
    out, unchanged = release_pending(
        first,
        state,
        c["runner"].tape,
        clock,
        expected_revision=revision,
        expected_ledger_hash=identity,
        status=status,
    )
    assert out.to_dict()["status"] == "rejected"
    assert unchanged is state


@pytest.mark.parametrize("family", ["linear", "hgb"])
def test_genuine_model_and_shared_evidence_integration(tmp_path: Path, family: str) -> None:
    from donghak_stock_vision.backtest.service import freeze_request
    from tests.backtest.test_hgb_trading import hgb_runner
    from tests.backtest.test_public_inference import scored_runner

    runner = (scored_runner if family == "linear" else hgb_runner)(tmp_path)
    frozen = freeze_request(runner, valuation_policy())
    assert frozen.to_dict()["version"] == 2
    assert (
        frozen.shared_evidence.analyses(runner.tape.manifest, runner.tape.release_plan)
        == runner.analyses
    )
    run = runner.run().to_dict()
    packets = run["position_history"]["evidence"]
    packet = min(packets, key=lambda p: p["execution"]["fill"]["sequence"])
    a, x = packet["acceptance"], packet["execution"]
    reserved = restore(FrozenJSON.freeze(a["reservation_checkpoint"]))
    fill_record = x["fill"]
    event = next(e for e in runner.tape.events if e.identifier == fill_record["source_event_id"])
    clock = VirtualClock.at(fill_record["fill_known_at"], x["market_sequence"])
    common = dict(
        expected_revision=reserved.to_dict()["revision"],
        ledger_sequence=fill_record["sequence"],
        quantity=fill_record["quantity"],
    )
    legacy, original = execute(FrozenJSON.freeze(a), reserved, runner.tape, event, clock, **common)
    shared, updated = execute(
        FrozenJSON.freeze(a),
        reserved,
        runner.tape,
        event,
        clock,
        account_transition=transition(reserved.to_dict()["revision"], reserved.identifier),
        **common,
    )
    assert legacy.to_dict() == x
    shared_value = shared.to_dict()
    shared_value.pop("account_transition")
    assert shared_value == x
    assert updated == original
    packet["execution"] = shared.to_dict()
    proof = build_position_history(
        updated, runner.tape, (FrozenJSON.freeze(packet),), cutoff=clock.cutoff
    )
    assert proof.to_dict()["complete"]
    v = account_view(
        updated, runner.tape, (FrozenJSON.freeze(packet),), clock, valuation_policy()
    ).to_dict()
    assert v["account"] == original.to_dict()
