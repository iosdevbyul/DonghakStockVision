"""Synthetic full-fill arithmetic only; no evidence of real liquidity or performance."""

from decimal import Decimal, localcontext
from typing import Any

import pytest

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.decision_bridge import DecisionBundle, admit
from donghak_stock_vision.backtest.execution import accept_candidate, execute
from donghak_stock_vision.backtest.ledger import apply, checkpoint, initialize
from tests.backtest.test_decision_bridge import scenario

FUTURE = "2026-01-06T00:00:00Z"


def execution_policy(**changes: Any) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": "explicit-fixture-v1",
            "scope": "research",
            "fill_mode": "full",
            "liquidity": "synthetic_explicit_full_fill",
            "execution_price_field": "open",
            "slippage_rate": "0.01",
            "buy_fee_rate": "0.002",
            "sell_fee_rate": "0.003",
            "sell_tax_rate": "0.004",
            "buy_tax": "not_applicable",
            "price_quantum": "0.01",
            "price_rounding": "adverse",
            "cost_quantum": "0.01",
            "cost_rounding": "half_up",
            **changes,
        }
    )


def setup(
    side: str = "BUY", *, future_event_at: str = FUTURE, **policy_changes: Any
) -> dict[str, Any]:
    c = scenario() if side == "BUY" else scenario(5, "prior_rise")
    old = c["tape"]
    source = old.sources[0].to_dict()
    source.update(trading_date="2026-01-06", source_received_at=FUTURE)
    source["fields"]["open"] = "90" if side == "BUY" else "110"
    future_source = FrozenJSON.freeze(source)
    q = old.quality.to_dict()
    q["sessions"].append("2026-01-06")
    q["coverage"]["005930"]["end"] = "2026-01-06"
    quality = FrozenJSON.freeze(q)
    plan = old.release_plan.to_dict()
    plan["releases"].append(
        {
            "sequence": 2,
            "previous_revision": None,
            "field_times": {
                k: FUTURE if k == "open" else "2026-01-06T00:30:00Z" for k in source["fields"]
            },
        }
    )
    release = FrozenJSON.freeze(plan)
    p = old.policy.to_dict()
    p["availability_policy"]["content_hash"] = release.identifier
    tape_policy = ExecutionPolicy.from_dict(p)
    m = old.manifest.to_dict()
    m.update(
        end_at="2026-01-06T01:00:00Z",
        execution_policy_id=tape_policy.identifier,
        availability_assumption_id=release.identifier,
    )
    m["quality_manifest"]["content_hash"] = quality.identifier
    m["market_snapshots"].append(
        {
            **m["market_snapshots"][0],
            "artifact_id": future_source.identifier,
            "content_hash": future_source.identifier,
        }
    )
    manifest = RunManifest.from_dict(m)
    first = old.events[0].to_dict()
    first.update(
        run_id=manifest.identifier,
        quality_evidence_ids=[quality.identifier],
        availability_assumption_id=release.identifier,
    )
    second = dict(first)
    second.update(
        sequence=2,
        trading_date="2026-01-06",
        event_at=future_event_at,
        available_at=FUTURE,
        source_received_at=FUTURE,
        public_fields=source["fields"],
        source={
            **first["source"],
            "artifact_id": future_source.identifier,
            "content_hash": future_source.identifier,
        },
    )
    tape = FrozenTape(
        manifest,
        tape_policy,
        (MarketEvent.from_dict(first), MarketEvent.from_dict(second)),
        (*old.sources, future_source),
        release,
        quality,
    )
    original = c["ledger"]
    ledger = initialize(
        manifest,
        tape.identifier,
        original.policy,
        original.events[0],
        cutoff=c["decision_clock"].cutoff,
    )
    c.update(
        ledger=ledger, tape=tape, analysis=FrozenAnalysis(manifest, release, c["analysis"].bundle)
    )
    c["bundle"] = DecisionBundle.calculate(c["bundle"].inputs, c["bundle"].policy, ledger)
    c["decision_id"] = c["bundle"].result.identifier
    admission, _ = admit(**c)
    assert admission.to_dict()["status"] == "admitted", admission.to_dict()
    return {
        "admission": admission,
        "ledger": ledger,
        "tape": tape,
        "decision_clock": c["decision_clock"],
        "candidate_clock": c["admission_clock"],
        "clock": c["admission_clock"],
        "policy": execution_policy(**policy_changes),
        "reservation": FrozenJSON.freeze(
            {
                "max_notional_krw": "950" if side == "BUY" else "0",
                "max_cost_krw": "50" if side == "BUY" else "0",
            }
        ),
        "ledger_sequence": 1,
    }


def ready(side: str = "BUY", **changes: Any) -> dict[str, Any]:
    c = setup(side, **changes)
    acceptance, reserved = accept_candidate(**c)
    assert acceptance.to_dict()["status"] == "accepted", acceptance.to_dict()
    return {
        "acceptance": acceptance,
        "ledger": reserved,
        "tape": c["tape"],
        "event": c["tape"].events[1],
        "clock": VirtualClock.at(FUTURE, 2),
        "expected_revision": reserved.to_dict()["revision"],
        "ledger_sequence": 2,
        "quantity": 10 if side == "BUY" else 5,
    }


@pytest.mark.parametrize(
    "side,price,fee,tax,cash,held",
    [("BUY", "90.9", "1.82", "0", "9089.18", 10), ("SELL", "108.9", "1.63", "2.18", "10040.69", 0)],
)
def test_full_fill_arithmetic_and_ledger_idempotency(
    side: str,
    price: str,
    fee: str,
    tax: str,
    cash: str,
    held: int,
) -> None:
    c = ready(side)
    before = checkpoint(c["ledger"]).identifier
    output, state = execute(**c)
    v = output.to_dict()
    assert v["status"] == "filled", v
    f = v["fill"]
    assert (f["price_krw"], f["fee_krw"], f["tax_krw"]) == (price, fee, tax)
    assert Decimal(f["slippage_krw"]) > 0
    assert state.to_dict()["total_cash_krw"] == cash
    assert state.to_dict()["positions"]["005930"]["quantity"] == held
    assert state.to_dict()["reserved_cash_krw"] == "0"
    assert state.to_dict()["positions"]["005930"]["reserved_quantity"] == 0
    assert before == checkpoint(c["ledger"]).identifier
    repeated, repeated_state = execute(**dict(c, ledger=state))
    assert (
        repeated.identifier == output.identifier and repeated_state.identifier == state.identifier
    )
    assert not v["executable"] and not v["operational_eligible"]
    assert v["usage_restriction"] == "synthetic_test_only"


@pytest.mark.parametrize("side,price", [("BUY", "90.01"), ("SELL", "109.99")])
def test_adverse_price_rounding(side: str, price: str) -> None:
    c = ready(side, slippage_rate="0.00001")
    output, _ = execute(**c)
    assert output.to_dict()["fill"]["price_krw"] == price


@pytest.mark.parametrize("rounding,fee", [("down", "1"), ("up", "2"), ("half_up", "2")])
def test_cost_rounding(rounding: str, fee: str) -> None:
    output, _ = execute(**ready(cost_quantum="1", cost_rounding=rounding))
    assert output.to_dict()["fill"]["fee_krw"] == fee


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"execution_price_field": "volume"}, "unsupported_price_field"),
        ({"fill_mode": "partial"}, "partial_fill_unsupported"),
        ({"liquidity": "assume_daily_volume"}, "liquidity_unproven"),
        ({"scope": "operational"}, "real_trading_forbidden"),
        ({"slippage_rate": "NaN"}, "nonfinite_decimal"),
        ({"buy_fee_rate": "Infinity"}, "nonfinite_decimal"),
        ({"sell_tax_rate": "-0.01"}, "negative_amount"),
        ({"slippage_rate": 0.01}, "decimal_string_or_decimal_required"),
        ({"price_quantum": "0.05"}, "power_of_ten_quantum_required"),
    ],
)
def test_unsupported_policy_preserves_account(change: dict[str, Any], reason: str) -> None:
    c = setup(**change)
    output, state = accept_candidate(**c)
    assert output.to_dict()["reason"] == reason
    assert state is c["ledger"]


@pytest.mark.parametrize("key", ["slippage_rate", "buy_fee_rate", "sell_tax_rate", "cost_rounding"])
def test_missing_costs_never_default(key: str) -> None:
    c = setup()
    p = c["policy"].to_dict()
    del p[key]
    c["policy"] = FrozenJSON.freeze(p)
    output, state = accept_candidate(**c)
    assert output.to_dict()["reason"] == "missing_or_unknown_fields"
    assert state is c["ledger"]


def test_missing_policy() -> None:
    c = setup()
    c["policy"] = None
    assert accept_candidate(**c)[0].to_dict()["reason"] == "execution_policy_missing"


def test_same_event_and_past_clock() -> None:
    c = ready()
    c.update(event=c["tape"].events[0], clock=VirtualClock.at(FUTURE, 1))
    output, state = execute(**c)
    assert output.to_dict()["reason"] == "same_or_past_event"
    assert state is c["ledger"]
    c = ready()
    c["clock"] = VirtualClock.at("2026-01-05T23:59:59Z", 2)
    assert execute(**c)[0].to_dict()["reason"] == "future_market_event"


def test_unpublished_price() -> None:
    c = ready(execution_price_field="close")
    assert execute(**c)[0].to_dict()["reason"] == "price_not_public"


@pytest.mark.parametrize(
    "quantity,reason",
    [
        (0, "invalid_quantity_or_sequence"),
        (-1, "invalid_quantity_or_sequence"),
        (5, "partial_fill_unsupported"),
    ],
)
def test_bad_quantity(quantity: int, reason: str) -> None:
    c = ready()
    c["quantity"] = quantity
    output, state = execute(**c)
    assert output.to_dict()["reason"] == reason
    assert state is c["ledger"]


def test_reservation_shortfall_preserves_state() -> None:
    c = setup()
    c["reservation"] = FrozenJSON.freeze({"max_notional_krw": "100", "max_cost_krw": "0"})
    acceptance, ledger = accept_candidate(**c)
    r = ready()
    r.update(acceptance=acceptance, ledger=ledger, expected_revision=ledger.to_dict()["revision"])
    output, state = execute(**r)
    assert output.to_dict()["reason"] == "reservation_shortfall"
    assert state is ledger


def test_revision_and_candidate_budget() -> None:
    c = ready()
    c["expected_revision"] += 1
    assert execute(**c)[0].to_dict()["reason"] == "account_revision_mismatch"
    c = setup()
    c["reservation"] = FrozenJSON.freeze({"max_notional_krw": "1001", "max_cost_krw": "0"})
    assert accept_candidate(**c)[0].to_dict()["reason"] == "admission_budget_exceeded"


def test_fill_identity_conflict() -> None:
    c = ready()
    output, state = execute(**c)
    assert output.to_dict()["status"] == "filled"
    c.update(ledger=state, clock=VirtualClock.at("2026-01-06T00:00:01Z", 2))
    rejected, unchanged = execute(**c)
    assert rejected.to_dict()["reason"] == "fill_identifier_conflict"
    assert unchanged is state


def test_replay_hash_and_decimal_context_independence() -> None:
    c = ready()
    a, state = execute(**c)
    with localcontext() as ctx:
        ctx.prec = 4
        b, other = execute(**c)
    assert a.identifier == b.identifier and state.identifier == other.identifier
    from donghak_stock_vision.backtest.ledger import LedgerCommand

    replay = apply(
        c["ledger"], LedgerCommand(FrozenJSON.freeze(a.to_dict()["ledger_command"])), cutoff=FUTURE
    )
    assert replay.identifier == state.identifier


def test_utc_equivalent_cutoff() -> None:
    c = ready()
    a, _ = execute(**c)
    c["clock"] = VirtualClock.at("2026-01-06T09:00:00+09:00", 2)
    assert execute(**c)[0].identifier == a.identifier


def test_higher_sequence_does_not_allow_same_timestamp_market_event() -> None:
    c = setup(future_event_at="2026-01-05T00:00:00Z")
    acceptance, ledger = accept_candidate(**c)
    request = ready()
    request.update(acceptance=acceptance, ledger=ledger, tape=c["tape"], event=c["tape"].events[1])
    assert execute(**request)[0].to_dict()["reason"] == "same_or_past_event_time"


def test_execution_sequence_must_match_selected_event() -> None:
    c = ready()
    c["clock"] = VirtualClock.at(FUTURE, 3)
    assert execute(**c)[0].to_dict()["reason"] == "execution_sequence_mismatch"


def test_changed_account_after_reservation_is_rejected() -> None:
    from tests.backtest.test_ledger import command

    c = ready()
    change = command(
        "amend_cash",
        2,
        {"max_notional_krw": "960", "max_cost_krw": "0"},
        c["acceptance"].to_dict()["order_id"],
    )
    amended = apply(c["ledger"], change, cutoff=change.document.to_dict()["known_at"])
    c["ledger"] = amended
    c["ledger_sequence"] = 3
    output, unchanged = execute(**c)
    assert output.to_dict()["reason"] == "account_revision_mismatch"
    assert unchanged is amended


def test_tampered_execution_policy_cannot_rebind_existing_reservation() -> None:
    from donghak_stock_vision.data.learning import digest

    c = ready()
    a = c["acceptance"].to_dict()
    a["execution_policy"]["slippage_rate"] = "0"
    a["execution_policy_hash"] = digest(a["execution_policy"])
    c["acceptance"] = FrozenJSON.freeze(a)
    output, state = execute(**c)
    assert output.to_dict()["reason"] == "acceptance_policy_binding_mismatch"
    assert state is c["ledger"]


def test_operational_acceptance_and_candidate_revision_blocked() -> None:
    c = ready()
    a = c["acceptance"].to_dict()
    a["scope"] = "operational"
    c["acceptance"] = FrozenJSON.freeze(a)
    assert execute(**c)[0].to_dict()["reason"] == "real_trading_forbidden"
    c = setup()
    a = c["admission"].to_dict()
    a["account_revision"] += 1
    c["admission"] = FrozenJSON.freeze(a)
    assert accept_candidate(**c)[0].to_dict()["reason"] == "account_revision_mismatch"


def test_tampered_reservation_checkpoint_and_missing_sell_reservation() -> None:
    c = ready("SELL")
    a = c["acceptance"].to_dict()
    a["reservation_checkpoint"]["state"]["positions"]["005930"]["reserved_quantity"] = 0
    c["acceptance"] = FrozenJSON.freeze(a)
    output, state = execute(**c)
    assert output.to_dict()["reason"] == "checkpoint_state_mismatch"
    assert state is c["ledger"]


def test_acceptance_does_not_execute_and_double_reservation_is_blocked() -> None:
    c = setup("SELL")
    before = c["ledger"].to_dict()
    acceptance, reserved = accept_candidate(**c)
    after = reserved.to_dict()
    assert acceptance.to_dict()["status"] == "accepted"
    assert after["positions"]["005930"]["quantity"] == before["positions"]["005930"]["quantity"]
    assert after["total_cash_krw"] == before["total_cash_krw"]
    assert after["positions"]["005930"]["sellable_quantity"] == 0
    assert after["fills"] == {}
    rejected, unchanged = accept_candidate(**dict(c, ledger=reserved))
    assert rejected.to_dict()["reason"] == "account_revision_mismatch"
    assert unchanged is reserved
