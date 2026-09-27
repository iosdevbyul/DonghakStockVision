"""Pure decision table. No I/O, clock, learning, execution, or account mutation."""

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.data.learning import (
    FEATURE_VERSION,
    LABEL_VERSION,
    digest,
    event_contract,
)
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy, DecisionResult
from donghak_stock_vision.strategy.policy import (
    Blocked,
    costs,
    inspect_risk_exit_policy,
    integer,
    need,
    number,
    validate_policy,
    when,
)


def check_clock(obj: dict[str, Any], cutoff: datetime, ttl: int) -> list[datetime]:
    times = [when(need(obj, key)) for key in ("observed_at", "received_at")]
    if times[0] > times[1] or any(t > cutoff for t in times):
        raise Blocked("future_input")
    if any((cutoff - t).total_seconds() > ttl for t in times):
        raise Blocked("stale_input")
    return times


def analysis_context(a: dict[str, Any], p: dict[str, Any], request: dict[str, Any]) -> str | None:
    cutoff = when(request["decision_as_of"])
    if a["ticker"] != request["ticker"]:
        raise Blocked("ticker_mismatch")
    if a["model_version"] not in p["allowed_models"]:
        raise Blocked("model_policy_mismatch")
    if (
        a["feature_version"] != FEATURE_VERSION
        or a["label_version"] != LABEL_VERSION
        or a["session_basis"] != p["session_basis"]
    ):
        raise Blocked("analysis_contract_mismatch")
    if a["mode"] not in {"historical_research", "point_in_time"}:
        raise Blocked("analysis_mode_mismatch")
    if any(a.get(key) != value for key, value in event_contract(a["session_basis"]).items()):
        raise Blocked("analysis_contract_mismatch")
    restriction = a["usage_restriction"]
    if a["data_origin"] == "synthetic":
        if restriction != "synthetic_test_only":
            raise Blocked("origin_mismatch")
    elif a["data_origin"] != "real" or restriction != (
        "research_only" if a["mode"] == "historical_research" else "pit_review_required"
    ):
        raise Blocked("origin_mismatch")
    if a["input_status"] != "ok":
        raise Blocked(f"analysis_unavailable:{a['input_status']}")
    if not set(a["quality_flags"]) <= set(p["allowed_quality_flags"]):
        raise Blocked("quality_unaccepted")
    anchor = when(a["anchor_at"])
    if (
        anchor > cutoff
        or date.fromisoformat(a["last_trading_date"]) > anchor.astimezone(SEOUL).date()
    ):
        raise Blocked("future_input")
    if (cutoff - anchor).total_seconds() > p["ttl_seconds"]["analysis"]:
        raise Blocked("stale_analysis")
    if a["mode"] == "point_in_time":
        if (
            date.fromisoformat(a["last_trading_date"]) >= anchor.astimezone(SEOUL).date()
            or when(a["model_created_at"]) > anchor
            or when(a["latest_collected_at"]) > anchor
            or when(a["data_as_of"]) != anchor
        ):
            raise Blocked("future_input")
        if (cutoff.astimezone(SEOUL).date() - date.fromisoformat(a["last_trading_date"])).days > 7:
            raise Blocked("stale_analysis")
    contexts = {"prior_decline": "up", "prior_rise": "down", "flat": None}
    if a["context"] not in contexts:
        raise Blocked("invalid_context")
    direction = contexts[a["context"]]
    for side in ("up", "down"):
        value, status = a[f"{side}_score"], a[f"{side}_status"]
        if side == direction:
            if status != "scored" or value is None:
                raise Blocked("applicable_score_unavailable")
            if (
                type(value) not in {int, float}
                or not Decimal(str(value)).is_finite()
                or not 0 <= value <= 1
            ):
                raise Blocked("invalid_score")
        elif value is not None or status != "context_mismatch":
            raise Blocked("contract_conflict")
    return direction


def reentry(
    orders: dict[str, Any],
    market: dict[str, Any],
    p: dict[str, Any],
    cutoff: datetime,
    analysis_id: str,
) -> None:
    if need(orders, "history_complete") is not True or "last_exit" not in orders:
        raise Blocked("exit_history_unknown")
    last = orders["last_exit"]
    rules = p["reentry"]
    if last is None:
        if not rules["allow_first_entry"]:
            raise Blocked("first_entry_forbidden")
        return
    if need(last, "status") != "completed":
        raise Blocked("exit_history_unknown")
    end, available = when(need(last, "completed_at")), when(need(last, "received_at"))
    if end > available or available > cutoff or available > when(orders["received_at"]):
        raise Blocked("future_input")
    if need(last, "analysis_id") == analysis_id:
        raise Blocked("new_analysis_required")
    if rules["unit"] == "seconds":
        elapsed = (cutoff - end).total_seconds()
    else:
        if need(market, "calendar_verified") is not True:
            raise Blocked("calendar_unverified")
        sessions = need(market, "sessions")
        if sessions != sorted(set(sessions)):
            raise Blocked("invalid_sessions")
        days = [date.fromisoformat(d) for d in sessions]
        if any(d.weekday() > 4 for d in days):
            raise Blocked("invalid_sessions")
        start_day, end_day = end.astimezone(SEOUL).date(), cutoff.astimezone(SEOUL).date()
        coverage = need(market, "calendar_coverage")
        if (
            date.fromisoformat(coverage["start"]) > start_day
            or date.fromisoformat(coverage["end"]) < end_day
        ):
            raise Blocked("calendar_unverified")
        elapsed = sum(start_day < d <= end_day for d in days)
    if elapsed < rules["duration"]:
        raise Blocked("cooldown_active")


def _research(b: dict[str, Any], p: dict[str, Any], result: dict[str, Any]) -> None:
    checks = result["checks"]
    checks["policy"] = "checking"
    validate_policy(p)
    result["diagnostics"]["risk_exit_rules"] = inspect_risk_exit_policy(p["risk_exit"])
    request = b["request"]
    cutoff = when(request["decision_as_of"])
    for period in (p, p["costs"]):
        if not when(period["effective_from"]) <= cutoff <= when(period["effective_until"]):
            raise Blocked("policy_not_effective")
    checks["policy"] = "passed"
    checks["analysis"] = "checking"
    if b.get("assembly_error"):
        raise Blocked(b["assembly_error"])
    a = need(b, "analysis")
    direction = analysis_context(a, p, request)
    checks["analysis"] = "passed"
    checks["snapshots"] = "checking"
    clocks = [when(a["anchor_at"])]
    ttl = p["ttl_seconds"]
    for key in ("account", "orders", "market"):
        obj = need(b, key)
        if need(obj, "origin") != "virtual" or not need(obj, "snapshot_version"):
            raise Blocked("virtual_snapshot_required")
        clocks += check_clock(obj, cutoff, ttl[key])
    if (max(clocks) - min(clocks)).total_seconds() > ttl["max_skew"]:
        raise Blocked("incoherent_snapshot_times")
    account, orders, market = b["account"], b["orders"], b["market"]
    if need(account, "complete") is not True:
        raise Blocked("account_incomplete")
    if need(orders, "complete") is not True:
        raise Blocked("orders_unknown")
    if not need(account, "account_ref") or account["account_ref"] != need(orders, "account_ref"):
        raise Blocked("account_mismatch")
    if account["ticker"] != request["ticker"] or market["ticker"] != request["ticker"]:
        raise Blocked("ticker_mismatch")
    if need(account, "currency") != "KRW" or need(market, "currency") != "KRW":
        raise Blocked("currency_mismatch")
    quantity = integer(need(account, "quantity"), "quantity")
    sellable = integer(need(account, "sellable_quantity"), "sellable_quantity")
    if sellable > quantity:
        raise Blocked("invalid_sellable_quantity")
    result["position_state"] = "long" if quantity else "flat"
    if account.get("average_price_krw") is not None:
        number(account["average_price_krw"], "average_price", positive=quantity > 0)
    if quantity and a["context"] == "prior_decline":
        result["diagnostics"].update(
            exit_signal_status="no_applicable_exit_signal",
            exit_model_status="context_mismatch",
            diagnostic_codes=["exit_signal_absent_in_decline_context", "risk_exit_rules_disabled"],
        )
    elif quantity:
        result["diagnostics"].update(
            exit_signal_status="scored" if direction == "down" else "no_context",
            exit_model_status=a["down_status"],
        )
    reserved = Decimal(0)
    for order in need(orders, "items"):
        state = need(order, "state")
        remaining = integer(need(order, "remaining_quantity"), "remaining_quantity")
        order_reserved = number(need(order, "reserved_cash_krw"), "reserved_cash")
        reserved += order_reserved
        order_time = when(need(order, "received_at"))
        if order_time > cutoff or order_time > when(orders["received_at"]):
            raise Blocked("future_input")
        terminal = state in {"filled", "cancelled", "rejected"}
        if not terminal and state not in {
            "open",
            "partial",
            "cancel_pending",
            "unknown",
            "submitted",
        }:
            raise Blocked("orders_unknown")
        if terminal and (remaining or order_reserved):
            raise Blocked("invalid_terminal_order")
        if need(order, "ticker") == request["ticker"] and not terminal:
            raise Blocked("pending_order")
        if state == "unknown":
            raise Blocked("orders_unknown")
    if need(account, "cash_basis") != "net_of_reservations":
        raise Blocked("cash_basis_unknown")
    cash = number(need(account, "available_cash_krw"), "cash")
    if number(need(account, "reserved_cash_krw"), "reserved_cash") != reserved:
        raise Blocked("reservation_mismatch")
    if (
        need(market, "tradable") is not True
        or need(market, "session") != "open"
        or need(market, "listing_status") != "listed"
    ):
        raise Blocked("trading_unavailable")
    if (
        need(market, "quality_status") != "verified"
        or need(market, "market") != p["costs"]["market"]
    ):
        raise Blocked("market_quality_unavailable")
    if need(market, "adjustment") != need(b, "price_basis"):
        raise Blocked("price_basis_mismatch")
    price = number(need(market, "reference_price_krw"), "reference_price", positive=True)
    price_time = when(need(market, "price_observed_at"))
    if price_time > when(market["received_at"]) or price_time > cutoff:
        raise Blocked("future_input")
    if (cutoff - price_time).total_seconds() > ttl["price"]:
        raise Blocked("stale_price")
    equity = number(need(account, "equity_krw"), "equity", positive=True)
    gross = number(need(account, "gross_exposure_krw"), "gross")
    exposure = number(need(account, "position_exposure_krw"), "position_exposure")
    positions = integer(need(account, "positions_count"), "positions_count")
    if (
        exposure != price * quantity
        or gross < exposure
        or gross > equity
        or (quantity and not positions)
        or (positions == 0 and gross != 0)
        or cash + reserved + gross != equity
    ):
        raise Blocked("incoherent_portfolio_valuation")
    checks["snapshots"] = "passed"
    checks["sizing_risk_cost"] = "checking"
    action, qty, estimate = ("HOLD" if quantity else "WAIT"), 0, None
    if (
        not quantity
        and direction == "up"
        and Decimal(str(a["up_score"])) >= number(p["thresholds"]["buy"], "buy")
    ):
        reentry(orders, market, p, cutoff, request["analysis_id"])
        budget = number(p["sizing"]["buy_budget_krw"], "budget", positive=True)
        low, high = 0, min(int(budget / price), 2**63 - 1)
        while low < high:
            mid = (low + high + 1) // 2
            if costs(p, "buy", mid, price)["cash"] <= budget:
                low = mid
            else:
                high = mid - 1
        qty = low
        if not qty:
            raise Blocked("budget_below_one_share")
        estimate = costs(p, "buy", qty, price)
        if estimate["cash"] > cash:
            raise Blocked("insufficient_cash")
        action = "BUY"
        gross += qty * price
        exposure += qty * price
        positions += 1
    elif (
        quantity
        and direction == "down"
        and Decimal(str(a["down_score"])) >= number(p["thresholds"]["sell"], "sell")
    ):
        sizing = p["sizing"]
        basis = quantity if sizing["sell_basis"] == "held" else sellable
        qty = basis
        if sizing["sell_mode"] == "partial":
            qty = (
                sizing["partial_value"]
                if sizing["partial_kind"] == "quantity"
                else int(basis * number(sizing["partial_value"], "partial_fraction"))
            )
        if qty <= 0 or qty > sellable or qty > basis:
            raise Blocked("insufficient_sellable_quantity")
        estimate = costs(p, "sell", qty, price)
        if estimate["cash"] <= 0:
            raise Blocked("cost_exceeds_proceeds")
        action = "SELL"
        gross -= qty * price
        exposure -= qty * price
        positions -= int(qty == quantity)
    limits = p["limits"]
    if estimate:
        equity -= estimate["fee"] + estimate["tax"] + estimate["slippage"]
        if estimate["notional"] > number(limits["max_order_notional_krw"], "order_limit"):
            raise Blocked("order_notional_limit")
    if (
        equity <= 0
        or exposure > equity * number(limits["max_position_weight"], "position_limit")
        or gross > equity * number(limits["max_gross_weight"], "gross_limit")
        or positions > limits["max_positions"]
    ):
        raise Blocked("portfolio_limit")
    checks["sizing_risk_cost"] = "passed"
    reason = {
        "BUY": "entry_condition",
        "SELL": "exit_condition",
        "HOLD": "no_exit_condition",
        "WAIT": "no_entry_condition",
    }[action]
    result.update(
        action=action,
        status="virtual",
        decision_eligible=True,
        reason_codes=[reason],
        blocking_reasons=[],
        selected_rule=reason,
        proposed_quantity=qty or None,
        estimated_cost={k: str(v) for k, v in estimate.items()} if estimate else None,
    )


def decide(inputs: DecisionInput, policy: DecisionPolicy) -> DecisionResult:
    b, p = inputs.to_dict(), policy.to_dict()
    request = b["request"]
    raw_analysis = b.get("analysis")
    a: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    result: dict[str, Any] = {
        "schema_version": 1,
        "engine_version": "research_decision_v1",
        "action": "WAIT",
        "status": "blocked",
        "decision_scope": request["scope"],
        "ticker": request["ticker"],
        "decision_as_of": when(request["decision_as_of"]).isoformat(),
        "input_bundle_id": inputs.identifier,
        "policy_id": policy.identifier,
        "policy_version": p.get("version"),
        "analysis_id": request.get("analysis_id"),
        "model_version": a.get("model_version"),
        "snapshot_id": a.get("snapshot_id"),
        "mode": a.get("mode"),
        "data_origin": a.get("data_origin"),
        "usage_restriction": "synthetic_test_only"
        if a.get("usage_restriction") == "synthetic_test_only"
        else "research_only",
        "quality_flags": a.get("quality_flags", []),
        "decision_eligible": False,
        "operational_eligible": False,
        "executable": False,
        "execution_blockers": ["execution_not_implemented"],
        "position_state": "unknown",
        "proposed_quantity": None,
        "estimated_cost": None,
        "selected_rule": None,
        "checks": {},
        "evidence": {
            "analysis": a,
            "provenance": b.get("provenance"),
            "virtual_snapshot_ids": {k: digest(b.get(k)) for k in ("account", "orders", "market")},
        },
        "diagnostics": {
            "exit_signal_status": "not_evaluated",
            "exit_model_status": None,
            "risk_exit_rules": {},
            "diagnostic_codes": [],
            "safety_assurance": False,
        },
    }
    try:
        if request["scope"] == "operational":
            raise Blocked("operational_unavailable")
        # Fresh explicit Context avoids dependence on caller decimal traps/rounding/precision.
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
            _research(b, p, result)
    except Blocked as error:
        result.update(reason_codes=[str(error)], blocking_reasons=[str(error)])
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        result.update(
            reason_codes=["invalid_input_or_policy"], blocking_reasons=["invalid_input_or_policy"]
        )
    for key, status in result["checks"].items():
        if status == "checking":
            result["checks"][key] = "failed"
    return DecisionResult.from_dict(result)
