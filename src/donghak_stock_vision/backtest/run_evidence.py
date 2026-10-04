"""Read-only baseline evidence checks. No model loading, scoring or simulation."""

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.baseline_costs import tax_rates
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import canonical, digest
from donghak_stock_vision.data.schema import DailyBar, validate_range, validate_ticker

VERSION = "baseline_run_evidence_v1"
VERIFIED = "VERIFIED"
CATEGORIES = (
    "RUN_RANGE",
    "DATASET_IDENTITY",
    "SESSION_ORDERING",
    "CALENDAR_EVIDENCE",
    "CORPORATE_ACTION_EVIDENCE",
    "SETTLEMENT_EVIDENCE",
    "ARTIFACT_IDENTITY",
)
ACTION_KINDS = (
    "split",
    "reverse_split",
    "stock_dividend_bonus",
    "rights",
    "security_identity",
    "merger_spinoff",
)


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class EvidenceCheck:
    category: str
    reasons: tuple[str, ...]
    details: FrozenJSON

    def __post_init__(self) -> None:
        require(self.category in CATEGORIES, "invalid_evidence_category")
        require(
            type(self.reasons) is tuple and all(type(r) is str and r for r in self.reasons),
            "invalid_evidence_reasons",
        )
        require(type(self.details) is FrozenJSON, "immutable_evidence_required")

    @property
    def status(self) -> str:
        return f"BLOCKED_ON_{self.category}" if self.reasons else VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return dict(
            category=self.category,
            status=self.status,
            reasons=list(self.reasons),
            details=self.details.to_dict(),
        )


def check(category: str, reasons: Iterable[str], **details: Any) -> EvidenceCheck:
    return EvidenceCheck(category, tuple(sorted(set(reasons))), FrozenJSON.freeze(details))


@dataclass(frozen=True)
class BaselineRunEvidence:
    checks: tuple[EvidenceCheck, ...]
    identities: FrozenJSON
    version: str = VERSION

    def __post_init__(self) -> None:
        require(self.version == VERSION, "unsupported_evidence_version")
        require(
            type(self.checks) is tuple and tuple(c.category for c in self.checks) == CATEGORIES,
            "all_evidence_categories_required",
        )
        require(type(self.identities) is FrozenJSON, "immutable_evidence_required")

    def to_dict(self) -> dict[str, Any]:
        return dict(
            version=self.version,
            identities=self.identities.to_dict(),
            checks=[c.to_dict() for c in self.checks],
            ready=all(c.status == VERIFIED for c in self.checks),
        )

    @property
    def fingerprint(self) -> str:
        # Observation/report timestamps belong outside this semantic object.
        return digest(self.to_dict())


def audit_rows(
    rows: Iterable[Mapping[str, Any]],
    universe: tuple[str, ...],
    start: date,
    end: date,
    exclusions: tuple[date, ...],
) -> EvidenceCheck:
    """Check supplied order without sorting/repair; the hash uses canonical full source bars."""
    validate_range(start, end)
    require(
        universe == tuple(sorted(set(universe))) and bool(universe), "canonical_universe_required"
    )
    for ticker in universe:
        validate_ticker(ticker)
    require(exclusions == tuple(sorted(set(exclusions))), "canonical_exclusions_required")
    previous: dict[str, date] = {}
    seen: set[tuple[str, date]] = set()
    sessions: dict[str, list[str]] = {t: [] for t in universe}
    h = hashlib.sha256()
    counts = dict(
        rows=0,
        duplicates=0,
        nonincreasing=0,
        malformed=0,
        out_of_range=0,
        unexpected_ticker=0,
        metadata_mismatch=0,
        excluded_rows=0,
    )
    for raw in rows:
        counts["rows"] += 1
        try:
            bar = DailyBar.from_dict(dict(raw))
        except (ValueError, TypeError, KeyError, OverflowError):
            counts["malformed"] += 1
            continue
        h.update((canonical(bar.to_dict()) + "\n").encode())
        t, d = bar.ticker, bar.trading_date
        key = t, d
        counts["duplicates"] += key in seen
        counts["nonincreasing"] += t in previous and d <= previous[t]
        seen.add(key)
        previous[t] = d
        counts["out_of_range"] += not start <= d <= end
        counts["unexpected_ticker"] += t not in sessions
        counts["metadata_mismatch"] += (bar.provider, bar.market, bar.adjustment) != (
            "krx",
            "KOSPI",
            "unadjusted",
        )
        counts["excluded_rows"] += d in exclusions
        if t in sessions and start <= d <= end and d not in exclusions:
            sessions[t].append(d.isoformat())
    issues = [k for k, n in counts.items() if n and k not in {"rows", "excluded_rows"}]
    if counts["rows"] == 0:
        issues.append("no_rows")
    return check(
        "SESSION_ORDERING",
        issues,
        **counts,
        rows_sha256=h.hexdigest(),
        sessions=sessions,
        session_identity=digest(sessions),
        date_count=len({d for ds in sessions.values() for d in ds}),
        tickers_with_rows=sum(bool(ds) for ds in sessions.values()),
        missing_tickers=[t for t, ds in sessions.items() if not ds],
        exclusions=[d.isoformat() for d in exclusions],
    )


def audit_database(
    path: Path,
    universe: tuple[str, ...],
    start: date,
    end: date,
    exclusions: tuple[date, ...],
) -> tuple[EvidenceCheck, dict[str, Any]]:
    """Immutable SQLite read, no schema initialization, WAL creation or data writes."""
    require(path.is_file(), "market_database_missing")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        require(not sidecar.exists() or sidecar.stat().st_size == 0, "active_sqlite_sidecar")
    before = file_sha(path)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
        summary = db.execute(
            "SELECT count(*),count(distinct ticker),min(trading_date),max(trading_date) FROM bars"
        ).fetchone()

        def rows() -> Iterable[Mapping[str, Any]]:
            for ticker, day, payload in db.execute(
                "SELECT ticker,trading_date,payload FROM bars WHERE trading_date BETWEEN ? AND ? "
                "ORDER BY ticker,trading_date",
                (start.isoformat(), end.isoformat()),
            ):
                try:
                    raw = json.loads(payload)
                    if (
                        not isinstance(raw, dict)
                        or raw.get("ticker") != ticker
                        or raw.get("trading_date") != day
                    ):
                        yield {}
                    else:
                        yield raw
                except (ValueError, TypeError):
                    yield {}

        audited = audit_rows(rows(), universe, start, end, exclusions)
    after = file_sha(path)
    require(before == after, "source_database_changed_during_verification")
    if integrity != ["ok"]:
        audited = check(
            "SESSION_ORDERING",
            (*audited.reasons, "sqlite_integrity_failed"),
            **audited.details.to_dict(),
        )
    return audited, dict(
        sha256_before=before,
        sha256_after=after,
        integrity=integrity,
        total_rows=summary[0],
        total_tickers=summary[1],
        first_date=summary[2],
        last_date=summary[3],
    )


def dataset_check(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> EvidenceCheck:
    required = {"db_sha256", "rows_sha256", "universe_sha256", "dataset_id", "rows"}
    reasons = [f"missing_expected:{k}" for k in required - expected.keys()]
    reasons += [f"mismatch:{k}" for k in required if k in expected and actual.get(k) != expected[k]]
    return check("DATASET_IDENTITY", reasons, actual=dict(actual), expected=dict(expected))


def reviewed_document(
    document: FrozenJSON | None, expected_pin: str | None
) -> dict[str, Any] | None:
    """Pins come from independent review; a URL or self-declared verified flag is not evidence."""
    if document is None or expected_pin is None:
        return None
    sha(expected_pin)
    if document.identifier != expected_pin:
        return None
    value = document.to_dict()
    if (
        not value.get("source_urls")
        or not value.get("source_content_hashes")
        or not value.get("review_reference")
    ):
        return None
    for reference in value["source_content_hashes"]:
        sha(reference)
    return value


def calendar_check(
    sessions: Mapping[str, list[str]],
    start: date,
    end: date,
    exclusions: tuple[date, ...],
    document: FrozenJSON | None,
    expected_pin: str | None,
) -> EvidenceCheck:
    data = reviewed_document(document, expected_pin)
    if data is None:
        return check("CALENDAR_EVIDENCE", ["missing_independently_reviewed_exchange_calendar"])
    days = data.get("days", {})
    reasons = []
    d = start
    while d <= end:
        classification = days.get(d.isoformat())
        if classification not in {"session", "weekend", "holiday", "extraordinary_closure"}:
            reasons.append("unclassified_calendar_date")
        if (d.weekday() >= 5) != (classification == "weekend"):
            reasons.append("weekend_classification_mismatch")
        d += timedelta(days=1)
    for d in exclusions:
        if start <= d <= end and days.get(d.isoformat()) != "session":
            reasons.append("research_exclusion_is_not_exchange_holiday")
    expected = {
        d
        for d, state in days.items()
        if state == "session"
        and start.isoformat() <= d <= end.isoformat()
        and d not in {x.isoformat() for x in exclusions}
    }
    missing = {}
    for ticker, observed in sessions.items():
        if set(observed) - expected:
            reasons.append("observed_non_session")
        # No delisting/suspension inference from absence, including empty histories.
        absent = sorted(expected - set(observed))
        if absent:
            missing[ticker] = absent
            reasons.append("unexplained_missing_ticker_sessions")
    return check(
        "CALENDAR_EVIDENCE",
        reasons,
        calendar_pin=expected_pin,
        missing_sessions=missing,
        exclusions=[d.isoformat() for d in exclusions],
    )


def corporate_check(
    dataset_id: str,
    universe_hash: str,
    start: date,
    end: date,
    document: FrozenJSON | None,
    expected_pin: str | None,
) -> EvidenceCheck:
    data = reviewed_document(document, expected_pin)
    reasons = []
    if data is None:
        reasons.append("missing_authoritative_corporate_action_review")
    elif (
        data.get("dataset_id") != dataset_id
        or data.get("universe_sha256") != universe_hash
        or data.get("start") != start.isoformat()
        or data.get("end") != end.isoformat()
        or data.get("reviewed_action_kinds") != list(ACTION_KINDS)
        or data.get("unresolved_conditions") != []
        or data.get("quality_manifest_sha256") is None
    ):
        reasons.append("incomplete_or_unresolved_corporate_action_review")
    if data and not reasons:
        sha(data["quality_manifest_sha256"])
    return check("CORPORATE_ACTION_EVIDENCE", reasons, review_pin=expected_pin)


def settlement_check(
    trade_dates: tuple[date, ...],
    document: FrozenJSON | None,
    expected_pin: str | None,
) -> EvidenceCheck:
    data = reviewed_document(document, expected_pin)
    reasons: list[str] = []
    mapping = {}
    if data is None:
        return check(
            "SETTLEMENT_EVIDENCE", ["missing_reviewed_settlement_calendar_and_effective_rule"]
        )
    if data.get("rule") != "kospi_cash_equity_T_plus_2_settlement_sessions":
        return check("SETTLEMENT_EVIDENCE", ["unsupported_settlement_rule"])
    try:
        start, end = (
            date.fromisoformat(data["effective_start"]),
            date.fromisoformat(data["effective_end"]),
        )
        days = data["days"]
        sessions = sorted(d for d, kind in days.items() if kind == "session")
        for trade in trade_dates:
            if not start <= trade <= end or trade.isoformat() not in sessions:
                reasons.append("unsupported_trade_date")
                continue
            later = [d for d in sessions if d > trade.isoformat()]
            if len(later) < 2:
                reasons.append("settlement_calendar_tail_missing")
                continue
            settlement = date.fromisoformat(later[1])
            cursor = trade
            while cursor <= settlement:
                kind = days.get(cursor.isoformat())
                if kind not in {"session", "weekend", "holiday", "extraordinary_closure"}:
                    reasons.append("unclassified_settlement_date")
                if (cursor.weekday() >= 5) != (kind == "weekend"):
                    reasons.append("settlement_weekend_mismatch")
                cursor += timedelta(days=1)
            if settlement > end:
                reasons.append("settlement_rule_effectivity_exceeded")
            tax_rates(settlement)  # Existing bounded schedule, not a new tax calculator.
            mapping[trade.isoformat()] = settlement.isoformat()
    except (KeyError, ValueError, TypeError):
        reasons.append("unsupported_settlement_date_or_document")
    return check("SETTLEMENT_EVIDENCE", reasons, calendar_pin=expected_pin, mapping=mapping)


def identity_check(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> EvidenceCheck:
    required = {
        "up_sha",
        "down_sha",
        "up_fingerprint",
        "down_fingerprint",
        "lock_id",
        "qualification_id",
        "features",
        "feature_version",
        "label_version",
        "signal_policy",
        "quality_policy",
        "baseline_policy",
        "cost_policy",
        "runner_version",
    }
    reasons = [f"missing_expected:{k}" for k in required - expected.keys()]
    reasons += [f"mismatch:{k}" for k in required if k in expected and actual.get(k) != expected[k]]
    return check("ARTIFACT_IDENTITY", reasons, actual=dict(actual), expected=dict(expected))
