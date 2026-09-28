"""Stable text formatting of immutable research performance."""

from decimal import Context, Decimal, localcontext

from donghak_stock_vision.backtest.data import FrozenJSON


def percent(value: str | None) -> str:
    if value is None:
        return "unavailable"
    with localcontext(Context(prec=80)):
        text = format(Decimal(value) * 100, "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text) + "%"


def format_report(performance: FrozenJSON) -> str:
    p = performance.to_dict()

    def amount(key: str) -> str:
        return str(p[key]) if p[key] is not None else "unavailable"

    rows = [
        "SYNTHETIC HISTORICAL RESEARCH SIMULATION — NOT PIT/OOS",
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
    return "\n".join(rows) + "\n"
