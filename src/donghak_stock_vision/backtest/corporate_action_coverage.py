"""Fail-closed final corporate-action coverage contract.

This layer does not collect evidence, infer events from prices, or authorize price
adjustments. It only validates reviewed official evidence records and summarizes
whether the locked 993-security baseline universe has complete corporate-action
coverage.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from donghak_stock_vision.backtest import dart_evidence
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest

VERIFIED_STATUSES = {"VERIFIED_EVENT_PRESENT", "VERIFIED_NO_RELEVANT_EVENT"}


@dataclass(frozen=True)
class CorporateActionEventEvidence:
    """One reviewed economic event with an official effective date."""

    ticker: str
    event_class: str
    effective_date: date
    source: FrozenJSON
    review_reference: str
    document_reference: str
    version: str = "corporate_action_event_v1"

    def __post_init__(self) -> None:
        require(re.fullmatch(r"[0-9]{6}", self.ticker) is not None, "exact_ticker_required")
        require(
            self.event_class in dart_evidence.EVENT_CLASSES,
            "unsupported_corporate_action_class",
        )
        require(type(self.effective_date) is date, "effective_date_required")
        require(dart_evidence.source_valid(self.source.to_dict()), "official_event_source_required")
        require(bool(self.review_reference), "event_review_reference_required")
        require(bool(self.document_reference), "event_document_reference_required")
        require(self.version == "corporate_action_event_v1", "unsupported_event_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ticker": self.ticker,
            "event_class": self.event_class,
            "effective_date": self.effective_date.isoformat(),
            "source": self.source.to_dict(),
            "review_reference": self.review_reference,
            "document_reference": self.document_reference,
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class CorporateActionSecurityCoverage:
    """Complete reviewed coverage claim for one exact six-digit security."""

    ticker: str
    security_identity: str
    identity_source: FrozenJSON
    identity_effective_start: date
    identity_effective_end: date
    historical_identity_verified: bool
    query_start: date
    query_end: date
    covered_classes: tuple[str, ...]
    query_complete: bool
    authoritative_complete_semantics: bool
    query_sources: tuple[FrozenJSON, ...]
    events: tuple[CorporateActionEventEvidence, ...]
    review_reference: str
    version: str = "corporate_action_security_coverage_v1"

    def __post_init__(self) -> None:
        require(re.fullmatch(r"[0-9]{6}", self.ticker) is not None, "exact_ticker_required")
        require(bool(self.security_identity), "security_identity_required")
        require(
            dart_evidence.source_valid(self.identity_source.to_dict()),
            "official_identity_source_required",
        )
        require(
            type(self.identity_effective_start) is date
            and type(self.identity_effective_end) is date
            and self.identity_effective_start <= self.identity_effective_end,
            "invalid_identity_period",
        )
        require(
            type(self.historical_identity_verified) is bool,
            "identity_verification_flag_required",
        )
        require(
            type(self.query_start) is date
            and type(self.query_end) is date
            and self.query_start <= self.query_end,
            "invalid_query_period",
        )
        classes = tuple(sorted(set(self.covered_classes)))
        require(
            len(classes) == len(self.covered_classes)
            and set(classes) <= set(dart_evidence.EVENT_CLASSES),
            "invalid_corporate_action_classes",
        )
        object.__setattr__(self, "covered_classes", classes)
        require(type(self.query_complete) is bool, "query_complete_flag_required")
        require(
            type(self.authoritative_complete_semantics) is bool,
            "complete_semantics_flag_required",
        )
        require(bool(self.query_sources), "official_query_source_required")
        for source in self.query_sources:
            require(dart_evidence.source_valid(source.to_dict()), "official_query_source_required")
        ordered_sources = tuple(sorted(self.query_sources, key=lambda value: value.identifier))
        require(
            len({source.identifier for source in ordered_sources}) == len(ordered_sources),
            "duplicate_query_source",
        )
        object.__setattr__(self, "query_sources", ordered_sources)

        for event in self.events:
            require(
                type(event) is CorporateActionEventEvidence and event.ticker == self.ticker,
                "event_security_mismatch",
            )
            require(
                self.query_start <= event.effective_date <= self.query_end,
                "event_outside_query_period",
            )
        ordered_events = tuple(
            sorted(
                self.events,
                key=lambda event: (
                    event.effective_date,
                    event.event_class,
                    event.fingerprint,
                ),
            )
        )
        require(
            len({event.fingerprint for event in ordered_events}) == len(ordered_events),
            "duplicate_corporate_action_event",
        )
        object.__setattr__(self, "events", ordered_events)
        require(bool(self.review_reference), "coverage_review_reference_required")
        require(
            self.version == "corporate_action_security_coverage_v1",
            "unsupported_coverage_version",
        )

        if self.historical_identity_verified:
            require(
                self.identity_effective_start <= date.fromisoformat(dart_evidence.REQUIRED_START)
                and self.identity_effective_end >= date.fromisoformat(dart_evidence.REQUIRED_END),
                "historical_identity_period_incomplete",
            )

    @property
    def status(self) -> str:
        return dart_evidence.corporate_status(
            historical_identity=self.historical_identity_verified,
            query_complete=self.query_complete,
            covered_classes=self.covered_classes,
            start=self.query_start.isoformat(),
            end=self.query_end.isoformat(),
            authoritative_complete_semantics=self.authoritative_complete_semantics,
            events=tuple(
                {
                    "effective_date": event.effective_date.isoformat(),
                    "source": event.source.to_dict(),
                }
                for event in self.events
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ticker": self.ticker,
            "security_identity": self.security_identity,
            "identity_source": self.identity_source.to_dict(),
            "identity_effective_start": self.identity_effective_start.isoformat(),
            "identity_effective_end": self.identity_effective_end.isoformat(),
            "historical_identity_verified": self.historical_identity_verified,
            "query_start": self.query_start.isoformat(),
            "query_end": self.query_end.isoformat(),
            "covered_classes": list(self.covered_classes),
            "query_complete": self.query_complete,
            "authoritative_complete_semantics": self.authoritative_complete_semantics,
            "query_sources": [source.to_dict() for source in self.query_sources],
            "events": [event.to_dict() for event in self.events],
            "review_reference": self.review_reference,
            "status": self.status,
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())


def summarize_corporate_action_coverage(
    universe: tuple[str, ...],
    records: tuple[CorporateActionSecurityCoverage, ...],
) -> FrozenJSON:
    """Summarize reviewed records without filling missing securities or events."""

    require(type(universe) is tuple and bool(universe), "corporate_action_universe_required")
    require(
        tuple(sorted(set(universe))) == universe,
        "corporate_action_universe_must_be_sorted_unique",
    )
    for ticker in universe:
        require(re.fullmatch(r"[0-9]{6}", ticker) is not None, "exact_ticker_required")

    by_ticker: dict[str, CorporateActionSecurityCoverage] = {}
    for record in records:
        require(type(record) is CorporateActionSecurityCoverage, "coverage_record_required")
        require(record.ticker in universe, "coverage_ticker_outside_universe")
        require(record.ticker not in by_ticker, "duplicate_security_coverage")
        by_ticker[record.ticker] = record

    statuses = {
        ticker: by_ticker[ticker].status if ticker in by_ticker else "MISSING_COVERAGE_RECORD"
        for ticker in universe
    }
    verified = sum(status in VERIFIED_STATUSES for status in statuses.values())
    historical_identity_verified = sum(
        record.historical_identity_verified for record in by_ticker.values()
    )
    event_count = sum(len(record.events) for record in by_ticker.values())
    missing = [ticker for ticker in universe if ticker not in by_ticker]
    blocked = [ticker for ticker, status in statuses.items() if status not in VERIFIED_STATUSES]
    blockers = []
    if historical_identity_verified != len(universe):
        blockers.append("BLOCKED_ON_HISTORICAL_IDENTITY")
    if verified != len(universe):
        blockers.append("BLOCKED_ON_CORPORATE_ACTION_EVIDENCE")

    return FrozenJSON.freeze(
        {
            "version": "corporate_action_final_coverage_v1",
            "required_start": dart_evidence.REQUIRED_START,
            "required_end": dart_evidence.REQUIRED_END,
            "universe_count": len(universe),
            "reviewed_security_count": len(by_ticker),
            "historical_identity_verified_count": historical_identity_verified,
            "complete_corporate_coverage_count": verified,
            "verified_economic_event_count": event_count,
            "missing_security_count": len(missing),
            "blocked_security_count": len(blocked),
            "missing_securities": missing,
            "blocked_securities": blocked,
            "statuses": statuses,
            "record_fingerprints": {
                ticker: by_ticker[ticker].fingerprint for ticker in sorted(by_ticker)
            },
            "blockers": blockers,
            "corporate_action_gate_verified": verified == len(universe),
            "prices_adjusted": False,
            "prediction_or_backtest_invoked": False,
        }
    )
