"""Capture once through the Phase 1 boundary; all research then reads this copy."""

from datetime import date, datetime
from typing import Any

from donghak_stock_vision.data.learning import AnalysisError, Mode, timestamp
from donghak_stock_vision.data.research_calendar import (
    ResearchCalendarPolicy,
    calendar_policy,
    excluded_dates,
)
from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_range, validate_ticker
from donghak_stock_vision.storage.base import MarketDataStore


def validate_quality(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        if value["schema_version"] != 1 or not value["source"].strip():
            raise ValueError("schema/source")
        timestamp(value["verified_at"])
        sessions = [date.fromisoformat(d) for d in value["sessions"]]
        if (
            not sessions
            or sessions != sorted(set(sessions))
            or any(d.weekday() > 4 for d in sessions)
        ):
            raise ValueError("sessions")
        if not isinstance(value["coverage"], dict):
            raise ValueError("coverage")
        for ticker, entry in value["coverage"].items():
            validate_ticker(ticker)
            validate_range(date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"]))
            if entry["adjustment"] not in {"unadjusted", "adjusted"}:
                raise ValueError("adjustment")
            if "adjustment_method" in entry and (
                not isinstance(entry["adjustment_method"], str)
                or not entry["adjustment_method"].strip()
            ):
                raise ValueError("adjustment_method")
            for d in entry["excluded_dates"]:
                date.fromisoformat(d)
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise AnalysisError("invalid_quality_manifest") from error
    return value


def covered(quality: dict[str, Any] | None, ticker: str, start: date, end: date) -> bool:
    if quality is None:
        return False
    entry = quality["coverage"].get(ticker)
    return bool(
        entry
        and date.fromisoformat(entry["start"]) <= start
        and date.fromisoformat(entry["end"]) >= end
    )


def capture(
    store: MarketDataStore,
    tickers: list[str],
    start: date,
    end: date,
    cutoff: datetime,
    mode: Mode,
    *,
    quality: dict[str, Any] | None = None,
    acknowledge: bool = False,
    synthetic: bool = False,
    research_calendar: ResearchCalendarPolicy | None = None,
) -> dict[str, Any]:
    validate_range(start, end)
    if research_calendar is not None and mode != "historical_research":
        raise AnalysisError("research_calendar_requires_historical_research")
    cutoff = timestamp(cutoff)
    if end >= cutoff.astimezone(SEOUL).date() or not tickers:
        raise AnalysisError("invalid_snapshot_range")
    quality = validate_quality(quality)
    if quality and calendar_policy(quality) not in (None, research_calendar):
        raise AnalysisError("research_calendar_mismatch")
    if quality and timestamp(quality["verified_at"]) > cutoff:
        raise AnalysisError("quality_not_available_at_snapshot")
    complete = all(covered(quality, t, start, end) for t in tickers)
    if mode == "point_in_time" and not complete:
        raise AnalysisError("quality_unverified")
    if mode == "historical_research" and not complete and not acknowledge:
        raise AnalysisError("research_limitations_acknowledgement_required")
    rows: dict[str, list[dict[str, Any]]] = {}
    unavailable: dict[str, list[str]] = {}
    origins, profiles, markets = set(), set(), set()
    for ticker in sorted(set(tickers)):
        validate_ticker(ticker)
        bars = store.read(ticker, start, end)
        if [b.trading_date for b in bars] != sorted({b.trading_date for b in bars}):
            raise AnalysisError("invalid_market_order_or_duplicates")
        series_profiles = set()
        rows[ticker], unavailable[ticker] = [], []
        for bar in bars:
            bar.__post_init__()
            if bar.ticker != ticker or not start <= bar.trading_date <= end:
                raise AnalysisError("invalid_market_range")
            if bar.collected_at > cutoff:
                unavailable[ticker].append(bar.trading_date.isoformat())
                continue
            if bar.adjustment == "unknown":
                raise AnalysisError("quality_unverified")
            entry = quality["coverage"].get(ticker) if quality else None
            if entry and entry["adjustment"] != bar.adjustment:
                raise AnalysisError("adjustment_mismatch")
            if bar.adjustment == "adjusted" and not (
                complete and entry and entry.get("adjustment_method")
            ):
                raise AnalysisError("unverified_adjustment")
            origins.add("synthetic" if bar.provider == "fake" else "real")
            profiles.add(
                (bar.provider, bar.adjustment, entry.get("adjustment_method", "") if entry else "")
            )
            series_profiles.add((bar.provider, bar.market, bar.adjustment))
            markets.add(bar.market)
            if research_calendar is None or not research_calendar.excludes(bar.trading_date):
                rows[ticker].append(bar.to_dict())
        if len(series_profiles) > 1:
            raise AnalysisError("mixed_series_metadata")
    if len(origins) > 1 or len(profiles) > 1:
        raise AnalysisError("mixed_data_origin_or_price_basis")
    if origins == {"synthetic"} and not synthetic:
        raise AnalysisError("synthetic_mode_required")
    if origins == {"real"} and synthetic:
        raise AnalysisError("synthetic_mode_requires_fake_provider")
    origin = "synthetic" if synthetic else "real"
    flags = ["universe_incomplete", "revision_history_unavailable"]
    if mode == "historical_research":
        flags.append("retrospective_revisions_possible")
    if not complete:
        flags += ["corporate_actions_unverified", "calendar_unverified"]
    if research_calendar is not None:
        flags.append("research_sessions_excluded_not_holidays")
    if quality:
        flags.append("quality_review_may_be_retrospective")
    return {
        "schema_version": 1,
        **({"research_calendar": research_calendar.to_dict()} if research_calendar else {}),
        "mode": mode,
        "data_origin": origin,
        "usage_restriction": "synthetic_test_only"
        if synthetic
        else ("research_only" if mode == "historical_research" else "pit_review_required"),
        "anchor_policy": "nominal_eod_1600" if mode == "historical_research" else "received_at",
        "session_basis": "verified_sessions" if complete else "observed_bars_unverified",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cutoff": cutoff.isoformat(),
        "rows": rows,
        "unavailable_dates": unavailable,
        "quality": quality,
        "quality_flags": sorted(flags),
        "profiles": [list(p) for p in sorted(profiles)],
        "markets": sorted(markets),
    }


def snapshot_bars(snapshot: dict[str, Any], ticker: str) -> list[DailyBar]:
    excluded = excluded_dates(snapshot)
    if excluded and snapshot["mode"] != "historical_research":
        raise AnalysisError("research_calendar_requires_historical_research")
    return [
        DailyBar.from_dict(row)
        for row in snapshot["rows"].get(ticker, [])
        if row["trading_date"] not in excluded
    ]
