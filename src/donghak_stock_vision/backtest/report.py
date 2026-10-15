"""Stable text formatting of immutable research performance."""

from decimal import Context, Decimal, localcontext

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require


def percent(value: str | None) -> str:
    if value is None:
        return "unavailable"
    with localcontext(Context(prec=80)):
        text = format(Decimal(value) * 100, "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text) + "%"


def format_report(performance: FrozenJSON, details: FrozenJSON | None = None) -> str:
    p = performance.to_dict()

    def amount(key: str) -> str:
        return str(p[key]) if p[key] is not None else "unavailable"

    rows = [
        (
            "HISTORICAL RESEARCH SIMULATION — NOT PIT/OOS"
            if p["scope"] == "historical_research"
            else "SYNTHETIC HISTORICAL RESEARCH SIMULATION — NOT PIT/OOS"
        ),
        f"Backtest period: {p['start_at']} .. {p['end_at']}",
        f"Initial equity: {amount('initial_equity')} KRW",
        f"Final equity: {amount('final_equity')} KRW",
        f"Total return: {percent(p['total_return'])}",
        f"Realized P&L (completed episodes, net): {p['realized_pnl']} KRW",
        f"Ledger realized P&L (including partial exits): {p['ledger_realized_pnl']} KRW",
        f"Unrealized P&L: {amount('unrealized_pnl')} KRW",
        f"Maximum drawdown: {percent(p['mdd']['ratio'])}",
        f"Observed MDD lower bound: {percent(p['mdd']['observed_lower_bound'])}",
        f"MDD peak/trough: {p['mdd']['peak']} / {p['mdd']['trough']}",
        f"Completed trades: {p['completed_trades']}",
        f"Wins / Losses / Breakeven: {p['wins']} / {p['losses']} / {p['breakeven']}",
        f"Win rate: {percent(p['win_rate'])}",
        f"Fees: {p['costs']['fees']} KRW",
        f"Taxes (SELL): {p['costs']['sell_taxes']} KRW",
        f"Slippage diagnostic (already in prices): {p['costs']['slippage_impact']} KRW",
        f"Total explicit trading costs: {p['costs']['total_explicit_trading_costs']} KRW",
        f"Final cash (including reserved cash): {p['final_cash']} KRW",
        f"Open positions: {p['valuation_coverage']['open_positions']}",
        f"Unvalued positions: {p['valuation_coverage']['unvalued_positions']}",
        f"Valuation coverage: {p['valuation_coverage']}",
        "Final positions:",
    ]
    for pos in p["final_positions"]:
        rows.append(
            f"  {pos['ticker']} quantity={pos['quantity']} "
            f"market_value={pos['market_value']} basis={pos['cost_basis_krw']} "
            f"status={pos['status']} reason={pos['reason']}"
        )
    rows.append("Completed trade episodes:")
    for trade in p["trade_episodes"]:
        rows.append(
            f"  {trade['ticker']} {trade['entry_at']} -> {trade['exit_at']} "
            f"basis={trade['economic_basis']} proceeds={trade['net_exit_proceeds']} "
            f"pnl={trade['realized_pnl']} return={percent(trade['return_ratio'])} "
            f"{trade['outcome']} position={trade['position_id']}"
        )
    rows += ["Limitations:"] + [f"  {x}" for x in p["limitations"]]
    rows += [f"Run hash: {p['run_hash']}", f"Performance hash: {performance.identifier}"]
    if details is not None:
        require(
            details.to_dict()["sources"]["performance_hash"] == performance.identifier,
            "report_performance_mismatch",
        )
        rows.extend(detailed_lines(details))
    return "\n".join(rows) + "\n"


def detailed_lines(details: FrozenJSON) -> list[str]:
    d = details.to_dict()
    rows = [
        "Detailed decision timeline (" + d["schema_version"] + "):",
        (
            "Scores are conditional, uncalibrated scores. "
            "Feature contributions are not economic causation."
        ),
    ]
    for item in d["timeline"]:
        rows.append(
            f"  {item['ticker']} opportunity={item['opportunity_cutoff']} "
            f"decision={item['decision_cutoff']} action={item['action']} "
            f"context={item['context']} score={item['score']} thresholds={item['thresholds']}"
        )
        rows.append(
            f"    analysis={item['analysis_id']} model={item['model_id']} "
            f"snapshot={item['snapshot_id']}"
        )
        rows.extend("    " + reason["text"] for reason in item["reasons"])
        if item["conditions"]:
            rows.extend(
                f"    {side}: {reason['text']}" for side, reason in item["conditions"].items()
            )
        rows.append(
            f"    requested_quantity={item['requested_quantity']} admission={item['admission']} "
            f"reservation={item['reservation']} order={item['order_id']}"
        )
        for attempt in item["executions"]:
            rows.append(
                f"    execution attempt={attempt['attempt_at']} status={attempt['status']} "
                f"reason={attempt['reason']}"
            )
            if attempt["fill"]:
                f = attempt["fill"]
                rows.append(
                    f"      fill={f['fill_id']} at={f['fill_at']} known_at={f['fill_known_at']} "
                    f"quantity={f['quantity']} price={f['price_krw']} fee={f['fee_krw']} "
                    f"tax={f['tax_krw']} slippage={f['slippage_krw']}"
                )
        for contribution in item["feature_contributions"] or []:
            rows.append(
                f"    feature={contribution['feature']} value={contribution['value']} "
                f"standardized={contribution['standardized']} "
                f"contribution={contribution['contribution']}"
            )
        if item["unavailable"]:
            rows.append(f"    Unavailable: {item['unavailable']}")
    rows.append("Detailed position episodes:")
    for pos in d["positions"]:
        rows.append(
            f"  position={pos['position_id']} ticker={pos['ticker']} status={pos['status']} "
            f"quantity={pos['quantity']} previous_exit={pos['previous_completed_position_id']}"
        )
        rows.append(
            f"    acquired_at={pos['acquired_at']} exited_at={pos['exited_at']} "
            f"duration={pos['holding_duration']} "
            f"verified_sessions={pos['verified_trading_sessions']}"
        )
        rows.append(
            f"    realized_net_pnl={pos['realized_net_pnl']} "
            f"unrealized_pnl={pos['unrealized_pnl']} "
            f"valuation_unavailable={pos['unavailable']}"
        )
        rows.append(f"    acquisition={pos['acquisition']} sales={pos['sales']}")
    rows.append("Terminal orders:")
    for order in d["terminal_orders"]:
        rows.append(
            f"  {order['order_id']} {order['side']} {order['status']} "
            f"remaining={order['remaining_quantity']} reason={order['outcome_reason']}"
        )
    rows.append("Unaccepted order requests (no accepted ledger order):")
    rows.extend("  " + str(item) for item in d["unaccepted_order_requests"])
    rows.append("Account observations (cash includes reservations):")
    for point in d["account_timeline"]:
        rows.append(
            f"  {point['timestamp']} stage={point['stage']} cash={point['cash']} "
            f"equity={point['total_equity']} positions={point['positions']}"
        )
    rows.append("Detailed report hash: " + details.identifier)
    return rows
