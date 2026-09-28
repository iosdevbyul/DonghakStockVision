"""Synthetic bridge contracts, never evidence of market performance."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.decision_bridge import DecisionBundle, admit, prepare_decision
from donghak_stock_vision.backtest.ledger import checkpoint, initialize
from donghak_stock_vision.data.learning import digest, event_contract
from donghak_stock_vision.storage.decision import DecisionStore
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy, DecisionResult
from donghak_stock_vision.strategy.engine import decide
from tests.backtest.helpers import T
from tests.backtest.test_availability import fixture
from tests.backtest.test_ledger import command
from tests.backtest.test_ledger import policy as ledger_policy
from tests.backtest.time_helpers import tape_parts
from tests.strategy.helpers import inputs, policy
from tests.strategy.test_adapter_cli import create_analysis


def scenario(
    held: int = 0,
    context: str = "prior_decline",
    score: float = 0.7,
    mode: str = "historical_research",
) -> dict[str, Any]:
    store, _, _, ids = fixture(mode)
    b, p = inputs(held, context, score), policy()
    b["request"].update(ticker="005930", decision_as_of=T)
    a = b["analysis"]
    a.update(
        ticker="005930",
        model_version=ids["model_id"],
        snapshot_id=ids["snapshot_id"],
        anchor_at=T,
        snapshot_as_of=T,
        last_trading_date="2026-01-05",
        model_created_at=store.times["model"],
        **event_contract("verified_sessions"),
    )
    a.update(
        mode=mode,
        anchor_policy="received_at" if mode == "point_in_time" else "nominal_eod_1600",
        data_as_of=T if mode == "point_in_time" else None,
        last_trading_date="2026-01-02" if mode == "point_in_time" else "2026-01-05",
    )
    aid = digest(a)
    ids["analysis_id"] = aid
    store.records[("signal", aid)] = a
    b["request"]["analysis_id"] = aid
    for key in ("account", "orders", "market"):
        b[key].update(observed_at=T, received_at=T)
    b["account"].update(ticker="005930", account_ref="fixture-account")
    b["orders"]["account_ref"] = "fixture-account"
    b["market"].update(ticker="005930", price_observed_at=T)
    p.update(
        allowed_models=[ids["model_id"]],
        session_basis="verified_sessions",
        effective_from="2026-01-01T00:00:00Z",
        effective_until="2027-01-01T00:00:00Z",
    )
    p["costs"].update(effective_from=p["effective_from"], effective_until=p["effective_until"])
    parts = tape_parts(mode=mode)
    plan = parts["release_plan"].to_dict()
    plan["analysis_releases"] = [{**ids, "sequence": 1, "available_at": T}]
    release = FrozenJSON.freeze(plan)
    ep = parts["policy"].to_dict()
    ep["availability_policy"]["content_hash"] = release.identifier
    epolicy = ExecutionPolicy.from_dict(ep)
    m = parts["manifest"].to_dict()
    m.update(
        execution_policy_id=epolicy.identifier,
        availability_assumption_id=release.identifier if mode == "historical_research" else None,
    )
    m["initial_account"]["available_cash_krw"] = str(10000 - held * 100)
    m["initial_account"]["positions"] = [
        {
            "ticker": "005930",
            "quantity": held,
            "sellable_quantity": held,
            "reserved_quantity": 0,
            "cost_basis_krw": str(held * 100),
        }
    ]
    for key, ident in (("models", ids["model_id"]), ("analyses", aid)):
        m[key][0].update(artifact_id=ident, content_hash=ident)
    m["market_snapshots"].append(
        {
            **m["market_snapshots"][0],
            "artifact_id": ids["snapshot_id"],
            "content_hash": ids["snapshot_id"],
        }
    )
    run = RunManifest.from_dict(m)
    event = parts["events"][0].to_dict()
    event.update(
        run_id=run.identifier,
        availability_assumption_id=release.identifier if mode == "historical_research" else None,
    )
    tape = FrozenTape(
        run, epolicy, (MarketEvent.from_dict(event),), parts["sources"], release, parts["quality"]
    )
    analysis = FrozenAnalysis.capture(store, run, release, **ids)
    ledger = initialize(
        run, tape.identifier, ledger_policy(), command("initialize", 0, {}), cutoff=T
    )
    bundle = DecisionBundle.calculate(
        DecisionInput.from_dict(b), DecisionPolicy.from_dict(p), ledger
    )
    settings = {
        "version": "fixture-v1",
        "order_budget_krw": "1000",
        "max_order_quantity": 10,
        "max_position_quantity": {"005930": 10},
        "max_position_notional_krw": {"005930": "1000"},
        "ordered_tickers": ["005930"],
        "marks": {"005930": {"sequence": 1, "field": "open"}},
        "max_delay_seconds": 60,
        "expires_at": None,
    }
    return {
        "bundle": bundle,
        "decision_id": bundle.result.identifier,
        "ledger": ledger,
        "tape": tape,
        "analysis": analysis,
        "decision_clock": VirtualClock.at(T, 1),
        "admission_clock": VirtualClock.at(T, 1),
        "policy": settings,
        "history": FrozenJSON.freeze({}),
    }


def redecide(case: dict[str, Any], b: dict[str, Any]) -> None:
    case["bundle"] = DecisionBundle.calculate(
        DecisionInput.from_dict(b), case["bundle"].policy, case["ledger"]
    )
    case["decision_id"] = case["bundle"].result.identifier


@pytest.mark.parametrize(
    "held,context,side,qty", [(0, "prior_decline", "BUY", 10), (5, "prior_rise", "SELL", 5)]
)
def test_admitted_buy_sell_preserve_account_and_false_execution(
    held: int, context: str, side: str, qty: int
) -> None:
    c = scenario(held, context)
    before = checkpoint(c["ledger"])
    result, history = admit(**c)
    r = result.to_dict()
    assert r["status"] == "admitted", r
    assert r["candidate"]["side"] == side and r["candidate"]["quantity"] == qty
    assert r["candidate"]["accepted_at"] is None
    assert r["candidate"]["reserved_cash_krw"] == "0"
    assert r["candidate"]["reserved_quantity"] == 0
    assert r["candidate"]["broker_route"] is None
    assert r["virtual_order_eligible"] is True and r["executable"] is False
    assert r["candidate"]["usage_restriction"] == r["usage_restriction"] == "synthetic_test_only"
    assert checkpoint(c["ledger"]) == before
    again, same_history = admit(**{**c, "history": history})
    assert again == result and same_history == history
    assert FrozenJSON(result.payload_json) == result


@pytest.mark.parametrize("held,context", [(5, "prior_decline"), (0, "flat")])
def test_hold_wait_no_candidate_preserve_diagnostics(held: int, context: str) -> None:
    c = scenario(held, context)
    r = admit(**c)[0].to_dict()
    assert r["status"] == "rejected" and r["reason"] == "no_order_action"
    assert r["candidate"] is None
    assert r["decision_diagnostics"] == c["bundle"].result.to_dict()["diagnostics"]


def test_operational_never_admitted() -> None:
    c = scenario()
    b = c["bundle"].inputs.to_dict()
    b["request"]["scope"] = "operational"
    redecide(c, b)
    r = admit(**c)[0].to_dict()
    assert r["reason"] == "operational_forbidden" and r["candidate"] is None


@pytest.mark.parametrize(
    "key",
    [
        "order_budget_krw",
        "max_order_quantity",
        "ordered_tickers",
        "marks",
        "max_delay_seconds",
        "expires_at",
    ],
)
def test_missing_policy_is_blocked(key: str) -> None:
    c = scenario()
    del c["policy"][key]
    r = admit(**c)[0].to_dict()
    assert r["status"] == "blocked" and r["candidate"] is None


def test_absent_policy_has_distinct_code() -> None:
    c = scenario()
    c["policy"] = None
    assert admit(**c)[0].to_dict()["reason"] == "policy_missing"


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("order_budget_krw", "999", "order_budget_limit"),
        ("max_order_quantity", 9, "order_quantity_limit"),
        ("max_position_quantity", {"005930": 9}, "position_limit"),
        ("max_position_notional_krw", {"005930": "999"}, "position_limit"),
        ("ordered_tickers", ["000001"], "order_sequence_missing"),
    ],
)
def test_explicit_limits_do_not_resize_phase3(key: str, value: Any, reason: str) -> None:
    c = scenario()
    original = c["bundle"].result
    c["policy"][key] = value
    r = admit(**c)[0].to_dict()
    assert r["reason"] == reason and r["candidate"] is None
    assert c["bundle"].result == original


def test_account_revision_conflict() -> None:
    c = scenario()
    c["bundle"] = replace(c["bundle"], account_revision=1)
    assert admit(**c)[0].to_dict()["reason"] == "account_revision_conflict"


def test_same_decision_id_changed_content_is_conflict() -> None:
    c = scenario()
    result, history = admit(**c)
    changed = c["bundle"].result.to_dict()
    changed["proposed_quantity"] = 1
    bad = replace(c["bundle"], result=DecisionResult.from_dict(changed))
    r = admit(**{**c, "bundle": bad, "history": history})[0].to_dict()
    assert r["status"] == "rejected" and r["reason"] == "decision_id_conflict"
    assert result.to_dict()["candidate"]["quantity"] == 10


def test_unpublished_price_and_analysis_do_not_admit() -> None:
    c = scenario()
    c["policy"]["marks"]["005930"]["field"] = "close"
    assert admit(**c)[0].to_dict()["reason"] == "data_not_public"
    c = scenario()
    c["decision_clock"] = VirtualClock.at(T, 0)
    assert admit(**c)[0].to_dict()["reason"] == "analysis_not_public"


def test_later_clock_does_not_publish_future_price() -> None:
    c = scenario()
    c["admission_clock"] = VirtualClock.at("2026-01-05T01:00:00Z", 2)
    c["policy"]["max_delay_seconds"] = 3600
    c["policy"]["marks"]["005930"]["field"] = "close"
    assert admit(**c)[0].to_dict()["reason"] == "data_not_public"


def test_phase3_cash_and_quantity_failure_reasons_are_preserved() -> None:
    c = scenario()
    b = c["bundle"].inputs.to_dict()
    b["account"]["available_cash_krw"] = "100"
    b["account"]["equity_krw"] = "100"
    redecide(c, b)
    assert admit(**c)[0].to_dict()["reason"] == "insufficient_cash"
    c = scenario(5, "prior_rise")
    b = c["bundle"].inputs.to_dict()
    b["account"]["sellable_quantity"] = 2
    redecide(c, b)
    assert admit(**c)[0].to_dict()["reason"] == "insufficient_quantity"


def test_changed_policy_cannot_admit_same_decision_twice() -> None:
    c = scenario()
    _, history = admit(**c)
    c["policy"]["version"] = "changed"
    r = admit(**{**c, "history": history})[0].to_dict()
    assert r["reason"] == "decision_already_admitted"


def test_existing_phase3_prepare_and_store_public_api(tmp_path: Path) -> None:
    analysis_path, row = create_analysis(tmp_path)
    c = scenario()
    b, p = inputs(), policy()
    b["request"]["analysis_id"] = row["analysis_id"]
    p["allowed_models"] = [row["model_version"]]
    store = DecisionStore(tmp_path / "decisions.db", protected=[analysis_path])
    try:
        prepared = prepare_decision(
            b["request"],
            analysis_path,
            b["account"],
            b["orders"],
            b["market"],
            DecisionPolicy.from_dict(p),
            c["ledger"],
            store,
        )
        assert prepared.result == decide(prepared.inputs, prepared.policy)
        assert store.replay(prepared.result.identifier)["decision_id"] == prepared.result.identifier
        assert prepared.result.to_dict()["executable"] is False
    finally:
        store.close()


def test_pit_gate_cannot_be_bypassed_by_phase3_buy() -> None:
    c = scenario(mode="point_in_time")
    assert c["bundle"].result.to_dict()["action"] == "BUY"
    r = admit(**c)[0].to_dict()
    assert r["reason"] == "pit_evidence_missing" and r["candidate"] is None
    assert r["information_mode"] == "point_in_time"
    assert r["usage_restriction"] == "synthetic_test_only"


def test_ledger_pending_order_rejected_even_when_decision_omits_it() -> None:
    from donghak_stock_vision.backtest.ledger import apply
    from tests.backtest.test_ledger import order, reserve_event

    c = scenario()
    o = order(c["ledger"])
    state = apply(c["ledger"], reserve_event(o, 1), cutoff=T)
    c["ledger"] = state
    c["bundle"] = replace(
        c["bundle"],
        account_revision=state.to_dict()["revision"],
        ledger_state_hash=state.identifier,
    )
    before = checkpoint(state)
    r = admit(**c)[0].to_dict()
    assert r["reason"] == "pending_order"
    assert checkpoint(state) == before


def test_other_ticker_reservation_is_reflected_without_changing_it() -> None:
    from donghak_stock_vision.backtest.ledger import apply
    from tests.backtest.test_ledger import order, reserve_event

    c = scenario()
    o = order(c["ledger"], ticker="000001")
    state = apply(c["ledger"], reserve_event(o, 1, "500"), cutoff=T)
    c["ledger"] = state
    b = c["bundle"].inputs.to_dict()
    b["account"].update(available_cash_krw="9500", reserved_cash_krw="500")
    b["orders"]["items"] = [
        {
            "ticker": "000001",
            "state": "open",
            "remaining_quantity": 4,
            "reserved_cash_krw": "500",
            "received_at": T,
        }
    ]
    redecide(c, b)
    before = checkpoint(state)
    r = admit(**c)[0].to_dict()
    assert r["status"] == "admitted", r
    assert checkpoint(state) == before


def test_account_cash_tampering_does_not_pass_binding() -> None:
    c = scenario()
    b = c["bundle"].inputs.to_dict()
    b["account"].update(available_cash_krw="20000", equity_krw="20000")
    redecide(c, b)
    assert admit(**c)[0].to_dict()["reason"] == "account_snapshot_mismatch"


def test_duplicate_buy_account_binding_is_rejected() -> None:
    c = scenario(held=1)
    b = c["bundle"].inputs.to_dict()
    b["account"].update(
        quantity=0,
        sellable_quantity=0,
        gross_exposure_krw="0",
        position_exposure_krw="0",
        positions_count=0,
        equity_krw="9900",
        average_price_krw=None,
    )
    redecide(c, b)
    assert c["bundle"].result.to_dict()["action"] == "BUY"
    assert admit(**c)[0].to_dict()["reason"] == "account_snapshot_mismatch"


def test_invalid_execution_bit_cannot_be_forged() -> None:
    c = scenario()
    value = c["bundle"].result.to_dict()
    value["executable"] = True
    result = DecisionResult.from_dict(value)
    c["bundle"] = replace(c["bundle"], result=result)
    c["decision_id"] = result.identifier
    assert admit(**c)[0].to_dict()["reason"] == "decision_result_mismatch"
