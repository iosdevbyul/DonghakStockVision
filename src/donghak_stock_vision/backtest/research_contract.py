"""Versioned policy declarations only; no strategy, fills or portfolio simulation."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from donghak_stock_vision.backtest.contracts import VirtualFill as Fill
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import (
    choice,
    fields,
    flag,
    integer,
    money,
    reference,
    require,
    sha,
    strings,
    text,
)

__all__ = ["BacktestContract", "CollisionPolicy", "Fill", "OrderSide", "PositionSide"]


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(StrEnum):
    FLAT = "flat"
    LONG = "long"
    SHORT = "short"  # Vocabulary only: the initial cash-equity contract rejects short permission.


class CollisionPolicy(StrEnum):
    PESSIMISTIC = "pessimistic"
    REJECT_AS_AMBIGUOUS = "reject_as_ambiguous"


def day(value: Any) -> date:
    require(type(value) is str, "date_string_required")
    parsed = date.fromisoformat(value)
    require(parsed.isoformat() == value, "canonical_date_required")
    return parsed


def rate(value: Any, *, positive: bool = False) -> str:
    result = money(value, positive=positive)
    require(Decimal(result) < 1, "rate_out_of_range")
    return result


def rounding(value: Any) -> dict[str, Any]:
    r = fields(value, "quantum mode")
    r["quantum"] = money(r["quantum"], positive=True)
    choice(r["mode"], "half_up down up adverse")
    return r


def rule(value: Any) -> dict[str, Any]:
    r = fields(value, "mode specification")
    choice(r["mode"], "disabled external_rule")
    if r["mode"] == "disabled":
        require(r["specification"] is None, "disabled_rule_has_specification")
    else:
        r["specification"] = reference(r["specification"])
    return r


def normalize_policy(value: dict[str, Any]) -> dict[str, Any]:
    p = fields(
        value,
        "contract_version scope start end execution entry exit costs "
        "slippage sizing liquidity quality",
    )
    require(p["contract_version"] == "research_backtest_v1", "unsupported_contract_version")
    require(p["scope"] == "historical_research", "research_only")
    start, end = day(p["start"]), day(p["end"])
    require(start <= end, "invalid_period")
    e = fields(p["execution"], "version timing availability future_access fill_type")
    text(e["version"])
    choice(e["timing"], "next_observed_session_open")
    choice(e["availability"], "complete_daily_bar_next_seoul_midnight")
    choice(e["future_access"], "simulator_only")
    choice(e["fill_type"], "reuse_virtual_fill_v1")
    p["execution"] = e
    entry = fields(p["entry"], "version reference_price attempt_policy invalid_next_session")
    text(entry["version"])
    choice(entry["reference_price"], "open")
    choice(entry["attempt_policy"], "immediate_next_observed_only")
    choice(entry["invalid_next_session"], "unfilled_no_retry")
    p["entry"] = entry
    exit_policy = fields(
        p["exit"],
        "version maximum_holding_sessions target_rule stop_rule time_exit_rule "
        "reference_price collision holding_count_basis",
    )
    text(exit_policy["version"])
    exit_policy["maximum_holding_sessions"] = integer(
        exit_policy["maximum_holding_sessions"], positive=True
    )
    choice(exit_policy["holding_count_basis"], "entry_session_is_one sessions_after_entry")
    for name in ("target_rule", "stop_rule"):
        exit_policy[name] = rule(exit_policy[name])
    # Require an explicit referenced time-exit rule; do not implement one here.
    exit_policy["time_exit_rule"] = reference(exit_policy["time_exit_rule"])
    choice(exit_policy["reference_price"], "open close explicit_rule_price")
    choice(exit_policy["collision"], "pessimistic reject_as_ambiguous")
    p["exit"] = exit_policy
    cost_names = "version basis schedule rounding"
    # Optional explicit semantics for new policies; legacy payloads keep their identity.
    require(type(p["costs"]) is dict, "cost_policy_required")
    if "date_basis" in p["costs"] or "tax_rounding" in p["costs"]:
        cost_names += " date_basis tax_rounding"
    costs = fields(p["costs"], cost_names)
    if "date_basis" in costs:
        choice(costs["date_basis"], "settlement_date")
        costs["tax_rounding"] = rounding(costs["tax_rounding"])
        require(
            costs["tax_rounding"]["mode"] in {"half_up", "down", "up"}, "unsupported_tax_rounding"
        )
    text(costs["version"])
    choice(costs["basis"], "executed_notional")
    costs["rounding"] = rounding(costs["rounding"])
    require(costs["rounding"]["mode"] in {"half_up", "down", "up"}, "unsupported_cost_rounding")
    require(type(costs["schedule"]) is list and bool(costs["schedule"]), "cost_schedule_required")
    periods = []
    for item in costs["schedule"]:
        row = fields(
            item,
            "start end buy_fee_rate sell_fee_rate sell_tax_rate sell_market_levy_rate minimum_fee",
        )
        require(day(row["start"]) <= day(row["end"]), "invalid_cost_period")
        for name in ("buy_fee_rate", "sell_fee_rate", "sell_tax_rate", "sell_market_levy_rate"):
            row[name] = rate(row[name])
        minimum = fields(row["minimum_fee"], "mode amount_krw")
        choice(minimum["mode"], "disabled per_fill")
        if minimum["mode"] == "disabled":
            require(minimum["amount_krw"] is None, "disabled_minimum_fee_has_amount")
        else:
            minimum["amount_krw"] = money(minimum["amount_krw"], positive=True)
        row["minimum_fee"] = minimum
        periods.append(row)
    periods.sort(key=lambda item: item["start"])
    for previous, current in zip(periods, periods[1:], strict=False):
        require(day(previous["end"]) < day(current["start"]), "overlapping_cost_periods")
    cursor = start
    for row in periods:
        first, last = day(row["start"]), day(row["end"])
        if last < cursor:
            continue
        require(first <= cursor, "cost_schedule_gap")
        if last >= end:
            break
        cursor = last + timedelta(days=1)
    else:
        require(False, "cost_schedule_incomplete")
    costs["schedule"] = periods
    p["costs"] = costs
    slippage = fields(p["slippage"], "version method rate rounding")
    text(slippage["version"])
    choice(slippage["method"], "adverse_proportional")
    slippage["rate"] = rate(slippage["rate"])
    slippage["rounding"] = rounding(slippage["rounding"])
    require(slippage["rounding"]["mode"] == "adverse", "adverse_price_rounding_required")
    p["slippage"] = slippage
    sizing = fields(
        p["sizing"],
        "version initial_capital_krw policy maximum_concurrent_positions cash_availability "
        "leverage short_permission fractional_shares",
    )
    text(sizing["version"])
    sizing["initial_capital_krw"] = money(sizing["initial_capital_krw"], positive=True)
    sizing["policy"] = reference(sizing["policy"])
    sizing["maximum_concurrent_positions"] = integer(
        sizing["maximum_concurrent_positions"], positive=True
    )
    choice(sizing["cash_availability"], "available_after_reservations")
    sizing["leverage"] = money(sizing["leverage"], positive=True)
    require(Decimal(sizing["leverage"]) == 1, "leverage_unsupported")
    require(flag(sizing["short_permission"]) is False, "short_unsupported")
    require(flag(sizing["fractional_shares"]) is False, "fractional_shares_unsupported")
    p["sizing"] = sizing
    liquidity = fields(
        p["liquidity"],
        "version minimum_volume minimum_trading_value_krw maximum_participation_rate "
        "rejection assumption",
    )
    text(liquidity["version"])
    liquidity["minimum_volume"] = integer(liquidity["minimum_volume"], positive=True)
    # None explicitly disables an optional floor; no-trade rejection remains mandatory.
    if liquidity["minimum_trading_value_krw"] is not None:
        liquidity["minimum_trading_value_krw"] = money(
            liquidity["minimum_trading_value_krw"], positive=True
        )
    liquidity["maximum_participation_rate"] = money(
        liquidity["maximum_participation_rate"], positive=True
    )
    require(Decimal(liquidity["maximum_participation_rate"]) <= 1, "invalid_participation_rate")
    choice(liquidity["rejection"], "unfilled_no_retry")
    choice(liquidity["assumption"], "daily_bar_does_not_prove_fill")
    p["liquidity"] = liquidity
    quality = fields(
        p["quality"], "version allowed_research_flags segment_validation corporate_actions"
    )
    text(quality["version"])
    quality["allowed_research_flags"] = strings(quality["allowed_research_flags"])
    choice(quality["segment_validation"], "existing_check_segment_required")
    choice(quality["corporate_actions"], "reject_unusable_segment_no_adjustment")
    require(
        not set(quality["allowed_research_flags"])
        & {
            "suspected_discontinuity",
            "no_trade_bar",
            "suspected_gap",
            "excluded_corporate_action",
            "known_incomplete_trading_date",
            "invalid_market_order_or_duplicates",
        },
        "quality_failure_cannot_be_waived",
    )
    p["quality"] = quality
    return p


@dataclass(frozen=True)
class BacktestContract:
    """A complete declaration, not execution approval or an implemented strategy."""

    payload: FrozenJSON

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "payload", FrozenJSON.freeze(normalize_policy(self.payload.to_dict()))
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BacktestContract":
        return cls(FrozenJSON.freeze(normalize_policy(value)))

    def to_dict(self) -> dict[str, Any]:
        return self.payload.to_dict()

    @property
    def fingerprint(self) -> str:
        return self.payload.identifier

    def component_fingerprint(self, name: str) -> str:
        require(
            name
            in {
                "execution",
                "entry",
                "exit",
                "costs",
                "slippage",
                "sizing",
                "liquidity",
                "quality",
            },
            "unknown_policy_component",
        )
        return FrozenJSON.freeze(self.to_dict()[name]).identifier


@dataclass(frozen=True)
class ResultProvenance:
    """Future result lineage, not a fabricated backtest result."""

    dataset: str
    model_up: str
    model_down: str
    signal_policy: str
    backtest_contract: str
    execution_policy: str
    cost_policy: str
    result: str

    def __post_init__(self) -> None:
        for value in vars(self).values():
            sha(value)

    def validate_contract(self, contract: BacktestContract) -> None:
        require(self.backtest_contract == contract.fingerprint, "contract_provenance_mismatch")
        require(
            self.execution_policy == contract.component_fingerprint("execution"),
            "execution_provenance_mismatch",
        )
        require(
            self.cost_policy == contract.component_fingerprint("costs"), "cost_provenance_mismatch"
        )
