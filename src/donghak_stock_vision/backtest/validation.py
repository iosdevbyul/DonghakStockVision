"""Pure structural validation only: no clock, storage, fills, or policy defaults."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast

from donghak_stock_vision.data.schema import validate_ticker

if TYPE_CHECKING:
    from donghak_stock_vision.backtest.contracts import (
        Contract,
        ExecutionPolicy,
        MarketEvent,
        RunManifest,
    )


class ContractError(ValueError):
    """Malformed or inconsistent contract, not an execution failure."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ContractError(reason)


def fields(value: Any, names: str) -> dict[str, Any]:
    require(isinstance(value, dict), "expected_object")
    expected = set(names.split())
    require(set(value) == expected, "missing_or_unknown_fields")
    return dict(value)


def text(value: Any) -> str:
    require(isinstance(value, str) and bool(value.strip()), "expected_nonempty_string")
    return str(value)


def choice(value: Any, options: str) -> str:
    require(type(value) is str and value in options.split(), "invalid_enum")
    return str(value)


def flag(value: Any) -> bool:
    require(type(value) is bool, "expected_boolean")
    return bool(value)


def integer(value: Any, *, positive: bool = False) -> int:
    require(
        type(value) is int and int(positive) <= value <= 2**63 - 1, "invalid_quantity_or_sequence"
    )
    return int(value)


def sha(value: Any) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid_hash"
    )
    return str(value)


def utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        require(isinstance(parsed, datetime), "invalid_timestamp")
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "timezone_required")
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError) as error:
        raise ContractError("invalid_timezone_aware_timestamp") from error


def instant(value: Any) -> str:
    return utc(value).isoformat(timespec="microseconds")


def money(value: Any, *, signed: bool = False, positive: bool = False) -> str:
    require(type(value) in {str, Decimal}, "decimal_string_or_decimal_required")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ContractError("invalid_decimal") from error
    require(number.is_finite(), "nonfinite_decimal")
    exponent = number.as_tuple().exponent
    require(isinstance(exponent, int), "invalid_decimal")
    require(len(number.as_tuple().digits) <= 24 and abs(int(exponent)) <= 12, "decimal_bounds")
    require(signed or number >= 0, "negative_amount")
    require(not positive or number > 0, "nonpositive_price")
    if number.is_zero():
        return "0"
    output = format(number, "f")  # No quantize/normalize: independent of ambient Decimal context.
    return output.rstrip("0").rstrip(".") if "." in output else output


def strings(value: Any, *, hashes: bool = False) -> list[str]:
    require(type(value) is list, "expected_list")
    result = [sha(v) if hashes else text(v) for v in value]
    require(len(result) == len(set(result)), "duplicate_values")
    return sorted(result)


def reference(value: Any) -> dict[str, Any]:
    r = fields(value, "artifact_id version content_hash")
    return {
        "artifact_id": text(r["artifact_id"]),
        "version": text(r["version"]),
        "content_hash": sha(r["content_hash"]),
    }


def origin(value: dict[str, Any]) -> None:
    choice(value["information_mode"], "historical_research point_in_time")
    choice(value["data_origin"], "real synthetic")
    expected = (
        "synthetic_test_only"
        if value["data_origin"] == "synthetic"
        else (
            "research_only"
            if value["information_mode"] == "historical_research"
            else "pit_review_required"
        )
    )
    require(value["usage_restriction"] == expected, "origin_restriction_mismatch")


def data_reference(value: Any, parent: dict[str, Any]) -> dict[str, Any]:
    r = fields(
        value, "artifact_id version content_hash information_mode data_origin usage_restriction"
    )
    origin(r)
    for key in ("information_mode", "data_origin", "usage_restriction"):
        require(r[key] == parent[key], "mixed_input_provenance")
    base = reference({k: r[k] for k in ("artifact_id", "version", "content_hash")})
    return {**r, **base}


def references(value: Any, parent: dict[str, Any]) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) > 0, "required_artifact_references")
    result = [data_reference(r, parent) for r in value]
    ids = [r["artifact_id"] for r in result]
    require(len(ids) == len(set(ids)), "duplicate_artifact_reference")
    return sorted(result, key=lambda r: r["artifact_id"])


def position(value: Any) -> dict[str, Any]:
    p = fields(value, "ticker quantity sellable_quantity reserved_quantity cost_basis_krw")
    validate_ticker(p["ticker"])
    for key in ("quantity", "sellable_quantity", "reserved_quantity"):
        p[key] = integer(p[key])
    require(
        p["sellable_quantity"] + p["reserved_quantity"] <= p["quantity"], "position_reservation"
    )
    p["cost_basis_krw"] = money(p["cost_basis_krw"])
    return p


ACCOUNT_FIELDS = (
    "account_id revision observed_at received_at complete virtual currency available_cash_krw "
    "reserved_cash_krw positions valuation_id"
)


def account(value: Any) -> dict[str, Any]:
    a = fields(value, ACCOUNT_FIELDS)
    a["account_id"] = text(a["account_id"])
    a["revision"] = integer(a["revision"])
    for key in ("observed_at", "received_at"):
        a[key] = instant(a[key])
    require(utc(a["observed_at"]) <= utc(a["received_at"]), "account_time_order")
    a["complete"] = flag(a["complete"])
    require(a["virtual"] is True and a["currency"] == "KRW", "virtual_krw_account_required")
    for key in ("available_cash_krw", "reserved_cash_krw"):
        a[key] = money(a[key])
    require(type(a["positions"]) is list, "expected_positions")
    a["positions"] = sorted([position(p) for p in a["positions"]], key=lambda p: p["ticker"])
    tickers = [p["ticker"] for p in a["positions"]]
    require(len(tickers) == len(set(tickers)), "duplicate_position")
    a["valuation_id"] = sha(a["valuation_id"])
    return a


COMMON = "schema_version scope executable operational_eligible"
PROVENANCE = "information_mode data_origin usage_restriction"
POLICY_REFS = (
    "decision_policy availability_policy fill_policy cost_policy "
    "liquidity_policy reservation_policy "
    "settlement_policy valuation_policy ordering_policy end_policy storage_policy"
)
BODY_FIELDS = {
    "execution_policy": "version execution_mode p1_status p10_status " + POLICY_REFS,
    "run_manifest": PROVENANCE
    + " execution_mode start_at end_at initial_account execution_policy_id "
    "market_snapshots models analyses quality_manifest calendar universe versions time_basis "
    "availability_assumption_id random_seed",
    "market_event": PROVENANCE + " run_id sequence ticker trading_date event_at source_received_at "
    "available_at revision source public_fields quality_status quality_flags quality_available_at "
    "quality_evidence_ids market session listing_status halt_status adjustment "
    "availability_assumption_id",
    "simulation_account": PROVENANCE + " run_id " + ACCOUNT_FIELDS,
    "virtual_order": PROVENANCE
    + " run_id decision_id idempotency_key account_revision ticker side "
    "quantity remaining_quantity filled_quantity cancelled_quantity status "
    "submitted_at accepted_at "
    "expires_at cancelled_at reserved_cash_krw reserved_quantity policy_id virtual broker_route",
    "virtual_fill": PROVENANCE + " run_id order_id sequence ticker quantity price_krw notional_krw "
    "fee_krw tax_krw benchmark_price_krw slippage_krw fill_at fill_known_at source_event_id "
    "liquidity_evidence_id policy_id cost_charge_id virtual simulation_only broker_route",
    "ledger_event": PROVENANCE + " run_id sequence previous_state_hash cause_kind cause_id "
    "cash_delta_krw reserved_cash_delta_krw position_changes effective_at available_at recorded_at "
    "cost_charge_id virtual",
    "checkpoint": PROVENANCE + " run_id last_sequence state_hash manifest_hash status as_of "
    "recorded_at dependency_ids missing_reasons virtual",
}


def normalize(kind: str, value: Any) -> dict[str, Any]:
    require(kind in BODY_FIELDS, "unknown_contract_kind")
    v = fields(value, COMMON + " " + BODY_FIELDS[kind])
    require(type(v["schema_version"]) is int and v["schema_version"] == 1, "schema_version")
    require(v["scope"] == "research", "operational_forbidden")
    require(v["executable"] is False and v["operational_eligible"] is False, "execution_forbidden")
    if "virtual" in v:
        require(v["virtual"] is True, "virtual_required")
    if "broker_route" in v:
        require(v["broker_route"] is None, "broker_route_forbidden")
    if kind != "execution_policy":
        origin(v)
    if "run_id" in v:
        v["run_id"] = sha(v["run_id"])
    if kind in {"run_manifest", "execution_policy"}:
        choice(v["execution_mode"], "backtest paper")
    validators = {
        "execution_policy": policy_fields,
        "run_manifest": manifest_fields,
        "market_event": event_fields,
        "simulation_account": account_fields,
        "virtual_order": order_fields,
        "virtual_fill": fill_fields,
        "ledger_event": ledger_fields,
        "checkpoint": checkpoint_fields,
    }
    validators[kind](v)
    return v


def policy_fields(v: dict[str, Any]) -> None:
    v["version"] = text(v["version"])
    # PR-A is authorized; P1/P10 and any execution path are explicitly NOT approved.
    choice(v["p1_status"], "pending")
    choice(v["p10_status"], "pending")
    for key in POLICY_REFS.split():
        v[key] = reference(v[key])


def assumption(v: dict[str, Any]) -> None:
    if v["information_mode"] == "historical_research":
        v["availability_assumption_id"] = sha(v["availability_assumption_id"])
    else:
        require(v["availability_assumption_id"] is None, "pit_cannot_use_assumed_availability")


def manifest_fields(v: dict[str, Any]) -> None:
    v["start_at"], v["end_at"] = instant(v["start_at"]), instant(v["end_at"])
    require(utc(v["start_at"]) <= utc(v["end_at"]), "invalid_run_period")
    v["initial_account"] = account(v["initial_account"])
    require(v["initial_account"]["complete"] is True, "incomplete_initial_account")
    require(
        utc(v["initial_account"]["received_at"]) <= utc(v["start_at"]), "future_initial_account"
    )
    v["execution_policy_id"] = sha(v["execution_policy_id"])
    for key in ("market_snapshots", "models", "analyses"):
        v[key] = references(v[key], v)
    for key in ("quality_manifest", "calendar", "universe"):
        v[key] = data_reference(v[key], v)
    version = fields(
        v["versions"], "code_hash environment_hash feature_version label_version engine_version"
    )
    for key in version:
        version[key] = sha(version[key]) if key.endswith("hash") else text(version[key])
    v["versions"] = version
    basis = fields(
        v["time_basis"], "comparison_timezone trading_timezone boundary ordering_policy_id"
    )
    require(
        basis["comparison_timezone"] == "UTC" and basis["trading_timezone"] == "Asia/Seoul",
        "invalid_time_basis",
    )
    choice(basis["boundary"], "inclusive exclusive")
    basis["ordering_policy_id"] = sha(basis["ordering_policy_id"])
    v["time_basis"] = basis
    assumption(v)
    if v["random_seed"] is not None:
        v["random_seed"] = integer(v["random_seed"])


def event_fields(v: dict[str, Any]) -> None:
    v["sequence"] = integer(v["sequence"])
    validate_ticker(v["ticker"])
    require(type(v["trading_date"]) is str, "invalid_trading_date")
    try:
        require(
            date.fromisoformat(v["trading_date"]).isoformat() == v["trading_date"],
            "invalid_trading_date",
        )
    except ValueError as error:
        raise ContractError("invalid_trading_date") from error
    for key in ("event_at", "source_received_at", "available_at", "quality_available_at"):
        v[key] = instant(v[key])
    require(utc(v["event_at"]) <= utc(v["source_received_at"]), "event_after_receipt")
    require(utc(v["event_at"]) <= utc(v["available_at"]), "event_after_availability")
    if v["information_mode"] == "point_in_time":
        require(
            utc(v["source_received_at"]) <= utc(v["available_at"]), "pit_receipt_after_availability"
        )
    require(utc(v["quality_available_at"]) <= utc(v["available_at"]), "future_quality_evidence")
    v["revision"] = text(v["revision"])
    v["source"] = data_reference(v["source"], v)
    public = v["public_fields"]
    require(type(public) is dict and bool(public), "public_fields_required")
    require(
        set(public) <= {"open", "high", "low", "close", "price", "volume", "trading_value"},
        "unsupported_public_field",
    )
    v["public_fields"] = {k: integer(x) if k == "volume" else money(x) for k, x in public.items()}
    choice(v["quality_status"], "verified unverified invalid")
    v["quality_flags"] = strings(v["quality_flags"])
    v["quality_evidence_ids"] = strings(v["quality_evidence_ids"], hashes=True)
    require(
        v["quality_status"] != "verified" or bool(v["quality_evidence_ids"]),
        "quality_evidence_required",
    )
    choice(v["market"], "KOSPI KOSDAQ KONEX")
    choice(v["session"], "open closed unknown")
    choice(v["listing_status"], "listed delisted unknown")
    choice(v["halt_status"], "trading halted unknown")
    choice(v["adjustment"], "adjusted unadjusted unknown")
    assumption(v)


def account_fields(v: dict[str, Any]) -> None:
    v.update(account({key: v[key] for key in ACCOUNT_FIELDS.split()}))


def order_fields(v: dict[str, Any]) -> None:
    for key in ("decision_id", "policy_id"):
        v[key] = sha(v[key])
    v["idempotency_key"] = text(v["idempotency_key"])
    v["account_revision"] = integer(v["account_revision"])
    validate_ticker(v["ticker"])
    choice(v["side"], "BUY SELL")
    for key in (
        "quantity",
        "remaining_quantity",
        "filled_quantity",
        "cancelled_quantity",
        "reserved_quantity",
    ):
        v[key] = integer(v[key], positive=key == "quantity")
    require(
        v["quantity"] == v["remaining_quantity"] + v["filled_quantity"] + v["cancelled_quantity"],
        "order_quantity_conservation",
    )
    require(v["reserved_quantity"] <= v["remaining_quantity"], "order_reservation")
    v["reserved_cash_krw"] = money(v["reserved_cash_krw"])
    choice(
        v["status"],
        "submitted open partial cancel_pending unknown filled cancelled rejected expired",
    )
    v["submitted_at"] = instant(v["submitted_at"])
    for key in ("accepted_at", "expires_at", "cancelled_at"):
        if v[key] is not None:
            v[key] = instant(v[key])
            require(utc(v[key]) >= utc(v["submitted_at"]), "order_time_order")
    if v["status"] in {"open", "partial", "cancel_pending", "filled"}:
        require(v["accepted_at"] is not None, "acceptance_time_required")
    if v["status"] == "partial":
        require(
            0 < v["filled_quantity"] < v["quantity"] and v["remaining_quantity"] > 0,
            "partial_quantity",
        )
    if v["status"] in {"filled", "cancelled", "rejected", "expired"}:
        require(
            v["remaining_quantity"] == v["reserved_quantity"] == 0
            and Decimal(v["reserved_cash_krw"]) == 0,
            "terminal_reservation",
        )
    if v["status"] == "filled":
        require(v["filled_quantity"] == v["quantity"], "filled_quantity")
    if v["status"] == "cancelled":
        require(v["cancelled_at"] is not None, "cancellation_time_required")
    if v["status"] == "expired":
        require(v["expires_at"] is not None, "expiry_time_required")


def fill_fields(v: dict[str, Any]) -> None:
    for key in (
        "order_id",
        "source_event_id",
        "liquidity_evidence_id",
        "policy_id",
        "cost_charge_id",
    ):
        v[key] = sha(v[key])
    v["sequence"] = integer(v["sequence"])
    v["quantity"] = integer(v["quantity"], positive=True)
    validate_ticker(v["ticker"])
    for key in (
        "price_krw",
        "benchmark_price_krw",
        "notional_krw",
        "fee_krw",
        "tax_krw",
        "slippage_krw",
    ):
        v[key] = money(
            v[key],
            signed=key == "slippage_krw",
            positive=key in {"price_krw", "benchmark_price_krw", "notional_krw"},
        )
    v["fill_at"], v["fill_known_at"] = instant(v["fill_at"]), instant(v["fill_known_at"])
    require(utc(v["fill_at"]) <= utc(v["fill_known_at"]), "fill_time_order")
    require(v["simulation_only"] is True, "simulation_only_required")


def ledger_fields(v: dict[str, Any]) -> None:
    v["sequence"] = integer(v["sequence"])
    for key in ("previous_state_hash", "cause_id"):
        v[key] = sha(v[key])
    if v["cost_charge_id"] is not None:
        v["cost_charge_id"] = sha(v["cost_charge_id"])
    choice(v["cause_kind"], "order fill corporate_action cash_flow")
    for key in ("cash_delta_krw", "reserved_cash_delta_krw"):
        v[key] = money(v[key], signed=True)
    require(type(v["position_changes"]) is list, "position_changes_required")
    changes = []
    for row in v["position_changes"]:
        p = fields(row, "ticker quantity_increase quantity_decrease cost_basis_delta_krw")
        validate_ticker(p["ticker"])
        p["quantity_increase"] = integer(p["quantity_increase"])
        p["quantity_decrease"] = integer(p["quantity_decrease"])
        p["cost_basis_delta_krw"] = money(p["cost_basis_delta_krw"], signed=True)
        changes.append(p)
    tickers = [p["ticker"] for p in changes]
    require(len(tickers) == len(set(tickers)), "duplicate_position_change")
    v["position_changes"] = sorted(changes, key=lambda p: p["ticker"])
    for key in ("effective_at", "available_at", "recorded_at"):
        v[key] = instant(v[key])
    require(utc(v["effective_at"]) <= utc(v["available_at"]), "ledger_time_order")


def checkpoint_fields(v: dict[str, Any]) -> None:
    v["last_sequence"] = integer(v["last_sequence"])
    for key in ("state_hash", "manifest_hash"):
        v[key] = sha(v[key])
    require(v["manifest_hash"] == v["run_id"], "checkpoint_manifest_mismatch")
    choice(v["status"], "running paused failed completed incomplete")
    v["as_of"], v["recorded_at"] = instant(v["as_of"]), instant(v["recorded_at"])
    v["dependency_ids"] = strings(v["dependency_ids"], hashes=True)
    v["missing_reasons"] = strings(v["missing_reasons"])
    require(v["status"] != "completed" or not v["missing_reasons"], "incomplete_checkpoint")


@dataclass(frozen=True)
class PreflightReport:
    """Structural checks do not approve a run or implement an execution gate."""

    ready_for_execution: bool
    blockers: tuple[str, ...]


def validate_event_sequence(events: Sequence[MarketEvent], *, cutoff: str | datetime) -> None:
    boundary = utc(cutoff)
    previous_sequence = -1
    previous_time: datetime | None = None
    run: tuple[str, ...] | None = None
    for event in events:
        v = event.to_dict()
        identity = tuple(
            v[k] for k in ("run_id", "information_mode", "data_origin", "usage_restriction")
        )
        require(run is None or identity == run, "mixed_event_run")
        run = identity
        now = utc(v["available_at"])
        require(v["sequence"] > previous_sequence, "nonincreasing_sequence")
        require(previous_time is None or now >= previous_time, "availability_time_reversed")
        require(now <= boundary, "future_event")
        previous_sequence, previous_time = v["sequence"], now


def validate_run(
    manifest: RunManifest, policy: ExecutionPolicy, records: Sequence[Contract]
) -> PreflightReport:
    m, p = manifest.to_dict(), policy.to_dict()
    require(m["execution_policy_id"] == policy.identifier, "execution_policy_mismatch")
    require(m["execution_mode"] == p["execution_mode"], "execution_mode_mismatch")
    require(
        m["time_basis"]["ordering_policy_id"] == p["ordering_policy"]["content_hash"],
        "ordering_policy_mismatch",
    )
    start, end = utc(m["start_at"]), utc(m["end_at"])
    events: list[MarketEvent] = []
    for record in records:
        v = record.to_dict()
        require(v.get("run_id") == manifest.identifier, "run_id_mismatch")
        for key in ("information_mode", "data_origin", "usage_restriction"):
            require(v[key] == m[key], "mixed_run_provenance")
        time_key = {
            "market_event": "available_at",
            "simulation_account": "received_at",
            "virtual_order": "submitted_at",
            "virtual_fill": "fill_known_at",
            "ledger_event": "available_at",
            "checkpoint": "as_of",
        }.get(record.kind)
        require(time_key is not None, "invalid_run_record")
        observed = utc(v[str(time_key)])
        require(
            start <= observed <= end
            if m["time_basis"]["boundary"] == "inclusive"
            else start < observed < end,
            "outside_run_period",
        )
        if record.kind == "market_event":
            events.append(cast("MarketEvent", record))
            require(
                v["availability_assumption_id"] == m["availability_assumption_id"],
                "assumption_mismatch",
            )
    validate_event_sequence(events, cutoff=end)
    return PreflightReport(
        False,
        ("p1_pending", "p10_pending", "policy_resolution_pending", "execution_not_implemented"),
    )
