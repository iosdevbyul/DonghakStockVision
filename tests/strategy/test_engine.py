from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Inexact, getcontext, localcontext
from typing import Any

import pytest

from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy
from donghak_stock_vision.strategy.engine import decide
from tests.strategy.helpers import AT, decision, inputs, policy


@pytest.mark.parametrize(
    "held,context,score,action",
    [
        (0, "prior_decline", 0.6999, "WAIT"),
        (0, "prior_decline", 0.7, "BUY"),
        (0, "prior_decline", 0.7001, "BUY"),
        (5, "prior_rise", 0.5999, "HOLD"),
        (5, "prior_rise", 0.6, "SELL"),
        (5, "prior_rise", 0.6001, "SELL"),
        (5, "prior_decline", 0.9, "HOLD"),
        (0, "prior_rise", 0.9, "WAIT"),
        (0, "flat", 0.9, "WAIT"),
        (5, "flat", 0.9, "HOLD"),
    ],
)
def test_decision_table(held: int, context: str, score: float, action: str) -> None:
    result = decision(inputs(held, context, score))
    assert result["action"] == action, result
    assert result["status"] == "virtual"
    assert not result["operational_eligible"] and not result["executable"]
    assert result["usage_restriction"] == "synthetic_test_only"


def test_analysis_threshold_not_trading_threshold() -> None:
    b, p = inputs(score=0.49), policy()
    p["thresholds"]["buy"] = "0.4"
    assert b["analysis"]["signal_state"] == "neutral"
    assert decision(b, p)["action"] == "BUY"


def test_hold_diagnostics() -> None:
    for score in (0.1, 0.8):
        result = decision(inputs(5, score=score))
        assert result["reason_codes"] == ["no_exit_condition"]
        d = result["diagnostics"]
        assert d["exit_signal_status"] == "no_applicable_exit_signal"
        assert d["exit_model_status"] == "context_mismatch"
        assert len(d["diagnostic_codes"]) == 2 and not d["safety_assurance"]
        assert all(r["mode"] == "disabled" for r in d["risk_exit_rules"].values())


@pytest.mark.parametrize(
    "root,key,value,reason",
    [
        ("analysis", "up_score", None, "applicable_score_unavailable"),
        ("analysis", "down_score", 0.9, "contract_conflict"),
        ("analysis", "up_score", 1.01, "invalid_score"),
        ("analysis", "up_score", True, "invalid_score"),
        ("analysis", "input_status", "stale_data", "analysis_unavailable:stale_data"),
        ("analysis", "quality_flags", ["unknown_flag"], "quality_unaccepted"),
        ("analysis", "usage_restriction", "research_only", "origin_mismatch"),
        ("account", "complete", False, "account_incomplete"),
        ("orders", "complete", False, "orders_unknown"),
        ("orders", "history_complete", False, "exit_history_unknown"),
        ("market", "tradable", False, "trading_unavailable"),
        ("market", "adjustment", "adjusted", "price_basis_mismatch"),
        ("account", "quantity", -1, "invalid_integer:quantity"),
        ("account", "origin", "actual", "virtual_snapshot_required"),
        ("account", "received_at", "2020-06-01T07:00:01+00:00", "future_input"),
        ("account", "observed_at", "2020-06-01T06:58:59+00:00", "stale_input"),
        ("market", "price_observed_at", "2020-06-01T06:58:59+00:00", "stale_price"),
    ],
)
def test_bad_inputs_block(root: str, key: str, value: Any, reason: str) -> None:
    b = inputs(5 if root == "analysis" else 0)
    b[root][key] = value
    result = decision(b)
    assert result["action"] == "WAIT" and result["status"] == "blocked"
    assert reason in result["blocking_reasons"], result
    assert result["proposed_quantity"] is None


@pytest.mark.parametrize(
    "field", ["thresholds", "sizing", "limits", "ttl_seconds", "costs", "reentry", "risk_exit"]
)
def test_policy_missing(field: str) -> None:
    p = policy()
    del p[field]
    result = decision(inputs(), p)
    assert result["status"] == "blocked" and "missing_value" in result["reason_codes"][0]


@pytest.mark.parametrize("field", ["stop_loss", "take_profit", "max_holding_period"])
def test_disabled_required(field: str) -> None:
    p = policy()
    p["risk_exit"][field]["mode"] = "enabled"
    assert decision(inputs(), p)["reason_codes"] == ["unsupported_risk_exit_policy"]
    del p["risk_exit"][field]
    assert decision(inputs(), p)["status"] == "blocked"
    p = policy()
    p["risk_exit"][field]["parameters"] = {"value": "0.1"}
    assert decision(inputs(), p)["reason_codes"] == ["invalid_disabled_risk_policy"]


def test_operational_cannot_be_activated() -> None:
    for origin in ("real", "synthetic"):
        b = inputs()
        b["request"]["scope"] = "operational"
        b["analysis"].update(
            mode="point_in_time", data_origin=origin, operational_status="registered"
        )
        b["approved"] = True
        for p in ({}, policy()):
            result = decision(b, p)
            assert result["reason_codes"] == ["operational_unavailable"]
            assert not result["operational_eligible"] and not result["executable"]


def test_costs_sizing_and_cash() -> None:
    b, p = inputs(), policy()
    p["costs"]["buy"].update(fee_rate="0.01", tax_rate="0.01", slippage_rate="0.01")
    result = decision(b, p)
    assert result["proposed_quantity"] == 9, result
    assert result["estimated_cost"] == {
        "notional": "909.00",
        "fee": "10",
        "tax": "10",
        "slippage": "9.00",
        "cash": "929.00",
    }
    b["account"].update(available_cash_krw="928", equity_krw="928")
    assert decision(b, p)["reason_codes"] == ["insufficient_cash"]
    b["account"].update(available_cash_krw="929", equity_krw="929")
    p["limits"].update(max_position_weight="1", max_gross_weight="1")
    assert decision(b, p)["action"] == "BUY"


@pytest.mark.parametrize(
    "sizing,qty",
    [
        ({"sell_mode": "all", "sell_basis": "held"}, 5),
        ({"sell_mode": "partial", "partial_kind": "quantity", "partial_value": 2}, 2),
        ({"sell_mode": "partial", "partial_kind": "fraction", "partial_value": "0.5"}, 2),
    ],
)
def test_sell_modes(sizing: dict[str, Any], qty: int) -> None:
    p = policy()
    p["sizing"].update(sizing)
    result = decision(inputs(5, "prior_rise", 0.8), p)
    assert result["action"] == "SELL" and result["proposed_quantity"] == qty


def test_no_silent_partial_sale_or_risk_bypass() -> None:
    b, p = inputs(5, "prior_rise", 0.8), policy()
    b["account"]["sellable_quantity"] = 3
    assert decision(b, p)["reason_codes"] == ["insufficient_sellable_quantity"]
    p["sizing"]["sell_basis"] = "available"
    assert decision(b, p)["proposed_quantity"] == 3
    p["limits"]["max_order_notional_krw"] = "100"
    assert decision(b, p)["reason_codes"] == ["order_notional_limit"]
    p = policy()
    p["limits"]["max_position_weight"] = "0.05"
    assert decision(inputs(), p)["reason_codes"] == ["portfolio_limit"]


@pytest.mark.parametrize("state", ["open", "partial", "cancel_pending", "unknown", "submitted"])
def test_pending_orders(state: str) -> None:
    b = inputs()
    b["orders"]["items"] = [
        {
            "ticker": "000001",
            "state": state,
            "remaining_quantity": 1,
            "reserved_cash_krw": "0",
            "received_at": AT,
        }
    ]
    assert decision(b)["reason_codes"] == ["pending_order"]


def test_cooldown_and_first_entry() -> None:
    b, p = inputs(), policy()
    p["reentry"]["allow_first_entry"] = False
    assert decision(b, p)["reason_codes"] == ["first_entry_forbidden"]
    last = {
        "status": "completed",
        "analysis_id": "earlier",
        "completed_at": "2020-06-01T06:58:01+00:00",
        "received_at": AT,
    }
    b["orders"]["last_exit"] = last
    assert decision(b)["reason_codes"] == ["cooldown_active"]
    last["completed_at"] = "2020-06-01T06:58:00+00:00"
    assert decision(b)["action"] == "BUY"
    last["analysis_id"] = b["request"]["analysis_id"]
    assert decision(b)["reason_codes"] == ["new_analysis_required"]


def test_frozen_and_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import sqlite3

    b, p = inputs(), policy()
    immutable, settings = DecisionInput.from_dict(b), DecisionPolicy.from_dict(p)
    b["account"]["quantity"] = 99
    detached = immutable.to_dict()
    detached["analysis"]["up_score"] = 0
    with pytest.raises(FrozenInstanceError):
        immutable.payload_json = "{}"  # type: ignore[misc]
    expected = decide(immutable, settings)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("pure function attempted I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.traps[Inexact] = True
        assert decide(immutable, settings) == expected
    assert getcontext().prec != 3
    assert immutable.to_dict()["account"]["quantity"] == 0
    assert deepcopy(expected) == expected


@pytest.mark.parametrize("age,blocked", [(60, False), (61, True)])
def test_ttl_boundary(age: int, blocked: bool) -> None:
    from datetime import datetime, timedelta

    b = inputs()
    for key in ("observed_at", "received_at"):
        b["account"][key] = (datetime.fromisoformat(AT) - timedelta(seconds=age)).isoformat()
    assert (decision(b)["status"] == "blocked") is blocked


def test_reserved_cash_not_double_counted() -> None:
    b, p = inputs(), policy()
    b["account"].update(available_cash_krw="1000", reserved_cash_krw="9000")
    b["orders"]["items"] = [
        {
            "ticker": "000002",
            "state": "open",
            "remaining_quantity": 90,
            "reserved_cash_krw": "9000",
            "received_at": AT,
        }
    ]
    result = decision(b, p)
    assert result["action"] == "BUY" and result["proposed_quantity"] == 10
    b["account"]["reserved_cash_krw"] = "8999"
    assert decision(b, p)["blocking_reasons"] == ["reservation_mismatch"]


@pytest.mark.parametrize("field", ["fee_rate", "tax_rate", "slippage_rate", "fixed_fee_krw"])
def test_cost_fields_never_default_to_zero(field: str) -> None:
    p = policy()
    del p["costs"]["buy"][field]
    assert decision(inputs(), p)["status"] == "blocked"


def test_pit_research_keeps_previous_day_policy() -> None:
    b = inputs()
    b["analysis"].update(
        mode="point_in_time", data_as_of=AT, model_created_at=AT, latest_collected_at=AT
    )
    assert decision(b)["blocking_reasons"] == ["future_input"]
    b["analysis"]["last_trading_date"] = "2020-05-29"
    assert decision(b)["status"] == "virtual"
    b["analysis"]["last_trading_date"] = "2020-05-20"
    assert decision(b)["blocking_reasons"] == ["stale_analysis"]


def test_corrupt_contract_or_analysis_fails_closed() -> None:
    b = inputs()
    b["analysis"]["horizon_sessions"] = 20
    assert decision(b)["blocking_reasons"] == ["analysis_contract_mismatch"]
    b["analysis"] = []
    assert decision(b)["status"] == "blocked"


def test_verified_session_reentry_requires_calendar() -> None:
    b, p = inputs(), policy()
    p["reentry"].update(unit="verified_sessions", duration=1)
    b["orders"]["last_exit"] = {
        "status": "completed",
        "completed_at": "2020-05-29T07:00:00Z",
        "received_at": "2020-05-29T07:00:00Z",
        "analysis_id": "previous",
    }
    assert decision(b, p)["status"] == "blocked"
    b["market"].update(
        calendar_verified=True,
        sessions=["2020-05-29", "2020-06-01"],
        calendar_coverage={"start": "2020-05-29", "end": "2020-06-01"},
    )
    assert decision(b, p)["action"] == "BUY"
    p["reentry"]["duration"] = 2
    assert decision(b, p)["blocking_reasons"] == ["cooldown_active"]


def test_exit_history_cannot_arrive_after_frozen_order_snapshot() -> None:
    b = inputs()
    b["orders"].update(observed_at="2020-06-01T06:59:30Z", received_at="2020-06-01T06:59:30Z")
    b["orders"]["last_exit"] = {
        "status": "completed",
        "completed_at": "2020-06-01T06:00:00Z",
        "received_at": AT,
        "analysis_id": "previous",
    }
    assert decision(b)["blocking_reasons"] == ["future_input"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_json_rejected(value: float) -> None:
    b = inputs()
    b["analysis"]["up_score"] = value
    with pytest.raises(ValueError):
        DecisionInput.from_dict(b)
