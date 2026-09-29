"""Explicit daily research assumptions, never historical liquidity evidence."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.historical import ASSUMPTION, HistoricalBacktestInput
from donghak_stock_vision.backtest.validation import require, utc
from donghak_stock_vision.data.schema import SEOUL

MODE = "historical_daily_next_open_full_fill"
LIMITATIONS = [
    "historical_daily_full_fill_assumption",
    "no_order_book_or_liquidity_verification",
    "no_actual_orders",
    "source_revision_history_unavailable",
    "not_pit_or_oos",
    "model_training_cutoff_unverified",
    "fill_known_only_after_complete_daily_bar_publication",
]


def metadata(tape: FrozenTape) -> dict[str, Any]:
    return (
        {
            "usage_restriction": "research_only",
            "limitations": LIMITATIONS,
            "execution_assumption": MODE,
        }
        if tape.manifest.to_dict()["data_origin"] == "real"
        else {}
    )


def validate_input(tape: FrozenTape, document: FrozenJSON | None) -> HistoricalBacktestInput:
    require(document is not None, "historical_input_evidence_missing")
    assert document is not None
    frozen = HistoricalBacktestInput(document)
    d, m = document.to_dict(), tape.manifest.to_dict()
    require(
        d["kind"] == "historical_input_v1" and d["mode"] == "historical_research",
        "historical_input_required",
    )
    require(
        m["information_mode"] == "historical_research"
        and m["data_origin"] == "real"
        and m["usage_restriction"] == "research_only"
        and m["execution_mode"] == "backtest",
        "historical_research_only",
    )
    require(frozen.tape == tape, "historical_tape_mismatch")
    require(
        d["source_metadata"]["availability_assumption"] == ASSUMPTION,
        "historical_availability_required",
    )
    require(
        sorted(d["source_metadata"]["source_hashes"]) == sorted(s.identifier for s in tape.sources),
        "historical_source_mismatch",
    )
    require(
        d["tickers"] == sorted(set(d["tickers"])) and bool(d["tickers"]),
        "historical_universe_invalid",
    )
    require(
        d["source_metadata"]["quality"] == tape.quality.to_dict(), "historical_quality_mismatch"
    )
    require(
        utc(tape.quality.to_dict()["verified_at"]) <= utc(d["source_metadata"]["captured_at"]),
        "historical_quality_after_capture",
    )
    prior: dict[str, str] = {}
    for event, release in zip(tape.events, tape.release_plan.to_dict()["releases"], strict=True):
        e = event.to_dict()
        day = e["trading_date"]
        expected = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(), SEOUL)
        require(
            d["start"] <= day <= d["end"] and e["ticker"] in d["tickers"],
            "historical_period_mismatch",
        )
        require(day > prior.get(e["ticker"], ""), "historical_session_revision_unsupported")
        prior[e["ticker"]] = day
        require(
            utc(e["source_received_at"]) <= utc(d["source_metadata"]["captured_at"]),
            "historical_receipt_after_capture",
        )
        require(
            utc(e["event_at"]) == utc(e["available_at"]) == utc(expected),
            "historical_publication_mismatch",
        )
        require(
            release["previous_revision"] is None
            and all(utc(t) == utc(expected) for t in release["field_times"].values()),
            "historical_release_mismatch",
        )
        require(
            set(e["public_fields"]) == {"open", "high", "low", "close", "volume", "trading_value"},
            "complete_daily_bar_required",
        )
        source = next(
            s.to_dict() for s in tape.sources if s.identifier == e["source"]["content_hash"]
        )
        require(
            source["kind"] == "latest_daily" and source["data_origin"] == "real",
            "historical_source_required",
        )
    # FrozenAnalysis validates all checksums and the unchanged same-training-snapshot rule.
    for artifact in frozen.analyses:
        bundle = artifact.bundle.to_dict()
        for ticker in d["tickers"]:
            originals = sorted(
                (s.to_dict()["original"] for s in tape.sources if s.to_dict()["ticker"] == ticker),
                key=lambda r: r["trading_date"],
            )
            snapshot_rows = [
                r
                for r in bundle["snapshot"]["rows"].get(ticker, [])
                if d["start"] <= r["trading_date"] <= d["end"]
            ]
            require(originals == snapshot_rows, "historical_snapshot_market_mismatch")
    return frozen


def decision_event(tape: FrozenTape, ticker: str, clock: VirtualClock) -> dict[str, Any]:
    rows = [
        e.to_dict()
        for e in tape.events
        if e.to_dict()["ticker"] == ticker
        and e.to_dict()["sequence"] <= clock.sequence
        and utc(e.to_dict()["available_at"]) <= utc(clock.cutoff)
    ]
    require(bool(rows), "historical_decision_bar_unavailable")
    return rows[-1]


def quality(event: dict[str, Any]) -> None:
    require(
        event["quality_status"] == "verified"
        and not event["quality_flags"]
        and event["adjustment"] == "unadjusted"
        and event["session"] == "closed"
        and event["listing_status"] == "unknown"
        and event["halt_status"] == "unknown",
        "historical_market_quality_blocked",
    )
    require(event["public_fields"]["volume"] > 0, "historical_no_trade_bar")


def next_session(
    tape: FrozenTape,
    ticker: str,
    decision: VirtualClock,
    accepted: VirtualClock,
    event: dict[str, Any],
    clock: VirtualClock,
) -> None:
    prior = decision_event(tape, ticker, decision)
    require(event["trading_date"] > prior["trading_date"], "same_or_past_session")
    # Inspect only already published events, not future OPEN or volume.
    candidates = [
        e.to_dict()
        for e in tape.events
        if e.to_dict()["ticker"] == ticker
        and e.to_dict()["trading_date"] > prior["trading_date"]
        and e.to_dict()["sequence"] > accepted.sequence
        and e.to_dict()["sequence"] <= clock.sequence
        and utc(e.to_dict()["available_at"]) <= utc(clock.cutoff)
    ]
    require(
        bool(candidates) and candidates[0]["sequence"] == event["sequence"],
        "not_next_historical_session",
    )
    require(utc(clock.cutoff) == utc(event["available_at"]), "historical_execution_clock_mismatch")
    require(Decimal(event["public_fields"]["open"]) > 0, "historical_open_unavailable")
    quality(event)


def market_context(
    tape: FrozenTape, document: FrozenJSON, ticker: str, clock: VirtualClock, *, field: str
) -> dict[str, Any]:
    """Virtual order-entry context, NOT an assertion the exchange is currently open."""
    validate_input(tape, document)
    event = decision_event(tape, ticker, clock)
    quality(event)
    require(field in {"open", "high", "low", "close"}, "historical_mark_invalid")
    public = [x for x in tape.view(clock).to_dict()["items"] if x["sequence"] == event["sequence"]]
    require(len(public) == 1 and field in public[0]["public_fields"], "historical_mark_not_public")
    return {
        "origin": "virtual",
        "snapshot_version": "historical_daily_order_intent_v1",
        "observed_at": clock.cutoff,
        "received_at": clock.cutoff,
        "ticker": ticker,
        "currency": "KRW",
        "tradable": True,
        "session": "open",
        "listing_status": "listed",
        "quality_status": "verified",
        "market": event["market"],
        "adjustment": event["adjustment"],
        "reference_price_krw": public[0]["public_fields"][field],
        "price_observed_at": event["available_at"],
    }
