"""Bounded five-security historical research, one account, explicit immutable inputs."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, cast

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.decision_bridge import policy_values as admission_values
from donghak_stock_vision.backtest.execution import settings
from donghak_stock_vision.backtest.hgb_provider import HGBHistoricalProvider
from donghak_stock_vision.backtest.historical import (
    ASSUMPTION,
    HistoricalBacktestInput,
    build_historical_input,
    decision_schedule,
)
from donghak_stock_vision.backtest.historical_execution import (
    MODE,
    decision_event,
    market_context,
    validate_input,
)
from donghak_stock_vision.backtest.ledger import LedgerCommand, LedgerPolicy, checkpoint, initialize
from donghak_stock_vision.backtest.performance import policy_values
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.validation import fields, instant, money, require, sha, utc
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.schema import SEOUL, validate_range, validate_ticker
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.storage.analysis import AnalysisStore, ResearchRunStore
from donghak_stock_vision.storage.base import MarketDataStore
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from donghak_stock_vision.strategy.policy import validate_policy

REQUEST = "five_ticker_historical_request_v1"
MAX_DATES = 10
MAX_DAYS = 366


@dataclass(frozen=True)
class FiveTickerRequest:
    document: FrozenJSON

    def __post_init__(self) -> None:
        d = fields(
            self.document.to_dict(),
            "version tickers start end initial_cash model_family model_id model_references "
            "market calendar evidence_scope config",
        )
        require(d["version"] == REQUEST, "unsupported_five_ticker_request")
        require(
            isinstance(d["tickers"], list) and len(d["tickers"]) == len(set(d["tickers"])) == 5,
            "exactly_five_unique_tickers_required",
        )
        for ticker in d["tickers"]:
            validate_ticker(ticker)
        d["tickers"] = sorted(d["tickers"])
        start, end = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
        validate_range(start, end)
        require(
            d["start"] == start.isoformat() and d["end"] == end.isoformat(),
            "canonical_seoul_dates_required",
        )
        require((end - start).days < MAX_DAYS, "portfolio_range_limit")
        d["initial_cash"] = money(d["initial_cash"], positive=True)
        require(d["market"] == "KRX_KOSPI", "unsupported_portfolio_market")
        require(d["model_family"] in {"linear", "hgb"}, "unsupported_portfolio_model")
        sha(d["model_id"])
        refs = d["model_references"]
        if d["model_family"] == "linear":
            fields(refs, "model_id training_snapshot_id dataset_id")
            require(refs["model_id"] == d["model_id"], "model_reference_mismatch")
            for value in refs.values():
                sha(value)
        else:
            fields(refs, "up down")
            for row in refs.values():
                fields(row, "fingerprint checkpoint_sha256")
                for value in row.values():
                    sha(value)
        calendar = fields(d["calendar"], "session_basis research_calendar")
        require(
            calendar["session_basis"] in {"verified_sessions", "observed_bars_unverified"},
            "unsupported_session_basis",
        )
        if calendar["research_calendar"] is not None:
            ResearchCalendarPolicy.from_dict(calendar["research_calendar"])
        require(
            d["evidence_scope"] in {"synthetic_fixture", "retrospective_historical_research"},
            "explicit_evidence_scope_required",
        )
        c = fields(
            d["config"],
            "manifest policy captured_at quality availability_assumption decision_policy "
            "execution_policy ledger_policy valuation_policy admission_policy reservation_by_side "
            "decision_dates decision_mark_field release_steps",
        )
        require(
            type(c["decision_dates"]) is list and 1 <= len(c["decision_dates"]) <= MAX_DATES,
            "bounded_portfolio_schedule_required",
        )
        decision_schedule(c["decision_dates"], start, end, self.calendar)
        m = RunManifest.from_dict(c["manifest"]).to_dict()
        require(
            m["data_origin"] in {"real", "synthetic"}
            and m["information_mode"] == "historical_research"
            and m["execution_mode"] == "backtest",
            "historical_research_only",
        )
        require(
            m["initial_account"]["reserved_cash_krw"] == "0"
            and all(
                p["ticker"] in d["tickers"]
                and p["reserved_quantity"] == 0
                and p["quantity"] == p["sellable_quantity"]
                for p in m["initial_account"]["positions"]
            ),
            "invalid_initial_portfolio",
        )
        require(
            m["data_origin"] != "synthetic" or d["evidence_scope"] == "synthetic_fixture",
            "synthetic_scope_required",
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
        require(c["availability_assumption"] == ASSUMPTION, "explicit_daily_availability_required")
        require(utc(c["captured_at"]).astimezone(SEOUL).date() > end, "capture_before_period_end")
        ExecutionPolicy.from_dict(c["policy"])
        LedgerPolicy(FrozenJSON.freeze(c["ledger_policy"]))
        validate_policy(c["decision_policy"])
        ap = admission_values(c["admission_policy"])
        require(sorted(ap["ordered_tickers"]) == d["tickers"], "universe_order_policy_mismatch")
        require(c["decision_mark_field"] in {"open", "high", "low", "close"}, "invalid_mark_field")
        for side, row in fields(c["reservation_by_side"], "BUY SELL").items():
            for key, value in fields(row, "max_notional_krw max_cost_krw").items():
                money(value, positive=side == "BUY" and key == "max_notional_krw")
            require(
                side == "BUY" or all(money(v) == "0" for v in row.values()), "sell_cash_reservation"
            )
        require(
            isinstance(c["release_steps"], list) and len(c["release_steps"]) <= 50,
            "bounded_release_steps_required",
        )
        for row in c["release_steps"]:
            fields(row, "trading_date ticker status")
            require(
                date.fromisoformat(row["trading_date"]).isoformat() == row["trading_date"],
                "invalid_release_date",
            )
            require(
                row["ticker"] in d["tickers"]
                and d["start"] <= row["trading_date"] <= d["end"]
                and row["status"] in {"cancelled", "expired", "rejected"},
                "invalid_release_step",
            )
        c["release_steps"] = sorted(
            c["release_steps"], key=lambda row: (row["trading_date"], row["ticker"], row["status"])
        )
        require(
            len({(r["trading_date"], r["ticker"]) for r in c["release_steps"]})
            == len(c["release_steps"]),
            "duplicate_release_step",
        )
        object.__setattr__(self, "document", FrozenJSON.freeze(d))

    @property
    def calendar(self) -> ResearchCalendarPolicy | None:
        value = self.document.to_dict()["calendar"]["research_calendar"]
        return ResearchCalendarPolicy.from_dict(value) if value is not None else None


def readonly(store: Any) -> None:
    if hasattr(store, "connection"):
        require(
            store.connection.execute("PRAGMA query_only").fetchone()[0] == 1,
            "readonly_source_required",
        )


def run_five_ticker(
    request: FiveTickerRequest,
    market: MarketDataStore,
    analysis: AnalysisStore | None,
    *,
    run_output: AnalysisStore,
    hgb_provider: HGBHistoricalProvider | None = None,
) -> FrozenJSON:
    """Acquire bounded source inputs once; replay afterward consumes only frozen JSON."""
    from donghak_stock_vision.backtest.portfolio_report import replay_five_ticker

    d = request.document.to_dict()
    c, refs = d["config"], d["model_references"]
    readonly(market)
    if hasattr(market, "connection"):
        ResearchRunStore(cast(AnalysisStore, market), run_output)
    if analysis is not None:
        readonly(analysis)
        ResearchRunStore(analysis, run_output)
    if d["model_family"] == "linear":
        require(analysis is not None and hgb_provider is None, "linear_source_required")
        assert analysis is not None
        model = load_model(analysis, d["model_id"], "historical_research")
        require(
            model["snapshot_id"] == refs["training_snapshot_id"]
            and model["dataset_id"] == refs["dataset_id"],
            "model_reference_mismatch",
        )
        require(
            analysis.get("snapshot", model["snapshot_id"])["session_basis"]
            == d["calendar"]["session_basis"],
            "session_basis_mismatch",
        )
    else:
        require(hgb_provider is not None and analysis is None, "explicit_hgb_provider_required")
        assert hgb_provider is not None
        require(d["model_id"] == hgb_provider.model_id, "hgb_model_policy_mismatch")
        for name, verified in zip(("up", "down"), hgb_provider.resolver.verify(), strict=True):
            a = verified.artifact
            require(
                refs[name]
                == {"fingerprint": a.fingerprint, "checkpoint_sha256": a.checkpoint_sha256},
                "hgb_checkpoint_reference_mismatch",
            )
            require(
                json.loads(verified.provenance_json)["lock"]["session_basis"]
                == d["calendar"]["session_basis"],
                "session_basis_mismatch",
            )
    m = RunManifest.from_dict(c["manifest"]).to_dict()
    start_at = datetime.combine(date.fromisoformat(d["start"]), time(), SEOUL).isoformat()
    m["initial_account"].update(
        available_cash_krw=d["initial_cash"], observed_at=start_at, received_at=start_at
    )
    frozen = build_historical_input(
        market,
        start=date.fromisoformat(d["start"]),
        end=date.fromisoformat(d["end"]),
        tickers=d["tickers"],
        captured_at=c["captured_at"],
        quality=FrozenJSON.freeze(c["quality"]),
        manifest_template=RunManifest.from_dict(m),
        policy=ExecutionPolicy.from_dict(c["policy"]),
        availability_assumption=c["availability_assumption"],
        analysis_store=analysis,
        model_id=d["model_id"],
        research_calendar=request.calendar,
        decision_dates=c["decision_dates"],
        run_output=run_output,
        hgb_provider=hgb_provider,
        shared_evidence=True,
        five_ticker=True,
    )
    runner, unavailable = assemble_portfolio(request, frozen)
    replay = {
        "version": "five_ticker_frozen_run_v1",
        "request": d,
        "historical_input": frozen.document.to_dict(),
        "unavailable": unavailable,
        "initial_checkpoint": checkpoint(runner.initial).to_dict(),
        "decision_policy": runner.decision_policy.to_dict(),
        "execution_policy": runner.execution_policy.to_dict(),
        "config": runner.config.to_dict(),
        "valuation_policy": c["valuation_policy"],
    }
    return replay_five_ticker(FrozenJSON.freeze(replay))


def assemble_portfolio(
    request: FiveTickerRequest, frozen: HistoricalBacktestInput
) -> tuple[BacktestRunner, list[dict[str, Any]]]:
    d = request.document.to_dict()
    c = d["config"]
    start_at = datetime.combine(date.fromisoformat(d["start"]), time(), SEOUL).isoformat()
    tape = frozen.tape
    validate_input(tape, frozen.document)
    require(
        all(e.to_dict()["market"] == "KOSPI" for e in tape.events), "unsupported_portfolio_market"
    )
    source = frozen.document.to_dict()
    require(
        source["start"] == d["start"]
        and source["end"] == d["end"]
        and source["tickers"] == d["tickers"]
        and source["requested_model_id"] == d["model_id"],
        "portfolio_source_request_mismatch",
    )
    require(
        source["source_metadata"]["captured_at"] == instant(c["captured_at"]),
        "portfolio_capture_mismatch",
    )
    expected_quality = dict(c["quality"])
    if request.calendar is not None:
        expected_quality["research_calendar"] = request.calendar.to_dict()
    require(tape.quality.to_dict() == expected_quality, "portfolio_quality_binding_mismatch")
    require(
        tape.policy
        == ExecutionPolicy.from_dict(
            {**c["policy"], "availability_policy": tape.policy.to_dict()["availability_policy"]}
        ),
        "portfolio_policy_binding_mismatch",
    )
    require(
        tape.manifest.to_dict()["initial_account"]
        == {
            **RunManifest.from_dict(c["manifest"]).to_dict()["initial_account"],
            "available_cash_krw": d["initial_cash"],
            "observed_at": instant(start_at),
            "received_at": instant(start_at),
        },
        "portfolio_initial_account_mismatch",
    )
    source_events = {
        (e.to_dict()["trading_date"], e.to_dict()["ticker"]): e.to_dict() for e in tape.events
    }
    present = {e.to_dict()["ticker"] for e in tape.events}
    require(
        source["missing_tickers"] == sorted(set(d["tickers"]) - present),
        "portfolio_missing_ticker_mismatch",
    )
    expected_points = {(day, t) for day in c["decision_dates"] for t in present}
    require(
        {(p["trading_date"], p["ticker"]) for p in source["decision_points"]} == expected_points
        and len(expected_points) == len(source["decision_points"]),
        "portfolio_opportunity_coverage_mismatch",
    )
    artifacts = {a.analysis_id: a for a in frozen.analyses}
    linked = set()
    for point in source["decision_points"]:
        event = source_events.get((point["trading_date"], point["ticker"]))
        require(
            point["sequence"] == (event["sequence"] if event else None),
            "portfolio_point_sequence_mismatch",
        )
        if event is not None:
            require(
                point["available_at"] == event["available_at"], "portfolio_point_clock_mismatch"
            )
        if point["analysis_id"] is None:
            require(point["status"] == "blocked", "portfolio_available_analysis_missing")
            continue
        require(point["analysis_id"] in artifacts, "portfolio_analysis_reference_missing")
        linked.add(point["analysis_id"])
        b = artifacts[point["analysis_id"]].bundle.to_dict()
        require(
            b["signal"]["ticker"] == point["ticker"]
            and b["signal"]["last_trading_date"] == point["trading_date"]
            and b["model_id"] == d["model_id"],
            "portfolio_signal_binding_mismatch",
        )
        require(
            b["snapshot"]["session_basis"] == d["calendar"]["session_basis"],
            "session_basis_mismatch",
        )
        if d["model_family"] == "linear":
            require(
                b.get("contract") is None
                and b["model"]["snapshot_id"] == d["model_references"]["training_snapshot_id"]
                and b["model"]["dataset_id"] == d["model_references"]["dataset_id"],
                "model_reference_mismatch",
            )
        else:
            require(
                b.get("contract") == "hgb_historical_analysis_v1", "portfolio_model_family_mismatch"
            )
            for name in ("up", "down"):
                require(
                    b["signal"]["hgb_provenance"]["model_pair"][name]
                    == d["model_references"][name]["fingerprint"]
                    and b["hgb_evidence"]["models"][name]["checkpoint_sha256"]
                    == d["model_references"][name]["checkpoint_sha256"],
                    "hgb_checkpoint_reference_mismatch",
                )
    require(linked == set(artifacts), "portfolio_unlinked_analysis")
    steps, unavailable = [], []
    for point in source["decision_points"]:
        if point["status"] != "available":
            unavailable.append(point)
            continue
        group = [
            e.to_dict() for e in tape.events if e.to_dict()["available_at"] == point["available_at"]
        ]
        sequence = max(e["sequence"] for e in group)
        clock = VirtualClock.at(point["available_at"], sequence)
        try:
            context = market_context(
                tape, frozen.document, point["ticker"], clock, field=c["decision_mark_field"]
            )
        except ValueError as error:
            unavailable.append({**point, "status": "blocked", "reason": str(error)})
            continue
        ap = dict(c["admission_policy"])
        ap["marks"] = {
            t: {
                "sequence": decision_event(tape, t, clock)["sequence"],
                "field": c["decision_mark_field"],
            }
            for t in d["tickers"]
            if any(
                e["ticker"] == t and e["sequence"] <= sequence
                for e in (x.to_dict() for x in tape.events)
            )
        }
        steps.append(
            {
                "sequence": sequence,
                "ticker": point["ticker"],
                "analysis_id": point["analysis_id"],
                "market": context,
                "admission_policy": ap,
                "reservation": c["reservation_by_side"],
            }
        )
    unavailable += [
        {
            "ticker": ticker,
            "trading_date": day,
            "sequence": None,
            "available_at": None,
            "analysis_id": None,
            "status": "blocked",
            "reason": "missing_historical_data",
        }
        for ticker in source["missing_tickers"]
        for day in c["decision_dates"]
    ]
    releases = []
    for row in c["release_steps"]:
        events = [
            e.to_dict() for e in tape.events if e.to_dict()["trading_date"] == row["trading_date"]
        ]
        require(bool(events), "release_event_missing")
        releases.append(
            {
                "sequence": min(e["sequence"] for e in events),
                "ticker": row["ticker"],
                "status": row["status"],
            }
        )
    initial = initialize(
        tape.manifest,
        tape.identifier,
        LedgerPolicy(FrozenJSON.freeze(c["ledger_policy"])),
        LedgerCommand(
            FrozenJSON.freeze(
                {
                    "event_id": "five-ticker-initialize-" + tape.manifest.identifier,
                    "kind": "initialize",
                    "sequence": 0,
                    "order_id": None,
                    "effective_at": start_at,
                    "known_at": start_at,
                    "source_id": frozen.identifier,
                    "data": {},
                }
            )
        ),
        cutoff=start_at,
    )
    config = FrozenJSON.freeze(
        {
            "run_id": tape.manifest.identifier,
            "start_at": tape.manifest.to_dict()["start_at"],
            "end_at": tape.manifest.to_dict()["end_at"],
            "tickers": c["admission_policy"]["ordered_tickers"],
            "event_clock": "available_at",
            "history_mode": "verified_execution",
            "account_mode": "five_ticker_shared_v1",
            "release_steps": releases,
            "valuation_policy": c["valuation_policy"],
            "decision_steps": steps,
        }
    )
    runner = BacktestRunner(
        tape,
        frozen.analyses,
        initial,
        DecisionPolicy.from_dict(c["decision_policy"]),
        FrozenJSON.freeze(c["execution_policy"]),
        config,
        frozen.document,
    )
    return runner, unavailable


def canonical_frozen(document: FrozenJSON) -> FrozenJSON:
    """Normalize only an unordered artifact definition table; never drop definitions."""
    d = document.to_dict()
    if d.get("version") == "five_ticker_frozen_run_v1":
        table = d["historical_input"]["evidence_table"]
        table["artifacts"].sort(key=lambda artifact: artifact["artifact_id"])
    normalized = FrozenJSON.freeze(d)
    return document if normalized == document else normalized


def frozen_runner(document: FrozenJSON) -> BacktestRunner:
    document = canonical_frozen(document)
    d = fields(
        document.to_dict(),
        "version request historical_input unavailable initial_checkpoint decision_policy "
        "execution_policy config valuation_policy",
    )
    require(d["version"] == "five_ticker_frozen_run_v1", "unsupported_portfolio_frozen_run")
    request = FiveTickerRequest(FrozenJSON.freeze(d["request"]))
    evidence = HistoricalBacktestInput(FrozenJSON.freeze(d["historical_input"]))
    tape = evidence.tape
    validate_input(tape, evidence.document)
    require(
        evidence.document.to_dict().get("portfolio_contract") == "five_ticker_historical_input_v1",
        "portfolio_input_contract_required",
    )
    require(
        evidence.tape.manifest.to_dict()["data_origin"]
        == request.document.to_dict()["config"]["manifest"]["data_origin"],
        "portfolio_origin_mismatch",
    )
    expected, unavailable = assemble_portfolio(request, evidence)
    require(d["request"] == request.document.to_dict(), "portfolio_request_not_canonical")
    c = request.document.to_dict()["config"]
    for key, expected_value in {
        "initial_checkpoint": checkpoint(expected.initial).to_dict(),
        "decision_policy": expected.decision_policy.to_dict(),
        "execution_policy": expected.execution_policy.to_dict(),
        "config": expected.config.to_dict(),
        "valuation_policy": c["valuation_policy"],
        "unavailable": unavailable,
    }.items():
        require(d[key] == expected_value, "portfolio_frozen_binding_mismatch")
    return expected
