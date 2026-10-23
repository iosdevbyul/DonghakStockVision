"""Bounded, offline, read-only original KRX replay and blocked strategy-data readiness."""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import (
    CalendarMonthSource,
    HistoricalReadinessResult,
    HistoricalReadScope,
)
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.providers.base import ProviderError, RawPage
from donghak_stock_vision.providers.krx import ENDPOINTS, KRXProvider
from donghak_stock_vision.trading.domain import Exchange, Money
from donghak_stock_vision.trading.paper_types import wire


class _OfflineKRXParser(KRXProvider):
    """Reuse the original parser without creating a credential, HTTP client or request budget."""

    def __init__(self, market: str) -> None:
        require(market in ENDPOINTS, "unsupported_source_market")
        self.market = market

    def fetch_market(self, day: date) -> RawPage:
        raise ProviderError("readiness_network_forbidden")


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _calendar(
    owners: tuple[CalendarMonthSource, ...],
    scope: HistoricalReadScope,
) -> tuple[dict[str, str], list[str], list[str]]:
    require(type(owners) is tuple and len(owners) <= 14, "calendar_owner_budget")
    days: dict[str, str] = {}
    pins = []
    for owner in owners:
        require(type(owner) is CalendarMonthSource, "original_calendar_owner_required")
        normalized = owner.verify()
        month = normalized.to_dict()
        require(not days.keys() & month["days"].keys(), "duplicate_calendar_sessions")
        days.update(month["days"])
        pins.append(normalized.identifier)
    reasons = []
    if owners and scope.instrument.exchange is not Exchange.KOSPI:
        reasons.append(
            "calendar_market_mismatch"
        )  # Original ldMktTpCd=1 is not US/KOSDAQ approval.
    current = scope.window.start
    while current <= scope.window.end:
        if current.isoformat() not in days:
            reasons.append("calendar_scope_incomplete")
        current += timedelta(days=1)
    return days, sorted(pins), sorted(set(reasons))


def inspect_historical_market(
    database: Path,
    scope: HistoricalReadScope,
    trusted_scope_hash: str,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> HistoricalReadinessResult:
    require(type(scope) is HistoricalReadScope, "historical_scope_owner_required")
    # Check roles/partitions before opening files or accessing nested calendar data.
    scope.__post_init__()
    require(scope.document.identifier == sha(trusted_scope_hash), "scope_pin_mismatch")
    path = database.resolve(strict=True)
    require(path.is_file() and path.stat().st_size <= 2 * 1024**3, "database_byte_budget")
    require(
        not Path(str(path) + "-wal").exists() and not Path(str(path) + "-journal").exists(),
        "active_database_journal_unsupported",
    )
    require(_file_hash(path) == scope.trusted_database_hash, "database_pin_mismatch")
    calendar_days, calendar_pins, calendar_errors = _calendar(calendars, scope)
    invalid: list[str] = list(calendar_errors) if calendars else []
    provenance_missing: list[str] = []
    records: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    days_seen: list[str] = []
    adjustments: set[str] = set()
    raw_used: dict[int, tuple[dict[str, Any], list[DailyBar]]] = {}
    raw_bytes = 0
    # immutable=1 avoids journal/SHM writes; reject journals and compare the complete pin twice.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        require({"bars", "raw_pages", "bar_sources"} <= tables, "market_schema_missing")
        inventory = {
            "bars": dict(
                zip(
                    ("count", "symbols", "first_date", "last_date"),
                    connection.execute(
                        "SELECT COUNT(*),COUNT(DISTINCT ticker),"
                        "MIN(trading_date),MAX(trading_date) "
                        "FROM bars"
                    ).fetchone(),
                    strict=True,
                )
            ),
            "raw_pages": [
                dict(
                    zip(
                        (
                            "provider",
                            "count",
                            "first_source_date",
                            "last_source_date",
                            "first_ingested_at",
                            "last_ingested_at",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in connection.execute(
                    "SELECT provider,COUNT(*),MIN(source_date),MAX(source_date),MIN(collected_at),"
                    "MAX(collected_at) FROM raw_pages GROUP BY provider ORDER BY provider"
                )
            ],
            "raw_capture_receipt_table_present": "raw_capture_receipts" in tables,
            "scope": "aggregate_metadata_only_not_full_database_validation",
        }
        selected = connection.execute(
            "SELECT ticker,trading_date,payload FROM bars WHERE ticker=? "
            "AND trading_date BETWEEN ? AND ? "
            "ORDER BY trading_date LIMIT ?",
            (
                scope.instrument.symbol,
                scope.window.start.isoformat(),
                scope.window.end.isoformat(),
                scope.max_rows + 1,
            ),
        ).fetchall()
        require(len(selected) <= scope.max_rows, "scoped_row_budget_exceeded")
        if not selected:
            invalid.append("no_scoped_observations")
        parser = _OfflineKRXParser(scope.instrument.exchange.value)
        for ticker, trading_day, payload in selected:
            try:
                require(len(payload.encode()) <= 16384, "bar_payload_budget")
                bar = DailyBar.from_dict(json.loads(payload))
                require(
                    bar.ticker == ticker == scope.instrument.symbol
                    and bar.trading_date.isoformat() == trading_day,
                    "row_identity_mismatch",
                )
                require(
                    bar.provider == "krx" and bar.market == scope.instrument.exchange.value,
                    "source_instrument_mismatch",
                )
            except (ValueError, TypeError, KeyError):
                invalid.append("invalid_bar_or_source_identity")
                continue
            days_seen.append(trading_day)
            adjustments.add(bar.adjustment)
            links = connection.execute(
                "SELECT raw_id FROM bar_sources WHERE ticker=? AND trading_date=? "
                "ORDER BY raw_id LIMIT 17",
                (ticker, trading_day),
            ).fetchall()
            require(len(links) <= 16, "raw_link_budget")
            matching = []
            if not links:
                provenance_missing.append("original_raw_source_missing")
            for (raw_id,) in links:
                if raw_id not in raw_used:
                    row = connection.execute(
                        "SELECT provider,ticker,collected_at,source_date,sha256 "
                        "FROM raw_pages WHERE id=?",
                        (raw_id,),
                    ).fetchone()
                    if row is None:
                        provenance_missing.append("original_raw_source_missing")
                        continue
                    provider, source_scope, collected_at, source_date, expected = row
                    meta = {
                        "raw_id": raw_id,
                        "provider": provider,
                        "scope": source_scope,
                        "ingested_at": collected_at,
                        "source_date": source_date,
                        "sha256": expected,
                        "bytes": None,
                    }
                    parsed = []
                    # Reject wrong dates/partitions BEFORE loading any response body.
                    if source_date != trading_day:
                        invalid.append("raw_source_outside_authorized_observation")
                    elif provider != "krx" or source_scope not in (
                        scope.instrument.symbol,
                        "@krx_market:" + scope.instrument.exchange.value,
                    ):
                        invalid.append("raw_source_scope_mismatch")
                    else:
                        length = connection.execute(
                            "SELECT length(body) FROM raw_pages WHERE id=?", (raw_id,)
                        ).fetchone()[0]
                        raw_bytes += length
                        require(raw_bytes <= scope.max_raw_bytes, "raw_byte_budget_exceeded")
                        meta["bytes"] = length
                        body = connection.execute(
                            "SELECT body FROM raw_pages WHERE id=?", (raw_id,)
                        ).fetchone()[0]
                        if hashlib.sha256(body).hexdigest() != expected:
                            invalid.append("raw_source_hash_mismatch")
                            raw_used[raw_id] = meta, []
                            continue
                        try:
                            parsed = parser.parse_market(
                                RawPage(
                                    body,
                                    datetime.fromisoformat(collected_at),
                                    date.fromisoformat(source_date),
                                )
                            )
                        except (ProviderError, ValueError, TypeError):
                            invalid.append("original_raw_replay_failed")
                    raw_used[raw_id] = meta, parsed
                meta, parsed = raw_used[raw_id]
                if meta["source_date"] != trading_day:
                    invalid.append("raw_source_outside_authorized_observation")
                    continue
                original = [
                    b for b in parsed if b.ticker == ticker and b.trading_date == bar.trading_date
                ]
                if len(original) > 1:
                    invalid.append("duplicate_raw_observations")
                elif len(original) == 1 and original[0].to_dict() == bar.to_dict():
                    matching.append(raw_id)
                # Previous raw revisions may legitimately be linked; only exact replay counts.
            if links and not matching:
                invalid.append("cleaned_bar_not_reproduced_from_original")
            references.append(
                {
                    "trading_date": trading_day,
                    "bar_hash": digest(bar.to_dict()),
                    "matched_raw_ids": matching,
                }
            )
            records.append(
                {
                    "instrument": wire(scope.instrument),
                    "trading_date": trading_day,
                    "price_semantics": bar.adjustment,
                    "close": wire(Money(Decimal(bar.close), scope.instrument.currency)),
                    "observation_at": None,
                    "available_at": None,
                    "unavailable_reason": "daily_date_and_retrieval_time_do_not_prove_publication",
                    "ingested_at": bar.to_dict()["collected_at"],
                    "bar_hash": digest(bar.to_dict()),
                    "raw_ids": matching,
                }
            )
        if len(days_seen) != len(set(days_seen)):
            invalid.append("duplicate_observation_sessions")
        expected_sessions = sorted(
            day
            for day, state in calendar_days.items()
            if state == "session"
            and scope.window.start.isoformat() <= day <= scope.window.end.isoformat()
        )
        missing = sorted(set(expected_sessions) - set(days_seen)) if calendars else None
        unexpected = sorted(set(days_seen) - set(expected_sessions)) if calendars else None
        if missing:
            invalid.append("missing_expected_sessions")
        if unexpected:
            invalid.append("observation_outside_calendar_sessions")
    require(_file_hash(path) == scope.trusted_database_hash, "database_changed_during_inspection")
    require(
        not Path(str(path) + "-wal").exists() and not Path(str(path) + "-journal").exists(),
        "database_journal_changed_during_inspection",
    )
    blockers = [
        "independent_historical_availability_missing",
        "historical_observation_timestamp_missing",
        "independent_historical_custody_unverified",
        "corporate_action_coverage_not_verified",
        "historical_security_identity_not_verified",
        "strict_pit_qualification_unavailable",
        "authoritative_strategy_historical_replay_authorization_unavailable",
    ]
    if not calendars:
        blockers.append("official_session_calendar_missing")
    if adjustments - {"unadjusted"}:
        blockers.append("adjustment_information_time_and_action_evidence_missing")
    classification = (
        "structurally_invalid"
        if invalid
        else "structurally_valid_missing_original_provenance"
        if provenance_missing
        else "provenance_replayed_missing_independent_qualification"
    )
    observations = FrozenJSON.freeze(
        {
            "version": "historical_strategy_observations_v1",
            "scope_hash": trusted_scope_hash,
            "database_hash": scope.trusted_database_hash,
            "records": records,
            "canonical_market_quotes": None,
            "strategy_execution_eligible": False,
            "data_origin": "archived_provider_claim_not_independently_authenticated",
        }
    )
    report = {
        "version": "historical_market_readiness_v1",
        "scope_hash": trusted_scope_hash,
        "source": {
            "contract": "krx_daily_sqlite_v1",
            "recorded_provider": "krx",
            "database_hash": scope.trusted_database_hash,
            "endpoint_contract": "https://data-dbg.krx.co.kr/svc/apis/sto/"
            + ENDPOINTS[scope.instrument.exchange.value],
            "issuer_authenticated": False,
            "provenance_scope": "local_archived_raw_replay_only",
        },
        "inventory": inventory,
        "instrument": wire(scope.instrument),
        "window": scope.window.to_dict(),
        "partition": scope.partition,
        "split_hash": scope.trusted_split_hash,
        "classification": classification,
        "scoped_rows": len(records),
        "invalid_reasons": sorted(set(invalid)),
        "missing_provenance": sorted(set(provenance_missing)),
        "blockers": sorted(set(blockers)),
        "raw_sources": [raw_used[k][0] for k in sorted(raw_used)],
        "bar_references": references,
        "price_semantics": sorted(adjustments),
        "calendar_pins": calendar_pins,
        "missing_expected_sessions": missing,
        "unexpected_sessions": unexpected,
        "duplicate_sessions": len(days_seen) - len(set(days_seen)),
        "observation_timestamp": None,
        "availability_timestamp": None,
        "observation_projection_hash": observations.identifier,
        "eligible_for_historical_replay": False,
        "strict_pit_approved": False,
        "oos_approved": False,
        "operational_eligible": False,
        "limitations": [
            "no_approval_authority_added",
            "raw_hash_is_not_historical_custody",
            "scoped_raw_replay_not_full_database_verification",
            "no_model_or_strategy_execution",
        ],
    }
    report["content_fingerprint"] = digest(report)
    return HistoricalReadinessResult(FrozenJSON.freeze(report), observations)


def verify_historical_readiness(
    result: HistoricalReadinessResult,
    trusted_report_hash: str,
    database: Path,
    scope: HistoricalReadScope,
    trusted_scope_hash: str,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> None:
    require(type(result) is HistoricalReadinessResult, "readiness_owner_required")
    require(
        result.report.to_dict().get("content_fingerprint") == sha(trusted_report_hash),
        "readiness_report_pin_mismatch",
    )
    fresh = inspect_historical_market(database, scope, trusted_scope_hash, calendars)
    require(result == fresh, "readiness_semantic_replay_mismatch")


def controlled_historical_replay(
    result: HistoricalReadinessResult,
    trusted_report_hash: str,
    database: Path,
    scope: HistoricalReadScope,
    trusted_scope_hash: str,
    calendars: tuple[CalendarMonthSource, ...] = (),
) -> FrozenJSON:
    verify_historical_readiness(
        result, trusted_report_hash, database, scope, trusted_scope_hash, calendars
    )
    # No authentic native historical approval bridge exists. Never manufacture an eligible report.
    return FrozenJSON.freeze(
        {
            "version": "historical_strategy_replay_readiness_v1",
            "status": "blocked",
            "readiness_hash": trusted_report_hash,
            "reasons": sorted(
                set(
                    result.report.to_dict()["blockers"]
                    + result.report.to_dict()["invalid_reasons"]
                    + result.report.to_dict()["missing_provenance"]
                )
            ),
            "strategy_execution_started": False,
            "orders": 0,
            "fills": 0,
            "pit_approved": False,
            "oos_approved": False,
            "operational_eligible": False,
        }
    )
