"""Explicit arbitrary fixture values; never operational defaults or market evidence."""

from typing import Any

from donghak_stock_vision.data.learning import event_contract
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy

AT = "2020-06-01T07:00:00+00:00"


def policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scope": "research",
        "version": "synthetic_fixture_v1",
        "currency": "KRW",
        "allowed_models": ["fixture_model"],
        "allowed_quality_flags": [
            "universe_incomplete",
            "revision_history_unavailable",
            "retrospective_revisions_possible",
            "calendar_unverified",
            "corporate_actions_unverified",
        ],
        "session_basis": "observed_bars_unverified",
        "effective_from": "2020-01-01T00:00:00+00:00",
        "effective_until": "2021-01-01T00:00:00+00:00",
        "thresholds": {"buy": "0.7", "sell": "0.6"},
        "ttl_seconds": {
            "account": 60,
            "orders": 60,
            "market": 60,
            "analysis": 60,
            "price": 60,
            "max_skew": 60,
        },
        "ttl_boundary": "inclusive",
        "sizing": {
            "buy_budget_krw": "1000",
            "quantity_rounding": "floor",
            "sell_mode": "all",
            "sell_basis": "held",
        },
        "limits": {
            "max_order_notional_krw": "2000",
            "max_position_weight": "0.5",
            "max_gross_weight": "0.8",
            "max_positions": 3,
            "exposure_rule": "post_action_strict",
        },
        "costs": {
            "source": "synthetic_fixture",
            "version": "zero_cost_fixture",
            "market": "KOSPI",
            "rounding": "ceiling",
            "quantum_krw": "1",
            "effective_from": "2020-01-01T00:00:00+00:00",
            "effective_until": "2021-01-01T00:00:00+00:00",
            "buy": {"fee_rate": "0", "tax_rate": "0", "slippage_rate": "0", "fixed_fee_krw": "0"},
            "sell": {"fee_rate": "0", "tax_rate": "0", "slippage_rate": "0", "fixed_fee_krw": "0"},
        },
        "reentry": {
            "unit": "seconds",
            "duration": 120,
            "boundary": "inclusive",
            "require_new_analysis": True,
            "allow_first_entry": True,
        },
        "risk_exit": {
            k: {"mode": "disabled", "parameters": None}
            for k in ("stop_loss", "take_profit", "max_holding_period")
        },
    }


def inputs(held: int = 0, context: str = "prior_decline", score: float = 0.7) -> dict[str, Any]:
    direction = {"prior_decline": "up", "prior_rise": "down", "flat": None}[context]
    analysis = {
        "ticker": "000001",
        "model_version": "fixture_model",
        "snapshot_id": "fixture_snapshot",
        "mode": "historical_research",
        "data_origin": "synthetic",
        "usage_restriction": "synthetic_test_only",
        "anchor_policy": "nominal_eod_1600",
        "model_created_at": "2024-02-01T00:00:00+00:00",
        "snapshot_as_of": "2024-02-01T00:00:00+00:00",
        "anchor_at": AT,
        "data_as_of": None,
        "last_trading_date": "2020-06-01",
        "latest_collected_at": "2024-01-31T00:00:00+00:00",
        "input_status": "ok",
        "quality_flags": [],
        "context": context,
        "signal_state": "neutral",
        **event_contract("observed_bars_unverified"),
    }
    for side in ("up", "down"):
        analysis[f"{side}_score"] = score if direction == side else None
        analysis[f"{side}_status"] = "scored" if direction == side else "context_mismatch"
    common = {
        "origin": "virtual",
        "snapshot_version": "synthetic_fixture_v1",
        "observed_at": AT,
        "received_at": AT,
    }
    return {
        "request": {
            "scope": "research",
            "ticker": "000001",
            "decision_as_of": AT,
            "analysis_id": "fixture_analysis",
        },
        "analysis": analysis,
        "price_basis": "unadjusted",
        "account": {
            **common,
            "account_ref": "virtual_account",
            "complete": True,
            "ticker": "000001",
            "currency": "KRW",
            "quantity": held,
            "sellable_quantity": held,
            "average_price_krw": "100" if held else None,
            "cash_basis": "net_of_reservations",
            "available_cash_krw": str(10000 - held * 100),
            "reserved_cash_krw": "0",
            "equity_krw": "10000",
            "gross_exposure_krw": str(held * 100),
            "position_exposure_krw": str(held * 100),
            "positions_count": int(held > 0),
        },
        "orders": {
            **common,
            "account_ref": "virtual_account",
            "complete": True,
            "items": [],
            "history_complete": True,
            "last_exit": None,
        },
        "market": {
            **common,
            "ticker": "000001",
            "currency": "KRW",
            "tradable": True,
            "session": "open",
            "listing_status": "listed",
            "quality_status": "verified",
            "market": "KOSPI",
            "adjustment": "unadjusted",
            "reference_price_krw": "100",
            "price_observed_at": AT,
        },
    }


def decision(b: dict[str, Any], p: dict[str, Any] | None = None) -> dict[str, Any]:
    from donghak_stock_vision.strategy.engine import decide

    return decide(
        DecisionInput.from_dict(b), DecisionPolicy.from_dict(policy() if p is None else p)
    ).to_dict()
