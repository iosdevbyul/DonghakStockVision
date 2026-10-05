"""Immutable evidence packaging, without signals, model loading or simulation.

Official-source bytes and independently reviewed normalized documents are separate:
retrieving a URL does not establish complete calendar or corporate-action coverage.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.run_evidence import (
    BaselineRunEvidence,
    EvidenceCheck,
    check,
    reviewed_document,
    settlement_check,
)
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest

VERSION = "baseline_run_evidence_package_v1"
ANCHOR_START = date(2024, 7, 1)
ANCHOR_END = date(2025, 6, 30)
# feature_vector reads 11 bars including anchor, plus the preceding quality edge.
PRECEDING_CONTEXT_SESSIONS = 11
HOLDING_SESSIONS = 5
ACTION_CLASSES = (
    "split",
    "reverse_split",
    "stock_dividend_bonus",
    "rights_ex_rights",
    "merger",
    "spinoff",
    "security_identity_change",
    "share_consolidation",
    "listing_termination",
    "other_mechanical_discontinuity",
)


@dataclass(frozen=True)
class AnchorRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        require(type(self.start) is date and type(self.end) is date, "date_required")
        require(self.start <= self.end, "invalid_anchor_range")

    def contains_anchor(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_dict(self) -> dict[str, Any]:
        return dict(start=self.start.isoformat(), end=self.end.isoformat(), boundary="inclusive")

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def approval(self) -> EvidenceCheck:
        return check(
            "RUN_RANGE",
            () if (self.start, self.end) == (ANCHOR_START, ANCHOR_END) else ("unapproved_range",),
            **self.to_dict(),
            range_fingerprint=self.fingerprint,
            untouched_final_test=False,
            future_unbiased_confirmation_required=True,
        )


def derive_boundaries(
    anchors: AnchorRange,
    exclusions: tuple[date, ...],
    calendar: FrozenJSON | None,
    reviewed_pin: str | None,
) -> FrozenJSON:
    """Derive only from a reviewed, complete civil-date classification.

    Missing ticker observations cannot extend this bound silently: their continuity
    must be separately verified by calendar_check. Research exclusions never alter
    the settlement calendar.
    """
    data = reviewed_document(calendar, reviewed_pin)
    unresolved = dict(
        status="BLOCKED_ON_CALENDAR_EVIDENCE",
        preceding_context_sessions=PRECEDING_CONTEXT_SESSIONS,
        warmup_sessions=None,
        execution_tail=None,
        settlement_tail=None,
        settlement_mapping=None,
        calendar_fingerprint=reviewed_pin,
    )
    if data is None:
        return FrozenJSON.freeze(unresolved)
    try:
        days = data["days"]
        dates = sorted(date.fromisoformat(d) for d in days)
        require(bool(dates), "empty_calendar")
        cursor = dates[0]
        while cursor <= dates[-1]:
            state = days.get(cursor.isoformat())
            require(
                state in {"session", "weekend", "holiday", "extraordinary_closure"},
                "incomplete_calendar",
            )
            require((cursor.weekday() >= 5) == (state == "weekend"), "invalid_weekend")
            cursor += timedelta(days=1)
        for excluded in exclusions:
            if dates[0] <= excluded <= dates[-1]:
                require(days[excluded.isoformat()] == "session", "exclusion_is_not_holiday")
        sessions = [d for d in dates if days[d.isoformat()] == "session"]
        research = [d for d in sessions if d not in exclusions]
        require(anchors.start in research and anchors.end in research, "anchor_not_session")
        warmup = [d for d in research if d < anchors.start][-PRECEDING_CONTEXT_SESSIONS:]
        tail = [d for d in research if d > anchors.end][:HOLDING_SESSIONS]
        require(len(warmup) == PRECEDING_CONTEXT_SESSIONS, "incomplete_warmup_calendar")
        require(len(tail) == HOLDING_SESSIONS, "incomplete_execution_tail")
        # Conservative superset of possible entry/exit sessions; no intent generation.
        trades = tuple(d for d in research if anchors.start < d <= tail[-1])
        settlement = settlement_check(trades, calendar, reviewed_pin)
        require(not settlement.reasons, "incomplete_settlement_evidence")
        mapping = settlement.details.to_dict()["mapping"]
        final_settlement = date.fromisoformat(mapping[tail[-1].isoformat()])
        return FrozenJSON.freeze(
            dict(
                status="VERIFIED",
                preceding_context_sessions=PRECEDING_CONTEXT_SESSIONS,
                warmup_sessions=[d.isoformat() for d in warmup],
                execution_tail=[d.isoformat() for d in tail],
                settlement_tail=[
                    d.isoformat() for d in sessions if tail[-1] < d <= final_settlement
                ],
                settlement_mapping=mapping,
                settlement_mapping_fingerprint=digest(mapping),
                calendar_fingerprint=reviewed_pin,
                signal_anchors=anchors.to_dict(),
                tail_generates_signals=False,
                warmup_generates_signals=False,
            )
        )
    except (KeyError, ValueError, TypeError, IndexError):
        return FrozenJSON.freeze(unresolved)


def corporate_coverage(
    universe: tuple[str, ...],
    start: date,
    end: date,
    document: FrozenJSON | None,
    reviewed_pin: str | None,
) -> EvidenceCheck:
    """Require complete official query coverage and explicit security mappings.

    A reviewed query may contain events, including blocked events. Prices never
    enter this interface. Unsupported events remain a blocker, not adjusted bars.
    """
    data = reviewed_document(document, reviewed_pin)
    if data is None:
        return check("CORPORATE_ACTION_EVIDENCE", ["official_query_coverage_missing"])
    reasons = []
    if data.get("universe") != list(universe):
        reasons.append("incomplete_universe_coverage")
    if data.get("start") != start.isoformat() or data.get("end") != end.isoformat():
        reasons.append("incomplete_date_coverage")
    if data.get("action_classes") != list(ACTION_CLASSES):
        reasons.append("incomplete_event_class_coverage")
    if data.get("complete_query") is not True or data.get("all_pages_retrieved") is not True:
        reasons.append("incomplete_official_query")
    mappings = data.get("security_mapping", {})
    if set(mappings) != set(universe) or not all(mappings.values()):
        reasons.append("unverified_security_mapping")
    events = data.get("events")
    if not isinstance(events, list):
        reasons.append("missing_event_inventory")
    else:
        for event in events:
            if (
                not isinstance(event, dict)
                or event.get("ticker") not in universe
                or event.get("kind") not in ACTION_CLASSES
                or not event.get("official_reference")
                or not event.get("raw_sha256")
            ):
                reasons.append("invalid_event_evidence")
            # No event is presumed economically safe in unadjusted data.
            reasons.append("event_requires_existing_quality_resolution")
    if data.get("basis") != "official_query":
        reasons.append("price_movement_is_not_evidence")
    return check("CORPORATE_ACTION_EVIDENCE", reasons, document_fingerprint=reviewed_pin)


@dataclass(frozen=True)
class BaselineRunEvidencePackage:
    evidence: BaselineRunEvidence
    boundaries: FrozenJSON
    sources: FrozenJSON
    model_pin: FrozenJSON
    version: str = VERSION

    def __post_init__(self) -> None:
        require(self.version == VERSION, "unsupported_package_version")
        require(type(self.evidence) is BaselineRunEvidence, "evidence_required")
        for item in (self.boundaries, self.sources, self.model_pin):
            require(type(item) is FrozenJSON, "immutable_package_component_required")

    def to_dict(self) -> dict[str, Any]:
        evidence = self.evidence.to_dict()
        return dict(
            version=self.version,
            evidence=evidence,
            boundaries=self.boundaries.to_dict(),
            official_sources=self.sources.to_dict(),
            model_pin=self.model_pin.to_dict(),
            ready=evidence["ready"] and self.boundaries.to_dict().get("status") == "VERIFIED",
        )

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def report(self, *, retrieved_at: str, local_root: str) -> dict[str, Any]:
        """Transport/audit metadata never changes economic/evidence identity."""
        return dict(
            package=self.to_dict(),
            package_fingerprint=self.fingerprint,
            audit=dict(retrieved_at=retrieved_at, local_root=local_root),
        )
