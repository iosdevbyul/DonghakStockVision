"""Explicit synthetic fixtures, never trading-policy recommendations."""

from typing import Any

from donghak_stock_vision.backtest.contracts import (
    Checkpoint,
    Contract,
    ExecutionPolicy,
    LedgerEvent,
    MarketEvent,
    RunManifest,
    SimulationAccount,
    VirtualFill,
    VirtualOrder,
)
from donghak_stock_vision.backtest.validation import POLICY_REFS

H = "a" * 64
T = "2026-01-05T09:00:00+09:00"
END = "2026-01-06T09:00:00+09:00"
COMMON = {
    "schema_version": 1,
    "scope": "research",
    "executable": False,
    "operational_eligible": False,
}
ORIGIN = {
    "information_mode": "historical_research",
    "data_origin": "synthetic",
    "usage_restriction": "synthetic_test_only",
}
CLASSES = (
    ExecutionPolicy,
    RunManifest,
    MarketEvent,
    SimulationAccount,
    VirtualOrder,
    VirtualFill,
    LedgerEvent,
    Checkpoint,
)


def ref(name: str) -> dict[str, Any]:
    return {"artifact_id": name, "version": "fixture-v1", "content_hash": H}


def data_ref(name: str) -> dict[str, Any]:
    return {**ref(name), **ORIGIN}


def account() -> dict[str, Any]:
    return {
        "account_id": "fixture-account",
        "revision": 0,
        "observed_at": T,
        "received_at": T,
        "complete": True,
        "virtual": True,
        "currency": "KRW",
        "available_cash_krw": "10000",
        "reserved_cash_krw": "0",
        "positions": [],
        "valuation_id": H,
    }


def policy() -> ExecutionPolicy:
    return ExecutionPolicy.from_dict(
        {
            **COMMON,
            "version": "fixture-v1",
            "execution_mode": "backtest",
            "p1_status": "pending",
            "p10_status": "pending",
            **{key: ref(key) for key in POLICY_REFS.split()},
        }
    )


def manifest(p: ExecutionPolicy | None = None) -> RunManifest:
    selected = p if p is not None else policy()
    return RunManifest.from_dict(
        {
            **COMMON,
            **ORIGIN,
            "execution_mode": selected.to_dict()["execution_mode"],
            "start_at": T,
            "end_at": END,
            "initial_account": account(),
            "execution_policy_id": selected.identifier,
            "market_snapshots": [data_ref("snapshot")],
            "models": [data_ref("model")],
            "analyses": [data_ref("analysis")],
            "quality_manifest": data_ref("quality"),
            "calendar": data_ref("calendar"),
            "universe": data_ref("universe"),
            "versions": {
                "code_hash": H,
                "environment_hash": H,
                "feature_version": "v1",
                "label_version": "v1",
                "engine_version": "v1",
            },
            "time_basis": {
                "comparison_timezone": "UTC",
                "trading_timezone": "Asia/Seoul",
                "boundary": "inclusive",
                "ordering_policy_id": H,
            },
            "availability_assumption_id": H,
            "random_seed": None,
        }
    )


def payload(cls: type[Contract]) -> dict[str, Any]:
    if cls is ExecutionPolicy:
        return policy().to_dict()
    if cls is RunManifest:
        return manifest().to_dict()
    base = {**COMMON, **ORIGIN, "run_id": manifest().identifier}
    bodies: dict[type[Contract], dict[str, Any]] = {
        MarketEvent: {
            "sequence": 1,
            "ticker": "005930",
            "trading_date": "2026-01-05",
            "event_at": T,
            "source_received_at": T,
            "available_at": T,
            "revision": "r1",
            "source": data_ref("snapshot"),
            "public_fields": {"open": "100", "volume": 10},
            "quality_status": "verified",
            "quality_flags": [],
            "quality_available_at": T,
            "quality_evidence_ids": [H],
            "market": "KOSPI",
            "session": "open",
            "listing_status": "listed",
            "halt_status": "trading",
            "adjustment": "unadjusted",
            "availability_assumption_id": H,
        },
        SimulationAccount: account(),
        VirtualOrder: {
            "decision_id": H,
            "idempotency_key": "step-1",
            "account_revision": 0,
            "ticker": "005930",
            "side": "BUY",
            "quantity": 2,
            "remaining_quantity": 2,
            "filled_quantity": 0,
            "cancelled_quantity": 0,
            "status": "submitted",
            "submitted_at": T,
            "accepted_at": None,
            "expires_at": END,
            "cancelled_at": None,
            "reserved_cash_krw": "0",
            "reserved_quantity": 0,
            "policy_id": H,
            "virtual": True,
            "broker_route": None,
        },
        VirtualFill: {
            "order_id": H,
            "sequence": 2,
            "ticker": "005930",
            "quantity": 1,
            "price_krw": "100",
            "notional_krw": "100",
            "fee_krw": "1",
            "tax_krw": "0",
            "benchmark_price_krw": "99",
            "slippage_krw": "1",
            "fill_at": T,
            "fill_known_at": T,
            "source_event_id": H,
            "liquidity_evidence_id": H,
            "policy_id": H,
            "cost_charge_id": H,
            "virtual": True,
            "simulation_only": True,
            "broker_route": None,
        },
        LedgerEvent: {
            "sequence": 3,
            "previous_state_hash": H,
            "cause_kind": "fill",
            "cause_id": H,
            "cash_delta_krw": "-101",
            "reserved_cash_delta_krw": "0",
            "position_changes": [
                {
                    "ticker": "005930",
                    "quantity_increase": 1,
                    "quantity_decrease": 0,
                    "cost_basis_delta_krw": "101",
                }
            ],
            "effective_at": T,
            "available_at": T,
            "recorded_at": END,
            "cost_charge_id": H,
            "virtual": True,
        },
        Checkpoint: {
            "last_sequence": 3,
            "state_hash": H,
            "manifest_hash": manifest().identifier,
            "status": "running",
            "as_of": T,
            "recorded_at": END,
            "dependency_ids": [H],
            "missing_reasons": [],
            "virtual": True,
        },
    }
    return {**base, **bodies[cls]}
