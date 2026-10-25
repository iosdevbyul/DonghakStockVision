"""Bounded descriptive analytics over original persisted bars; no strategy authorization."""

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import CalendarMonthSource
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.schema import DailyBar, validate_range, validate_ticker
from donghak_stock_vision.storage.historical_readiness import _file_hash

MAX_ROWS = 2500
MAX_PAYLOAD = 8192


def validate_access(
    ticker: str,
    start: date,
    end: date,
    split: ExplicitResearchSplit,
    split_pin: str,
    max_rows: int = 1250,
) -> None:
    validate_ticker(ticker)
    validate_range(start, end)
    require(type(split) is ExplicitResearchSplit, "original_research_split_required")
    split.__post_init__()
    require(split.identifier == sha(split_pin), "research_split_pin_mismatch")
    require(
        end < split.final_test.start or start > split.final_test.end,
        "protected_final_test_or_holdout_overlap",
    )
    require((end - start).days <= 3660, "date_range_limit_exceeded")
    require(type(max_rows) is int and 1 <= max_rows <= MAX_ROWS, "invalid_observation_limit")


def _number(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000000000000000001")), "f")


def _calendar(
    owners: tuple[CalendarMonthSource, ...], start: date, end: date, market: str
) -> tuple[dict[str, str], list[str], list[str]]:
    require(type(owners) is tuple and len(owners) <= 24, "calendar_month_limit")
    days: dict[str, str] = {}
    pins = []
    for owner in owners:
        require(type(owner) is CalendarMonthSource, "original_calendar_owner_required")
        month = owner.verify()
        values = month.to_dict()["days"]
        require(not days.keys() & values.keys(), "duplicate_calendar_month")
        days.update(values)
        pins.append(month.identifier)
    reasons = []
    if not owners:
        reasons.append("trusted_calendar_not_supplied")
    elif market != "KOSPI":
        reasons.append("original_kind_calendar_is_kospi_only")
    elif any(
        (start + timedelta(days=i)).isoformat() not in days for i in range((end - start).days + 1)
    ):
        reasons.append("calendar_date_coverage_incomplete")
    return days, sorted(pins), reasons


def _statistics(
    entries: list[dict[str, Any]], calendar: dict[str, str], calendar_verified: bool, blocked: bool
) -> dict[str, Any]:
    returns = []
    simple: list[Decimal] = []
    logs: list[Decimal] = []
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        for previous, current in zip(entries, entries[1:], strict=False):
            reason = "invalid_or_duplicate_observations" if blocked else None
            a = date.fromisoformat(previous["date"])
            b = date.fromisoformat(current["date"])
            if calendar_verified and any(
                calendar.get((a + timedelta(days=i)).isoformat()) == "session"
                for i in range(1, (b - a).days)
            ):
                reason = "missing_expected_session_between_observations"
            ratio = (
                None
                if reason
                else Decimal(current["ohlcv"]["close"]) / Decimal(previous["ohlcv"]["close"])
            )
            value = None if ratio is None else ratio - 1
            if value is not None and ratio is not None:
                simple.append(value)
                logs.append(ratio.ln())
            returns.append(
                {
                    "date": current["date"],
                    "previous_date": previous["date"],
                    "return": _number(value) if value is not None else None,
                    "unavailable_reason": reason,
                    "calendar_days": (b - a).days,
                    "session_spacing": "verified" if calendar_verified else "unverified",
                }
            )
        unavailable = (
            "invalid_or_duplicate_observations"
            if blocked
            else "no_observations"
            if not entries
            else None
        )
        first = entries[0]["ohlcv"]["close"] if entries and not blocked else None
        last = entries[-1]["ohlcv"]["close"] if entries and not blocked else None
        volatility = None
        if not blocked and len(logs) >= 2:
            mean = sum(logs, Decimal(0)) / Decimal(len(logs))
            volatility = (
                sum(((v - mean) ** 2 for v in logs), Decimal(0)) / Decimal(len(logs) - 1)
            ).sqrt()
        return {
            "price_change": last - first if first is not None and last is not None else None,
            "percentage_change": _number((Decimal(last) / Decimal(first) - 1) * 100)
            if first is not None and last is not None
            else None,
            "average_observed_daily_volume": _number(
                sum((Decimal(e["ohlcv"]["volume"]) for e in entries), Decimal(0))
                / Decimal(len(entries))
            )
            if unavailable is None
            else None,
            "unavailable_reason": unavailable,
            "return_pair_count": len(simple),
            "excluded_return_pairs": len(returns) - len(simple),
            "daily_returns": returns,
            "realized_log_return_volatility": _number(volatility)
            if volatility is not None
            else None,
            "volatility_unavailable_reason": unavailable
            or ("at_least_two_eligible_return_pairs_required" if volatility is None else None),
            "volatility_method": "sample_stddev_log_close_returns_ddof_1_not_annualized",
            "numeric_format": "decimal_strings_18_places_half_even_precision_50",
        }


def analyze_market_data(
    database: Path,
    ticker: str,
    start: date,
    end: date,
    *,
    expected_sha256: str,
    split: ExplicitResearchSplit,
    split_pin: str,
    data_origin: str,
    market: str = "KOSPI",
    view: str = "summary",
    max_rows: int = 1250,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> FrozenJSON:
    validate_access(ticker, start, end, split, split_pin, max_rows)  # Before source/calendar I/O.
    require(data_origin in ("local_krx", "synthetic"), "explicit_data_origin_required")
    require(
        market in ("KOSPI", "KOSDAQ", "KONEX") and view in ("summary", "history", "quality"),
        "unsupported_market_or_view",
    )
    sha(expected_sha256)
    require(database.is_file() and not database.is_symlink(), "market_database_unavailable")
    path = database.resolve()
    require(path.stat().st_size <= 2 * 1024**3, "database_size_limit")
    for suffix in ("-wal", "-journal"):
        require(not Path(str(path) + suffix).exists(), "active_database_journal_unsupported")
    require(_file_hash(path) == expected_sha256, "database_fingerprint_mismatch")
    calendar, calendar_pins, calendar_reasons = _calendar(calendars, start, end, market)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        calls = 0

        def progress() -> int:
            nonlocal calls
            calls += 1
            return int(calls > 50000)

        connection.set_progress_handler(
            progress, 1000
        )  # Bounded SQL work, including metadata scans.
        columns = {r[1] for r in connection.execute("PRAGMA table_info(bars)")}
        require({"ticker", "trading_date", "payload"} <= columns, "market_bars_schema_missing")
        indexes = list(connection.execute("PRAGMA index_list(bars)"))
        indexed = any(r[1] == "sqlite_autoindex_bars_1" and r[2] == 1 for r in indexes)
        hint = " INDEXED BY sqlite_autoindex_bars_1" if indexed else ""
        global_row = connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT ticker),MIN(trading_date),MAX(trading_date) FROM bars"
            + hint
        ).fetchone()
        symbol_row = connection.execute(
            "SELECT COUNT(*),MIN(trading_date),MAX(trading_date) FROM bars"
            + hint
            + " WHERE ticker=?",
            (ticker,),
        ).fetchone()
        require(symbol_row[0] > 0, "symbol_not_found")
        # Select length before payload to reject maliciously oversized data without loading it.
        sizes = connection.execute(
            "SELECT MAX(LENGTH(CAST(payload AS BLOB))) FROM bars"
            + hint
            + " WHERE ticker=? AND trading_date BETWEEN ? AND ?",
            (ticker, start.isoformat(), end.isoformat()),
        ).fetchone()[0]
        require(sizes is None or sizes <= MAX_PAYLOAD, "persisted_payload_size_limit")
        rows = connection.execute(
            "SELECT trading_date,payload FROM bars"
            + hint
            + " WHERE ticker=? AND trading_date BETWEEN ? AND ? "
            "ORDER BY trading_date,payload LIMIT ?",
            (ticker, start.isoformat(), end.isoformat(), max_rows + 1),
        ).fetchall()
        require(len(rows) <= max_rows, "observation_limit_exceeded")
    dates = Counter(row[0] for row in rows)
    issues: list[dict[str, str]] = []
    entries = []
    valid: list[DailyBar] = []
    profiles: set[tuple[str, str, str]] = set()
    regressions = []
    previous = None
    for day, serialized in rows:
        errors = []
        bar = None
        if dates[day] > 1:
            errors.append("duplicate_observation_date")
        try:
            payload = json.loads(serialized)
            require(type(payload) is dict, "object_required")
            try:
                timestamp(payload.get("collected_at", ""))
            except (ValueError, TypeError, AttributeError):
                errors.append("invalid_collection_timestamp")
            bar = DailyBar.from_dict(payload)
            require(
                bar.ticker == ticker and bar.trading_date.isoformat() == day,
                "payload_identity_mismatch",
            )
            require(bar.provider == "krx" and bar.market == market, "provider_or_market_mismatch")
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            errors.append("invalid_ohlcv_or_source_identity")
        if not errors and bar is not None:
            profiles.add((bar.provider, bar.market, bar.adjustment))
            if previous is not None and bar.collected_at < previous.collected_at:
                regressions.append(day)
            previous = bar
            valid.append(bar)
        entry = {
            "date": day,
            "ohlcv": bar.to_dict() if not errors and bar is not None else None,
            "issues": sorted(set(errors)),
        }
        entries.append(entry)
        issues.extend({"date": day, "code": code} for code in entry["issues"])
    usable = [e for e in entries if e["ohlcv"] is not None]
    mixed = len(profiles) > 1
    expected_sessions = (
        [
            d
            for d, kind in sorted(calendar.items())
            if start.isoformat() <= d <= end.isoformat() and kind == "session"
        ]
        if not calendar_reasons
        else None
    )
    missing = (
        [d for d in expected_sessions if d not in dates] if expected_sessions is not None else None
    )
    off_session = (
        [d for d in dates if d not in expected_sessions] if expected_sessions is not None else None
    )
    stats = _statistics(
        usable, calendar, not calendar_reasons, bool(issues) or mixed or bool(off_session)
    )
    warnings = list(calendar_reasons)
    if issues:
        warnings.append("invalid_or_duplicate_observations")
    if mixed:
        warnings.append("mixed_adjustment_semantics")
    if regressions:
        warnings.append("collection_times_regress_not_publication_chronology")
    if missing:
        warnings.append("missing_expected_sessions")
    if off_session:
        warnings.append("observations_on_calendar_non_sessions")
    if symbol_row[2] < global_row[3]:
        warnings.append("symbol_lags_dataset_coverage")
    if end.isoformat() > global_row[3]:
        warnings.append("requested_end_exceeds_dataset_coverage")
    latest = entries[-1]["ohlcv"] if entries else None
    result = FrozenJSON.freeze(
        {
            "version": "market_data_analytics_v1",
            "view": view,
            "ticker": ticker,
            "security_name": None,
            "name_unavailable_reason": "verified_security_name_reader_not_configured",
            "data_origin": data_origin,
            "origin_assurance": "operator_declared_not_pit_authentication",
            "market": market,
            "currency": "KRW",
            "database_sha256": expected_sha256,
            "dataset_coverage": dict(
                zip(("observations", "symbols", "first_date", "last_date"), global_row, strict=True)
            ),
            "symbol_coverage": dict(
                zip(("observations", "first_date", "last_date"), symbol_row, strict=True)
            ),
            "selected_range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rows": len(rows),
                "valid_rows": len(valid),
            },
            "status": "empty" if not rows else "warnings" if warnings else "ok",
            "latest_in_range": latest,
            "latest_available_ohlcv": latest
            if latest is not None and latest["trading_date"] == symbol_row[2]
            else None,
            "latest_available_unavailable_reason": None
            if latest is not None and latest["trading_date"] == symbol_row[2]
            else "latest_symbol_record_outside_selected_range_or_invalid",
            "price_semantics": sorted({p[2] for p in profiles}),
            "statistics": stats,
            "observations": entries if view == "history" else None,
            "quality": {
                "issues": issues,
                "duplicate_dates": sorted(d for d, n in dates.items() if n > 1),
                "invalid_rows": sum(bool(e["issues"]) for e in entries),
                "collection_timestamp_regressions": regressions,
                "physical_source_order": "not_recorded; query_sorted_by_trading_date",
                "calendar_status": "verified_original_kind_sessions"
                if not calendar_reasons
                else "unavailable",
                "calendar_unavailable_reasons": calendar_reasons,
                "calendar_pins": calendar_pins,
                "missing_sessions": missing,
                "off_session_observations": off_session,
                "storage_unique_index_present": indexed,
                "warnings": warnings,
                "stale_relative_to_dataset": symbol_row[2] < global_row[3],
                "lag_calendar_days": (
                    date.fromisoformat(global_row[3]) - date.fromisoformat(symbol_row[2])
                ).days,
                "wall_clock_staleness": None,
            },
            "access": {
                "original_split_pin": split_pin,
                "final_test_payload_accessed": False,
                "purpose": "descriptive_only_no_strategy_eligibility",
                "pit_qualified": False,
                "oos_approved": False,
                "corporate_action_adjustments_applied": False,
            },
            "limits": {
                "max_rows": max_rows,
                "max_calendar_days": 3661,
                "max_payload_bytes": MAX_PAYLOAD,
            },
            "limitations": [
                "retrieval_times_are_not_publication_or_decision_availability",
                "unadjusted_returns_can_include_corporate_actions",
                "no_strategy_inference_or_profitability_claim",
                "global_coverage_is_key_metadata_only_not_full_dataset_quality_validation",
            ],
        }
    )
    require(len(result.payload_json.encode()) <= 4 * 1024 * 1024, "analytics_output_limit")
    require(_file_hash(path) == expected_sha256, "database_changed_during_query")
    for suffix in ("-wal", "-journal"):
        require(not Path(str(path) + suffix).exists(), "database_changed_during_query")
    return result
