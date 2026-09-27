"""Explicit research parameters. Missing values are never trading defaults."""

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.strategy.contracts import RiskExitPolicy


class Blocked(ValueError):
    pass


def need(obj: dict[str, Any], key: str) -> Any:
    if key not in obj or obj[key] is None:
        raise Blocked(f"missing_value:{key}")
    return obj[key]


def number(value: Any, name: str, *, positive: bool = False) -> Decimal:
    if type(value) not in {str, int}:
        raise Blocked(f"invalid_decimal:{name}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise Blocked(f"invalid_decimal:{name}") from error
    if not result.is_finite() or result < 0 or (positive and not result):
        raise Blocked(f"invalid_decimal:{name}")
    # Bounded decimal arithmetic is a schema/resource constraint, not a risk limit.
    exponent = result.as_tuple().exponent
    if not isinstance(exponent, int) or len(result.as_tuple().digits) > 24 or abs(exponent) > 12:
        raise Blocked(f"decimal_precision_exceeded:{name}")
    return result


def integer(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise Blocked(f"invalid_integer:{name}")
    return value


def ratio(value: Any, name: str) -> Decimal:
    result = number(value, name)
    if result > 1:
        raise Blocked(f"invalid_ratio:{name}")
    return result


def when(value: Any) -> datetime:
    try:
        return timestamp(value)
    except (TypeError, AttributeError, ValueError) as error:
        raise Blocked("invalid_timestamp") from error


def inspect_risk_exit_policy(value: dict[str, Any]) -> dict[str, Any]:
    for name in ("stop_loss", "take_profit", "max_holding_period"):
        entry = need(value, name)
        if need(entry, "mode") != "disabled":
            raise Blocked("unsupported_risk_exit_policy")
        if "parameters" not in entry or entry["parameters"] is not None:
            raise Blocked("invalid_disabled_risk_policy")
    return RiskExitPolicy("disabled", "disabled", "disabled").diagnostics()


def validate_policy(p: dict[str, Any]) -> None:
    if need(p, "schema_version") != 1 or need(p, "scope") != "research":
        raise Blocked("invalid_policy_scope_or_version")
    if not isinstance(need(p, "version"), str) or not p["version"]:
        raise Blocked("invalid_policy_version")
    if need(p, "currency") != "KRW":
        raise Blocked("unsupported_currency")
    for key in ("allowed_models", "allowed_quality_flags"):
        if not isinstance(need(p, key), list) or any(not isinstance(v, str) for v in p[key]):
            raise Blocked("invalid_policy_list")
    if need(p, "session_basis") not in {"verified_sessions", "observed_bars_unverified"}:
        raise Blocked("invalid_session_basis")
    for side in ("buy", "sell"):
        ratio(need(need(p, "thresholds"), side), f"threshold_{side}")
    for key in ("account", "orders", "market", "analysis", "price", "max_skew"):
        integer(need(need(p, "ttl_seconds"), key), key)
    if need(p, "ttl_boundary") != "inclusive":
        raise Blocked("unsupported_ttl_boundary")
    if when(need(p, "effective_from")) > when(need(p, "effective_until")):
        raise Blocked("invalid_policy_period")
    sizing = need(p, "sizing")
    number(need(sizing, "buy_budget_krw"), "budget", positive=True)
    if need(sizing, "quantity_rounding") != "floor":
        raise Blocked("unsupported_quantity_rounding")
    if need(sizing, "sell_mode") not in {"all", "partial"}:
        raise Blocked("invalid_sell_mode")
    if need(sizing, "sell_basis") not in {"held", "available"}:
        raise Blocked("invalid_sell_basis")
    if sizing["sell_mode"] == "partial":
        if need(sizing, "partial_kind") == "quantity":
            valid = integer(need(sizing, "partial_value"), "partial_quantity") > 0
        elif sizing["partial_kind"] == "fraction":
            valid = ratio(need(sizing, "partial_value"), "partial_fraction") > 0
        else:
            valid = False
        if not valid:
            raise Blocked("invalid_partial_policy")
    limits = need(p, "limits")
    number(need(limits, "max_order_notional_krw"), "order_limit", positive=True)
    for key in ("max_position_weight", "max_gross_weight"):
        ratio(need(limits, key), key)
    integer(need(limits, "max_positions"), "max_positions")
    if need(limits, "exposure_rule") != "post_action_strict":
        raise Blocked("unsupported_exposure_rule")
    c = need(p, "costs")
    for key in ("source", "version"):
        if not isinstance(need(c, key), str) or not c[key].strip():
            raise Blocked("invalid_cost_metadata")
    if need(c, "market") not in {"KOSPI", "KOSDAQ", "KONEX"}:
        raise Blocked("invalid_cost_market")
    if need(c, "rounding") not in {"ceiling", "half_up"}:
        raise Blocked("unsupported_cost_rounding")
    number(need(c, "quantum_krw"), "quantum", positive=True)
    if when(need(c, "effective_from")) > when(need(c, "effective_until")):
        raise Blocked("invalid_cost_period")
    for side in ("buy", "sell"):
        rules = need(c, side)
        for key in ("fee_rate", "tax_rate", "slippage_rate"):
            ratio(need(rules, key), key)
        if number(rules["slippage_rate"], "slippage_rate") >= 1:
            raise Blocked("invalid_slippage")
        number(need(rules, "fixed_fee_krw"), "fixed_fee")
    r = need(p, "reentry")
    if need(r, "unit") not in {"seconds", "verified_sessions"}:
        raise Blocked("unsupported_cooldown_unit")
    integer(need(r, "duration"), "cooldown")
    if need(r, "boundary") != "inclusive" or need(r, "require_new_analysis") is not True:
        raise Blocked("invalid_reentry_policy")
    if type(need(r, "allow_first_entry")) is not bool:
        raise Blocked("invalid_first_entry_policy")
    inspect_risk_exit_policy(need(p, "risk_exit"))


def costs(p: dict[str, Any], side: str, qty: int, price: Decimal) -> dict[str, Decimal]:
    c = p["costs"]
    rules = c[side]
    slip = number(rules["slippage_rate"], "slippage_rate")
    execution = price * (1 + slip if side == "buy" else 1 - slip)
    notional = execution * qty
    rounding = ROUND_CEILING if c["rounding"] == "ceiling" else ROUND_HALF_UP
    quantum = number(c["quantum_krw"], "quantum", positive=True)
    fee = notional * number(rules["fee_rate"], "fee_rate") + number(
        rules["fixed_fee_krw"], "fixed_fee"
    )
    tax = notional * number(rules["tax_rate"], "tax_rate")
    fee = (fee / quantum).to_integral_value(rounding=rounding) * quantum
    tax = (tax / quantum).to_integral_value(rounding=rounding) * quantum
    return {
        "notional": notional,
        "fee": fee,
        "tax": tax,
        "slippage": abs(execution - price) * qty,
        "cash": notional + fee + tax if side == "buy" else notional - fee - tax,
    }
