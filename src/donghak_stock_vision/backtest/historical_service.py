"""Explicit historical orchestration. Decisions, fills and accounting remain delegated."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.decision_bridge import policy_values as admission_values
from donghak_stock_vision.backtest.execution import settings
from donghak_stock_vision.backtest.historical import build_historical_input
from donghak_stock_vision.backtest.historical_execution import MODE, market_context, validate_input
from donghak_stock_vision.backtest.ledger import LedgerCommand, LedgerPolicy, initialize
from donghak_stock_vision.backtest.performance import calculate_performance, policy_values
from donghak_stock_vision.backtest.report import format_report
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.validation import fields, money, require, text
from donghak_stock_vision.data.schema import SEOUL, validate_range, validate_ticker
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.base import MarketDataStore
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from donghak_stock_vision.strategy.policy import validate_policy


@dataclass(frozen=True)
class HistoricalBacktestRequest:
    start: date
    end: date
    initial_cash: str
    tickers: tuple[str, ...]
    model_id: str
    config: FrozenJSON

    def __post_init__(self) -> None:
        require(type(self.start) is date and type(self.end) is date, "seoul_dates_required")
        validate_range(self.start, self.end)
        object.__setattr__(self, "initial_cash", money(self.initial_cash, positive=True))
        universe = tuple(sorted(set(self.tickers)))
        require(bool(universe), "empty_universe")
        for ticker in universe:
            validate_ticker(ticker)
        require(len(universe) == 1, "concurrent_multi_ticker_execution_unsupported")
        object.__setattr__(self, "tickers", universe)
        text(self.model_id)
        c = fields(
            self.config.to_dict(),
            "manifest policy captured_at quality availability_assumption "
            "decision_policy execution_policy ledger_policy valuation_policy "
            "admission_policy reservation_by_side decision_dates decision_mark_field",
        )
        m = RunManifest.from_dict(c["manifest"]).to_dict()
        require(
            m["information_mode"] == "historical_research"
            and m["data_origin"] == "real"
            and m["execution_mode"] == "backtest",
            "historical_research_only",
        )
        require(
            settings(FrozenJSON.freeze(c["execution_policy"]))["liquidity"] == MODE,
            "historical_execution_required",
        )
        require(
            policy_values(FrozenJSON.freeze(c["valuation_policy"])).get("historical_marks")
            == "verified_daily_research",
            "historical_valuation_assumption_required",
        )
        require(
            m["initial_account"]["reserved_cash_krw"] == "0", "initial_reservations_unsupported"
        )
        for position in m["initial_account"]["positions"]:
            require(position["ticker"] in universe, "initial_position_outside_universe")
            require(
                position["reserved_quantity"] == 0
                and position["sellable_quantity"] == position["quantity"],
                "initial_locked_or_unsettled_position",
            )
        validate_policy(c["decision_policy"])
        LedgerPolicy(FrozenJSON.freeze(c["ledger_policy"]))
        ExecutionPolicy.from_dict(c["policy"])
        ap = admission_values(c["admission_policy"])
        require(ap["ordered_tickers"] == list(universe), "universe_order_policy_mismatch")
        require(c["decision_mark_field"] in {"open", "high", "low", "close"}, "invalid_mark_field")
        reservations = fields(c["reservation_by_side"], "BUY SELL")
        for side, values in reservations.items():
            values = fields(values, "max_notional_krw max_cost_krw")
            for key, amount in values.items():
                money(amount, positive=side == "BUY" and key == "max_notional_krw")
            if side == "SELL":
                require(all(money(x) == "0" for x in values.values()), "sell_cash_reservation")
        dates = c["decision_dates"]
        require(dates == "all_available" or isinstance(dates, list), "decision_schedule_required")
        if isinstance(dates, list):
            require(dates == sorted(set(dates)), "decision_dates_not_canonical")
            for day in dates:
                require(
                    self.start <= date.fromisoformat(day) <= self.end, "decision_outside_period"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "initial_cash": self.initial_cash,
            "tickers": list(self.tickers),
            "model_id": self.model_id,
            "config": self.config.to_dict(),
        }


@dataclass(frozen=True)
class HistoricalBacktestResult:
    document: FrozenJSON

    @property
    def identifier(self) -> str:
        return self.document.identifier

    @property
    def report(self) -> str:
        return str(self.document.to_dict()["report"])


def run_historical_backtest(
    request: HistoricalBacktestRequest, market: MarketDataStore, analysis: AnalysisStore
) -> HistoricalBacktestResult:
    """Freeze once with public SignalService.research through the existing builder.

    Phase 2 may persist signal artifacts/audit to the supplied analysis store. The
    market store is read only. All subsequent operations consume frozen values.
    """
    c = request.config.to_dict()
    m = c["manifest"]
    start_at = datetime.combine(request.start, time(), SEOUL).isoformat()
    m["initial_account"].update(
        available_cash_krw=request.initial_cash, observed_at=start_at, received_at=start_at
    )
    frozen = build_historical_input(
        market,
        start=request.start,
        end=request.end,
        tickers=list(request.tickers),
        captured_at=c["captured_at"],
        quality=FrozenJSON.freeze(c["quality"]),
        manifest_template=RunManifest.from_dict(m),
        policy=ExecutionPolicy.from_dict(c["policy"]),
        availability_assumption=c["availability_assumption"],
        analysis_store=analysis,
        model_id=request.model_id,
    )
    tape = frozen.tape
    validate_input(tape, frozen.document)
    source = frozen.document.to_dict()
    unavailable = [p for p in source["decision_points"] if p["status"] != "available"]
    unavailable += [
        {"ticker": t, "reason": "missing_historical_data"} for t in source["missing_tickers"]
    ]
    steps = []
    for point in source["decision_points"]:
        if point["status"] != "available":
            continue
        if (
            c["decision_dates"] != "all_available"
            and point["trading_date"] not in c["decision_dates"]
        ):
            continue
        seq, ticker = point["sequence"], point["ticker"]
        clock = VirtualClock.at(point["available_at"], seq)
        try:
            market_input = market_context(
                tape, frozen.document, ticker, clock, field=c["decision_mark_field"]
            )
        except ValueError as exc:
            unavailable.append({**point, "status": "blocked", "reason": str(exc)})
            continue
        ap = dict(c["admission_policy"])
        ap["marks"] = {ticker: {"sequence": seq, "field": c["decision_mark_field"]}}
        steps.append(
            {
                "sequence": seq,
                "ticker": ticker,
                "analysis_id": point["analysis_id"],
                "market": market_input,
                "admission_policy": ap,
                "reservation": c["reservation_by_side"],
            }
        )
    command = LedgerCommand(
        FrozenJSON.freeze(
            {
                "event_id": "historical-initialize-" + tape.manifest.identifier,
                "kind": "initialize",
                "sequence": 0,
                "order_id": None,
                "effective_at": start_at,
                "known_at": start_at,
                "source_id": frozen.identifier,
                "data": {},
            }
        )
    )
    initial = initialize(
        tape.manifest,
        tape.identifier,
        LedgerPolicy(FrozenJSON.freeze(c["ledger_policy"])),
        command,
        cutoff=start_at,
    )
    config = FrozenJSON.freeze(
        {
            "run_id": tape.manifest.identifier,
            "start_at": tape.manifest.to_dict()["start_at"],
            "end_at": tape.manifest.to_dict()["end_at"],
            "tickers": list(request.tickers),
            "event_clock": "available_at",
            "history_mode": "verified_execution",
            "decision_steps": steps,
        }
    )
    run = BacktestRunner(
        tape,
        frozen.analyses,
        initial,
        DecisionPolicy.from_dict(c["decision_policy"]),
        FrozenJSON.freeze(c["execution_policy"]),
        config,
        frozen.document,
    ).run()
    performance = calculate_performance(
        run, tape, FrozenJSON.freeze(c["valuation_policy"]), historical_input=frozen.document
    )
    limitations = sorted(
        set(source["limitations"] + performance.to_dict()["limitations"])
        - {"real_runner_unsupported"}
    )
    r = run.to_dict()
    blocked = [
        {"sequence": e["sequence"], "stage": stage, "result": item}
        for e in r["events"]
        for stage in ("blocked", "candidates", "admissions", "executions")
        for item in e.get(stage, [])
        if stage == "blocked" or item.get("status") in {"blocked", "rejected"}
    ]
    blocked += [
        {"sequence": e["sequence"], "stage": "event", "reason": e["reason"]}
        for e in r["events"]
        if e["status"] == "failed"
    ]
    status = "completed_with_unavailable_inputs" if unavailable else r["status"]
    report = (
        "Mode: historical_research\n"
        "PIT verified: false; OOS verified: false\n"
        "Revision history available: false; Model training cutoff verified: false\n"
        "Full-fill assumption: true; Liquidity verified: false\n"
        f"Execution policy: {MODE}\nTickers: "
        + ", ".join(request.tickers)
        + f"\nStatus: {status}\n"
        + format_report(performance)
        + "Input limitations:\n"
        + "".join(f"  {x}\n" for x in limitations)
        + "Unavailable analysis/market inputs:\n"
        + "".join(f"  {FrozenJSON.freeze(p).payload_json}\n" for p in unavailable)
        + "Blocked/rejected outcomes:\n"
        + "".join(f"  {FrozenJSON.freeze(p).payload_json}\n" for p in blocked)
    )
    return HistoricalBacktestResult(
        FrozenJSON.freeze(
            {
                "version": 1,
                "mode": "historical_research",
                "status": status,
                "request": request.to_dict(),
                "historical_input": source,
                "historical_input_hash": frozen.identifier,
                "run": r,
                "run_hash": run.identifier,
                "performance": performance.to_dict(),
                "performance_hash": performance.identifier,
                "report": report,
                "unavailable": unavailable,
                "blocked": blocked,
                "limitations": limitations,
                "pit_verified": False,
                "oos_verified": False,
                "model_training_cutoff_verified": False,
                "revision_history_available": False,
                "liquidity_verified": False,
                "full_fill_assumption": True,
                "execution_policy": MODE,
                "operational_eligible": False,
                "executable": False,
            }
        )
    )
