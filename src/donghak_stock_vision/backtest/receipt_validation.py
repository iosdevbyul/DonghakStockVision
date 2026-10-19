"""Authoritative legacy reconstruction adapters; never replace existing validators."""

from datetime import date
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical import HistoricalBacktestInput, decision_schedule
from donghak_stock_vision.backtest.historical_service import (
    HistoricalBacktestRequest,
    run_frozen_historical,
)
from donghak_stock_vision.backtest.portfolio_report import replay_five_ticker
from donghak_stock_vision.backtest.report import format_report
from donghak_stock_vision.backtest.service import run_backtest_with_report
from donghak_stock_vision.backtest.trading_report import ReportedBacktest
from donghak_stock_vision.backtest.validation import instant, require, utc
from donghak_stock_vision.data.research_calendar import calendar_policy

BUNDLE = "reported_backtest_archive_source_v1"


def reported_result(frozen: FrozenJSON, reported: ReportedBacktest) -> FrozenJSON:
    """New opt-in archive envelope; the original ReportedBacktest is unchanged."""
    return FrozenJSON.freeze(
        {
            "version": BUNDLE,
            "frozen_input": frozen.to_dict(),
            "run": reported.run.to_dict(),
            "run_hash": reported.run.identifier,
            "performance": reported.performance.to_dict(),
            "performance_hash": reported.performance.identifier,
            "detailed_report": reported.details.to_dict(),
            "detailed_report_hash": reported.details.identifier,
            "report": format_report(reported.performance, reported.details),
        }
    )


def validate_result(document: FrozenJSON) -> None:
    """Full exact replay, including rejected/pending intents and factual projections."""
    d = document.to_dict()
    version = d.get("version")
    if version == "five_ticker_historical_result_v1":
        expected = replay_five_ticker(FrozenJSON.freeze(d["frozen_input"]))
    elif version == BUNDLE:
        frozen = FrozenJSON.freeze(d["frozen_input"])
        f = frozen.to_dict()
        result = run_backtest_with_report(
            frozen,
            start=f["config"]["start_at"],
            end=f["config"]["end_at"],
            initial_cash=f["initial_checkpoint"]["state"]["total_cash_krw"],
            tickers=f["config"]["tickers"],
        )
        expected = reported_result(frozen, result)
    else:
        require(type(version) is int and version == 1, "unsupported_archive_result")
        require(d.get("mode") == "historical_research", "unsupported_archive_result")
        r = d["request"]
        request = HistoricalBacktestRequest(
            date.fromisoformat(r["start"]),
            date.fromisoformat(r["end"]),
            r["initial_cash"],
            tuple(r["tickers"]),
            r["model_id"],
            FrozenJSON.freeze(r["config"]),
        )
        source = d["historical_input"]
        c = request.config.to_dict()
        require(
            all(source[k] == r[k] for k in ("start", "end", "tickers"))
            and source["requested_model_id"] == request.model_id
            and utc(source["source_metadata"]["captured_at"]) == utc(c["captured_at"]),
            "archive_historical_request_mismatch",
        )
        frozen_input = HistoricalBacktestInput(FrozenJSON.freeze(source))
        # Bind original request policies/provenance, not merely the derived run.
        tape = frozen_input.tape
        m = tape.manifest.to_dict()
        quality = dict(c["quality"])
        calendar = calendar_policy(c)
        if calendar is not None:
            quality["research_calendar"] = calendar.to_dict()
        require(tape.quality.to_dict() == quality, "archive_historical_quality_mismatch")
        require(
            source["source_metadata"]["availability_assumption"] == c["availability_assumption"],
            "archive_historical_availability_mismatch",
        )
        template = dict(c["manifest"])
        for key in (
            "start_at",
            "end_at",
            "execution_policy_id",
            "availability_assumption_id",
            "market_snapshots",
            "analyses",
            "models",
            "quality_manifest",
            "universe",
        ):
            template[key] = m[key]
        template["initial_account"] = {
            **template["initial_account"],
            "available_cash_krw": request.initial_cash,
            "observed_at": instant(m["start_at"]),
            "received_at": instant(m["start_at"]),
        }
        require(template == m, "archive_historical_manifest_mismatch")
        schedule = decision_schedule(c["decision_dates"], request.start, request.end, calendar)
        events = [e.to_dict() for e in tape.events]
        present = {e["ticker"] for e in events}
        required_points = (
            {(day, ticker) for day in schedule for ticker in present}
            if schedule is not None
            else {(e["trading_date"], e["ticker"]) for e in events}
        )
        require(
            {(p["trading_date"], p["ticker"]) for p in source["decision_points"]}
            == required_points,
            "archive_historical_schedule_mismatch",
        )
        require(
            m["initial_account"]["available_cash_krw"] == request.initial_cash,
            "archive_historical_cash_mismatch",
        )
        original_policy: dict[str, Any] = dict(c["policy"])
        actual_policy = tape.policy.to_dict()
        original_policy["availability_policy"] = actual_policy["availability_policy"]
        require(original_policy == actual_policy, "archive_historical_policy_mismatch")
        expected = run_frozen_historical(
            request, frozen_input, shared_evidence="historical_input_transport_hash" in d
        ).document
    require(expected == document, "archive_result_replay_conflict")
