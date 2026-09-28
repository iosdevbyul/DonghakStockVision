"""Fail-closed shape, provenance and temporal boundaries; no execution simulation."""

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from donghak_stock_vision.backtest.contracts import (
    Checkpoint,
    Contract,
    ExecutionPolicy,
    MarketEvent,
    RunManifest,
    SimulationAccount,
    VirtualFill,
    VirtualOrder,
)
from donghak_stock_vision.backtest.validation import (
    POLICY_REFS,
    ContractError,
    validate_event_sequence,
    validate_run,
)
from tests.backtest.helpers import CLASSES, H, T, manifest, payload, policy


@pytest.mark.parametrize(
    "field", POLICY_REFS.split() + ["execution_mode", "p1_status", "p10_status"]
)
def test_explicit_policy_fields_required(field: str) -> None:
    source = payload(ExecutionPolicy)
    del source[field]
    with pytest.raises(ContractError, match="missing_or_unknown_fields"):
        ExecutionPolicy.from_dict(source)


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize(
    "field,value", [("scope", "operational"), ("executable", True), ("operational_eligible", True)]
)
def test_operational_paths_forbidden(cls: type[Contract], field: str, value: Any) -> None:
    source = payload(cls)
    source[field] = value
    with pytest.raises(ContractError):
        cls.from_dict(source)


@pytest.mark.parametrize("cls", [VirtualOrder, VirtualFill])
@pytest.mark.parametrize("field,value", [("broker_route", "live-broker"), ("virtual", False)])
def test_real_routing_forbidden(cls: type[Contract], field: str, value: Any) -> None:
    source = payload(cls)
    source[field] = value
    with pytest.raises(ContractError):
        cls.from_dict(source)


@pytest.mark.parametrize(
    "field,value",
    [("execution_mode", "live"), ("p1_status", "approved"), ("p10_status", "approved")],
)
def test_policy_cannot_claim_approval(field: str, value: str) -> None:
    source = payload(ExecutionPolicy)
    source[field] = value
    with pytest.raises(ContractError, match="invalid_enum"):
        ExecutionPolicy.from_dict(source)


@pytest.mark.parametrize(
    "cls,field,value",
    [
        (MarketEvent, "information_mode", "operational"),
        (MarketEvent, "data_origin", "mixed"),
        (MarketEvent, "usage_restriction", "research_only"),
        (MarketEvent, "session", "regular"),
        (MarketEvent, "adjustment", "auto"),
        (MarketEvent, "quality_status", "ok"),
        (VirtualOrder, "status", "done"),
        (VirtualOrder, "side", "SHORT"),
        (Checkpoint, "status", "success"),
        (RunManifest, "execution_mode", "auto"),
    ],
)
def test_unknown_enums_rejected(cls: type[Contract], field: str, value: str) -> None:
    source = payload(cls)
    source[field] = value
    with pytest.raises(ContractError):
        cls.from_dict(source)


@pytest.mark.parametrize(
    "amount",
    ["NaN", "Infinity", "-Infinity", "-1", 1.25, 10, True, Decimal("NaN"), "1e100000", "", None],
)
def test_invalid_price_rejected(amount: Any) -> None:
    source = payload(MarketEvent)
    source["public_fields"]["open"] = amount
    with pytest.raises(ContractError):
        MarketEvent.from_dict(source)


@pytest.mark.parametrize("quantity", [-1, 1.0, True, "1", 2**63, None])
def test_invalid_quantity_rejected(quantity: Any) -> None:
    source = payload(VirtualOrder)
    source["quantity"] = quantity
    with pytest.raises(ContractError):
        VirtualOrder.from_dict(source)


@pytest.mark.parametrize("stamp", ["2026-01-05T00:00:00", datetime(2026, 1, 5), "nonsense", None])
def test_timezone_required(stamp: Any) -> None:
    source = payload(MarketEvent)
    source["available_at"] = stamp
    with pytest.raises(ContractError):
        MarketEvent.from_dict(source)


def event(sequence: int, available: str = T) -> MarketEvent:
    source = payload(MarketEvent)
    source.update(sequence=sequence, available_at=available)
    return MarketEvent.from_dict(source)


def test_equal_instants_and_offset_boundary_use_utc() -> None:
    items = [event(1, "2026-01-04T19:00:00-05:00"), event(2, "2026-01-05T00:00:00Z")]
    validate_event_sequence(items, cutoff=T)
    validate_event_sequence(items, cutoff="2026-01-05T00:00:00.000001Z")
    with pytest.raises(ContractError, match="future_event"):
        validate_event_sequence(items, cutoff="2026-01-04T23:59:59.999999Z")


@pytest.mark.parametrize(
    "items,reason",
    [
        ([event(1), event(1)], "nonincreasing_sequence"),
        ([event(2), event(1)], "nonincreasing_sequence"),
        ([event(1, "2026-01-05T00:00:01Z"), event(2)], "availability_time_reversed"),
    ],
)
def test_event_order(items: list[MarketEvent], reason: str) -> None:
    with pytest.raises(ContractError, match=reason):
        validate_event_sequence(items, cutoff="2026-01-06T00:00:00Z")


def test_run_preflight_includes_sequence_and_stays_unapproved() -> None:
    report = validate_run(manifest(), policy(), [event(1), event(2)])
    assert report.ready_for_execution is False
    assert {"p1_pending", "p10_pending", "policy_resolution_pending"} <= set(report.blockers)
    with pytest.raises(ContractError, match="nonincreasing_sequence"):
        validate_run(manifest(), policy(), [event(1), event(1)])


def with_origin(value: Any, mode: str, origin: str) -> Any:
    if isinstance(value, list):
        return [with_origin(v, mode, origin) for v in value]
    if not isinstance(value, dict):
        return value
    output = {k: with_origin(v, mode, origin) for k, v in value.items()}
    if "information_mode" in output:
        output.update(
            information_mode=mode,
            data_origin=origin,
            usage_restriction="synthetic_test_only"
            if origin == "synthetic"
            else "research_only"
            if mode == "historical_research"
            else "pit_review_required",
        )
    if "availability_assumption_id" in output:
        output["availability_assumption_id"] = H if mode == "historical_research" else None
    return output


@pytest.mark.parametrize("execution", ["backtest", "paper"])
@pytest.mark.parametrize("mode", ["historical_research", "point_in_time"])
@pytest.mark.parametrize("origin", ["real", "synthetic"])
def test_execution_information_and_origin_are_independent(
    execution: str, mode: str, origin: str
) -> None:
    p = payload(ExecutionPolicy)
    p["execution_mode"] = execution
    selected = ExecutionPolicy.from_dict(p)
    m = RunManifest.from_dict(with_origin(manifest(selected).to_dict(), mode, origin))
    e = with_origin(payload(MarketEvent), mode, origin)
    e["run_id"] = m.identifier
    assert not validate_run(m, selected, [MarketEvent.from_dict(e)]).ready_for_execution


@pytest.mark.parametrize("key", ["models", "market_snapshots", "analyses", "quality_manifest"])
def test_mixed_nested_provenance_rejected(key: str) -> None:
    source = payload(RunManifest)
    source[key] = with_origin(source[key], "historical_research", "real")
    with pytest.raises(ContractError, match="mixed_input_provenance"):
        RunManifest.from_dict(source)


def test_mixed_run_records_rejected_even_with_matching_run_id() -> None:
    source = with_origin(payload(MarketEvent), "historical_research", "real")
    with pytest.raises(ContractError, match="mixed_run_provenance"):
        validate_run(manifest(), policy(), [MarketEvent.from_dict(source)])
    with pytest.raises(ContractError, match="mixed_event_run"):
        validate_event_sequence([event(1), MarketEvent.from_dict(source)], cutoff=T)


def test_pit_cannot_backdate_late_receipt() -> None:
    source = payload(MarketEvent)
    source["source_received_at"] = "2026-02-01T00:00:00Z"
    MarketEvent.from_dict(source)  # historical hypothetical availability is explicit
    source = with_origin(source, "point_in_time", "synthetic")
    with pytest.raises(ContractError, match="pit_receipt_after_availability"):
        MarketEvent.from_dict(source)


@pytest.mark.parametrize("field", ["event_at", "quality_available_at"])
def test_future_event_or_quality_rejected(field: str) -> None:
    source = payload(MarketEvent)
    source[field] = "2026-01-05T00:00:00.000001Z"
    with pytest.raises(ContractError):
        MarketEvent.from_dict(source)


def test_missing_quality_evidence_and_duplicate_fields_rejected() -> None:
    source = payload(MarketEvent)
    source["quality_evidence_ids"] = []
    with pytest.raises(ContractError, match="quality_evidence_required"):
        MarketEvent.from_dict(source)
    source = payload(MarketEvent)
    source["quality_evidence_ids"] = [H, H]
    with pytest.raises(ContractError, match="duplicate_values"):
        MarketEvent.from_dict(source)
    source = payload(MarketEvent)
    source["public_fields"]["future_close"] = "100"
    with pytest.raises(ContractError, match="unsupported_public_field"):
        MarketEvent.from_dict(source)


@pytest.mark.parametrize(
    "stamp,allowed",
    [
        ("2026-01-05T23:59:59.999999Z", True),
        ("2026-01-06T00:00:00Z", True),
        ("2026-01-06T00:00:00.000001Z", False),
    ],
)
def test_run_end_boundary(stamp: str, allowed: bool) -> None:
    record = event(1, stamp)
    if allowed:
        validate_run(manifest(), policy(), [record])
    else:
        with pytest.raises(ContractError, match="outside_run_period"):
            validate_run(manifest(), policy(), [record])


def test_explicit_exclusive_boundary_and_policy_identity() -> None:
    source = payload(RunManifest)
    source["time_basis"]["boundary"] = "exclusive"
    m = RunManifest.from_dict(source)
    e = payload(MarketEvent)
    e["run_id"] = m.identifier
    with pytest.raises(ContractError, match="outside_run_period"):
        validate_run(m, policy(), [MarketEvent.from_dict(e)])
    source = payload(ExecutionPolicy)
    source["version"] = "different"
    with pytest.raises(ContractError, match="execution_policy_mismatch"):
        validate_run(manifest(), ExecutionPolicy.from_dict(source), [])


@pytest.mark.parametrize(
    "field,value",
    [("remaining_quantity", 1), ("status", "filled"), ("status", "partial"), ("status", "open")],
)
def test_inconsistent_order_snapshot_rejected(field: str, value: Any) -> None:
    source = payload(VirtualOrder)
    source[field] = value
    with pytest.raises(ContractError):
        VirtualOrder.from_dict(source)


def test_account_reservations_and_future_initial_account() -> None:
    source = payload(SimulationAccount)
    source["positions"] = [
        {
            "ticker": "005930",
            "quantity": 2,
            "sellable_quantity": 2,
            "reserved_quantity": 1,
            "cost_basis_krw": "100",
        }
    ]
    with pytest.raises(ContractError, match="position_reservation"):
        SimulationAccount.from_dict(source)
    source = payload(RunManifest)
    source["initial_account"]["received_at"] = "2026-01-05T00:00:00.000001Z"
    with pytest.raises(ContractError, match="future_initial_account"):
        RunManifest.from_dict(source)


def test_partial_and_terminal_records_do_not_generate_fills() -> None:
    source = payload(VirtualOrder)
    source.update(status="partial", accepted_at=T, remaining_quantity=1, filled_quantity=1)
    partial = VirtualOrder.from_dict(source)
    source.update(status="filled", remaining_quantity=0, filled_quantity=2)
    filled = VirtualOrder.from_dict(source)
    assert partial.identifier != filled.identifier
    assert partial.to_dict()["remaining_quantity"] == 1
    source["reserved_cash_krw"] = "1"
    with pytest.raises(ContractError, match="terminal_reservation"):
        VirtualOrder.from_dict(source)


@pytest.mark.parametrize("cls", CLASSES)
def test_no_omitted_or_extra_contract_fields(cls: type[Contract]) -> None:
    source = payload(cls)
    del source["schema_version"]
    with pytest.raises(ContractError):
        cls.from_dict(source)
    source = payload(cls)
    source["approval_override"] = True
    with pytest.raises(ContractError):
        cls.from_dict(source)


def test_fill_confirmation_and_checkpoint_consistency() -> None:
    source = payload(VirtualFill)
    source["fill_at"] = "2026-01-05T00:00:00.000001Z"
    with pytest.raises(ContractError, match="fill_time_order"):
        VirtualFill.from_dict(source)
    source = payload(Checkpoint)
    source.update(status="completed", missing_reasons=["unknown_mark"])
    with pytest.raises(ContractError, match="incomplete_checkpoint"):
        Checkpoint.from_dict(source)
    source = payload(Checkpoint)
    source["manifest_hash"] = "b" * 64
    with pytest.raises(ContractError, match="checkpoint_manifest_mismatch"):
        Checkpoint.from_dict(source)


def test_period_and_policy_bindings_are_not_inferred() -> None:
    source = payload(RunManifest)
    source["end_at"] = "2026-01-04T00:00:00Z"
    with pytest.raises(ContractError, match="invalid_run_period"):
        RunManifest.from_dict(source)
    source = payload(RunManifest)
    source["execution_mode"] = "paper"
    with pytest.raises(ContractError, match="execution_mode_mismatch"):
        validate_run(RunManifest.from_dict(source), policy(), [])
    source = payload(RunManifest)
    source["time_basis"]["ordering_policy_id"] = "b" * 64
    with pytest.raises(ContractError, match="ordering_policy_mismatch"):
        validate_run(RunManifest.from_dict(source), policy(), [])
