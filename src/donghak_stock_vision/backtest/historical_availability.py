"""Immutable, explicitly unverified historical timestamp claims; no approval authority."""

from dataclasses import dataclass
from datetime import date, datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import integer, require, sha, text, utc
from donghak_stock_vision.trading.domain import Instrument
from donghak_stock_vision.trading.paper_types import wire

TIMESTAMP_KINDS = (
    "publication",
    "correction",
    "first_observed",
    "provider_ingestion",
    "local_retrieval",
    "artifact_capture",
    "authenticated_timestamp",
)


@dataclass(frozen=True)
class AvailabilityTimestampClaim:
    kind: str
    at: datetime
    origin_reference: str

    def __post_init__(self) -> None:
        require(self.kind in TIMESTAMP_KINDS, "unsupported_timestamp_kind")
        object.__setattr__(self, "at", utc(self.at))
        require(len(text(self.origin_reference)) <= 512, "timestamp_reference_bound")


@dataclass(frozen=True)
class HistoricalRevisionClaim:
    sequence: int
    source_hash: str
    previous_hash: str | None
    corrected_at: datetime | None

    def __post_init__(self) -> None:
        require(0 < integer(self.sequence) <= 8, "revision_sequence_bound")
        sha(self.source_hash)
        if self.previous_hash is not None:
            sha(self.previous_hash)
        if self.corrected_at is not None:
            object.__setattr__(self, "corrected_at", utc(self.corrected_at))


@dataclass(frozen=True)
class HistoricalAvailabilityClaim:
    """Declared external evidence, never an authenticated timestamp by construction."""

    instrument: Instrument
    trading_date: date
    bar_hash: str
    raw_id: int
    raw_hash: str
    document_identity: str
    publisher_id: str | None
    custodian_id: str | None
    timestamps: tuple[AvailabilityTimestampClaim, ...]
    revisions: tuple[HistoricalRevisionClaim, ...]
    capture_reference: str | None
    trust_root_reference: str | None

    def __post_init__(self) -> None:
        require(type(self.instrument) is Instrument, "instrument_owner_required")
        self.instrument.__post_init__()
        require(type(self.trading_date) is date, "trading_date_required")
        sha(self.bar_hash)
        sha(self.raw_hash)
        require(integer(self.raw_id) > 0, "raw_identity_required")
        require(
            self.document_identity == f"raw_pages/{self.raw_id}/sha256/{self.raw_hash}",
            "source_document_identity_mismatch",
        )
        for value in (
            self.publisher_id,
            self.custodian_id,
            self.capture_reference,
            self.trust_root_reference,
        ):
            require(value is None or len(text(value)) <= 512, "evidence_reference_bound")
        require(
            type(self.timestamps) is tuple and len(self.timestamps) <= 7, "timestamp_claim_bound"
        )
        require(
            all(type(t) is AvailabilityTimestampClaim for t in self.timestamps),
            "timestamp_owner_required",
        )
        require(
            len({t.kind for t in self.timestamps}) == len(self.timestamps),
            "duplicate_timestamp_kind",
        )
        for timestamp in self.timestamps:
            timestamp.__post_init__()
        times = {t.kind: t.at for t in self.timestamps}
        if "publication" in times and "correction" in times:
            require(
                times["publication"] <= times["correction"], "conflicting_publication_correction"
            )
        require(type(self.revisions) is tuple and len(self.revisions) <= 8, "revision_claim_bound")
        for index, revision in enumerate(self.revisions):
            require(type(revision) is HistoricalRevisionClaim, "revision_owner_required")
            revision.__post_init__()
            require(revision.sequence == index + 1, "revision_sequence_mismatch")
            previous = self.revisions[index - 1] if index else None
            require(
                revision.previous_hash == (previous.source_hash if previous else None),
                "revision_link_mismatch",
            )
            if previous and previous.corrected_at is not None and revision.corrected_at is not None:
                require(
                    previous.corrected_at < revision.corrected_at, "conflicting_revision_chronology"
                )
        if self.revisions:
            require(
                self.revisions[-1].source_hash == self.raw_hash, "revision_head_not_original_source"
            )

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "historical_availability_claim_v1",
                **wire(self),
                "assurance": "declarations_not_authenticated_availability",
            }
        )


@dataclass(frozen=True)
class PinnedAvailabilityClaim:
    claim: HistoricalAvailabilityClaim
    trusted_document_hash: str
    expected_publisher: str | None
    expected_custodian: str | None
    acknowledgement: bytes | None = None
    exported_receipt: bytes | None = None
    trusted_acknowledgement_hash: str | None = None
    trusted_receipt_hash: str | None = None
