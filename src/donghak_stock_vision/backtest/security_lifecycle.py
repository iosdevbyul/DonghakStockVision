"""Read-only, bounded security lifecycle evidence; never infer events from bars."""

import re
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum

from donghak_stock_vision.backtest.dart_evidence import source_valid
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.backtest.verify_dart import semantic_metadata
from donghak_stock_vision.data.learning import digest


class LifecycleReason(StrEnum):
    NOT_YET_LISTED = "NOT_YET_LISTED"
    DELISTED = "DELISTED"
    TRADING_SUSPENDED = "TRADING_SUSPENDED"
    RELISTING_TRANSITION = "RELISTING_TRANSITION"
    SECURITY_IDENTITY_TRANSITION = "SECURITY_IDENTITY_TRANSITION"
    CORPORATE_RESTRUCTURING = "CORPORATE_RESTRUCTURING"
    VERIFIED_NON_TRADING_SECURITY_STATE = "VERIFIED_NON_TRADING_SECURITY_STATE"


@dataclass(frozen=True)
class LifecycleEvidence:
    """A reviewed exact-security interval, bounded by official effective evidence.

    The interval is an explanation of non-observation, not an approval to trade
    or a certificate of whole-period security/corporate-action continuity.
    """

    ticker: str
    security_identity: str
    reason: LifecycleReason
    start: date
    end: date
    effective_date: date
    source: FrozenJSON
    identity_source: FrozenJSON
    exact_identity_verified: bool
    effective_date_verified: bool
    review_reference: str
    version: str = "security_lifecycle_interval_v1"

    def __post_init__(self) -> None:
        require(re.fullmatch(r"[0-9]{6}", self.ticker) is not None, "exact_ticker_required")
        require(bool(self.security_identity), "security_identity_required")
        require(isinstance(self.reason, LifecycleReason), "unknown_lifecycle_reason")
        require(self.start <= self.end, "invalid_lifecycle_interval")
        require(source_valid(self.source.to_dict()), "official_lifecycle_source_required")
        require(source_valid(self.identity_source.to_dict()), "official_identity_source_required")
        for field in ("source", "identity_source"):
            value = semantic_metadata(getattr(self, field).to_dict())
            require("?" not in value["url"], "canonical_endpoint_required")
            object.__setattr__(self, field, FrozenJSON.freeze(value))
        require(bool(self.review_reference), "review_reference_required")
        require(self.version == "security_lifecycle_interval_v1", "unsupported_lifecycle_version")
        if self.reason in (LifecycleReason.NOT_YET_LISTED, LifecycleReason.RELISTING_TRANSITION):
            require(self.end < self.effective_date, "listing_boundary_violation")
        elif self.reason == LifecycleReason.DELISTED:
            require(self.start >= self.effective_date, "delisting_boundary_violation")
        elif self.reason == LifecycleReason.TRADING_SUSPENDED:
            require(self.start == self.effective_date, "suspension_boundary_violation")

    @property
    def verified(self) -> bool:
        return self.exact_identity_verified and self.effective_date_verified

    @property
    def fingerprint(self) -> str:
        value = asdict(self)
        for key in ("start", "end", "effective_date"):
            value[key] = value[key].isoformat()
        value["source"] = self.source.to_dict()
        value["identity_source"] = self.identity_source.to_dict()
        return digest(value)


def reconcile_lifecycle(missing: FrozenJSON, evidence: tuple[LifecycleEvidence, ...]) -> FrozenJSON:
    """Consume an already frozen gap inventory. Never read bars or extend intervals."""
    rows = missing.to_dict()
    output = []
    seen = set()
    for ticker, sessions in sorted(rows.items()):
        require(re.fullmatch(r"[0-9]{6}", ticker) is not None, "exact_ticker_required")
        require(sessions == sorted(set(sessions)), "invalid_missing_session_order")
        intervals = {e.fingerprint: e for e in evidence if e.ticker == ticker and e.verified}
        for day in sessions:
            parsed = date.fromisoformat(day)
            matches = [e for e in intervals.values() if e.start <= parsed <= e.end]
            # Multiple overlapping claims need an explicit review, even for the same reason.
            match = matches[0] if len(matches) == 1 else None
            output.append(
                dict(
                    ticker=ticker,
                    date=day,
                    verified=match is not None,
                    reason=match.reason.value if match else "UNEXPLAINED_DATA_GAP",
                    status="VERIFIED" if match else "BLOCKED",
                    blocker="conflicting_lifecycle_intervals" if len(matches) > 1 else None,
                    evidence_fingerprint=match.fingerprint if match else None,
                    source=match.source.to_dict() if match else None,
                    security_identity=match.security_identity if match else None,
                )
            )
            if not match:
                seen.add(ticker)
    explained = sum(row["verified"] for row in output)
    return FrozenJSON.freeze(
        dict(
            version="security_lifecycle_reconciliation_v1",
            input_gap_fingerprint=missing.identifier,
            sessions=output,
            total=len(output),
            explained=explained,
            unexplained=len(output) - explained,
            unresolved_securities=sorted(seen),
            status="VERIFIED" if output and explained == len(output) else "BLOCKED",
            bar_absence_used_as_evidence=False,
            price_adjustment_performed=False,
        )
    )
