"""Read-only display metadata; never historical identity or PIT approval.

The v1 compiler supports exact DART corporation identities and existing KIND
boundary references. It has no current security-name/type authority adapter.
"""

import gzip
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlparse

from pydantic import Field, ValidationError, model_validator

from donghak_stock_vision.api.exporter import (
    _english_name,
    load_artifact,
    snapshot_bytes,
)
from donghak_stock_vision.api.models import FrozenModel, StockSummary
from donghak_stock_vision.api.service import ResearchAPIReader
from donghak_stock_vision.api.snapshot import ResearchAPISnapshot, SnapshotReader
from donghak_stock_vision.backtest.dart_evidence import corporation_mapping
from donghak_stock_vision.data.learning import canonical, digest

Ticker = Annotated[str, Field(pattern=r"^[0-9A-Z]{6}$")]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MetadataError(ValueError):
    """Metadata is unavailable or cannot be reproduced from its declared sources."""


class EvidenceReference(FrozenModel):
    evidenceId: str
    publisher: Literal["OpenDART", "KRX KIND"]
    url: str
    rawSha256: Fingerprint
    artifactFingerprint: Fingerprint
    documentReference: str
    verifiedAsOf: date | None = None
    verifiedAsOfUnavailableReason: Literal["source_verification_time_not_recorded"] = (
        "source_verification_time_not_recorded"
    )


class CorporationMetadata(FrozenModel):
    corporationId: Annotated[str, Field(pattern=r"^DART:[0-9]{8}$")]
    koreanName: str
    englishName: str | None
    nameLanguages: tuple[Literal["ko", "en"], ...]
    verification: Literal["verified_in_source_snapshot"] = "verified_in_source_snapshot"
    sourceModifiedDate: date | None
    verifiedAsOf: date | None = None
    evidenceReference: str
    temporalScope: Literal["current_source_snapshot_not_historical"] = (
        "current_source_snapshot_not_historical"
    )


class SecurityMetadata(FrozenModel):
    ticker: Ticker
    securityName: str | None = None
    securityNameEnglish: str | None = None
    nameEvidenceReference: str | None = None
    nameStatus: Literal["unavailable", "verified_in_source_snapshot"] = "unavailable"
    corporationId: str | None
    corporationRelationship: Literal["verified_exact_dart_stock_code", "unavailable"]
    relationshipEvidenceReference: str | None
    securityType: Literal["unknown", "common", "preferred", "other"] = "unknown"
    typeEvidenceReference: str | None = None
    market: Literal["KOSPI"]
    marketBasis: Literal["research_snapshot_scope_not_current_listing"] = (
        "research_snapshot_scope_not_current_listing"
    )
    verifiedAsOf: date | None = None
    unavailableReasons: tuple[str, ...]
    historicalIdentityVerified: Literal[False] = False
    temporalScope: Literal["current_display_metadata_only"] = "current_display_metadata_only"

    @model_validator(mode="after")
    def evidence_required(self) -> Self:
        named = self.securityName is not None or self.securityNameEnglish is not None
        if named != (self.nameStatus == "verified_in_source_snapshot"):
            raise ValueError("security_name_verification_conflict")
        if named != (self.nameEvidenceReference is not None):
            raise ValueError("security_name_evidence_required")
        if (self.securityType != "unknown") != (self.typeEvidenceReference is not None):
            raise ValueError("security_type_evidence_required")
        linked = self.corporationId is not None
        if linked != (self.corporationRelationship == "verified_exact_dart_stock_code"):
            raise ValueError("corporation_relationship_conflict")
        if linked != (self.relationshipEvidenceReference is not None):
            raise ValueError("corporation_relationship_evidence_required")
        return self


class HistoricalBoundaryReference(FrozenModel):
    ticker: Ticker
    securityIdentity: str
    effectiveBoundary: date
    evidenceReference: str
    scope: Literal["exact_boundary_only_not_current_name_or_full_period"] = (
        "exact_boundary_only_not_current_name_or_full_period"
    )


class SecurityMetadataSnapshot(FrozenModel):
    version: Literal["security_metadata_v1"] = "security_metadata_v1"
    researchSnapshotSha256: Fingerprint
    legacyStocksFingerprint: Fingerprint
    sources: tuple[EvidenceReference, ...]
    corporations: tuple[CorporationMetadata, ...]
    securities: tuple[SecurityMetadata, ...]
    historicalBoundaries: tuple[HistoricalBoundaryReference, ...]
    limitations: tuple[str, ...]
    pitVerified: Literal[False] = False
    operationalQualificationChanged: Literal[False] = False

    @model_validator(mode="after")
    def identifiers(self) -> Self:
        for values in (
            tuple(s.ticker for s in self.securities),
            tuple(c.corporationId for c in self.corporations),
            tuple(s.evidenceId for s in self.sources),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("metadata_identifiers_not_unique_and_sorted")
        corp_ids = {c.corporationId for c in self.corporations}
        refs = {s.evidenceId for s in self.sources}
        tickers = {s.ticker for s in self.securities}
        for security in self.securities:
            if security.corporationId is not None and security.corporationId not in corp_ids:
                raise ValueError("metadata_corporation_reference_missing")
            for ref in (
                security.relationshipEvidenceReference,
                security.nameEvidenceReference,
                security.typeEvidenceReference,
            ):
                if ref is not None and ref not in refs:
                    raise ValueError("metadata_evidence_reference_missing")
        if any(c.evidenceReference not in refs for c in self.corporations):
            raise ValueError("metadata_evidence_reference_missing")
        keys = tuple(
            (b.ticker, b.effectiveBoundary, b.evidenceReference) for b in self.historicalBoundaries
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("metadata_boundaries_not_unique_and_sorted")
        if any(
            b.ticker not in tickers or b.evidenceReference not in refs
            for b in self.historicalBoundaries
        ):
            raise ValueError("metadata_boundary_reference_missing")
        return self


def legacy_fingerprint(stocks: tuple[StockSummary, ...]) -> str:
    return digest([s.model_dump(mode="json") for s in stocks])


def fingerprint(snapshot: SecurityMetadataSnapshot) -> str:
    return digest(snapshot.model_dump(mode="json"))


def metadata_bytes(snapshot: SecurityMetadataSnapshot) -> bytes:
    return (
        canonical(
            {"artifact": snapshot.model_dump(mode="json"), "fingerprint": fingerprint(snapshot)}
        )
        + "\n"
    ).encode()


def build_metadata(root: Path, research: ResearchAPISnapshot) -> SecurityMetadataSnapshot:
    """Reconcile exact raw DART evidence, without corporation/security name fallback."""
    dart = root / "docs/evidence/baseline-dart"
    loaded = load_artifact(dart / "artifacts/dart-corporation-mapping.json")
    rows = loaded.artifact["securities"]
    stocks = SnapshotReader(research).stocks()
    universe = tuple(s.ticker for s in stocks)
    if len(universe) != research.coverage.universe:
        raise MetadataError("research_universe_count_mismatch")
    if universe != tuple(sorted(set(universe))):
        raise MetadataError("research_tickers_not_unique_and_sorted")
    if any(re.fullmatch(r"[0-9A-Z]{6}", ticker) is None for ticker in universe):
        raise MetadataError("invalid_metadata_ticker")
    raw = (dart / "raw/corpCode.zip").read_bytes()
    if hashlib.sha256(raw).hexdigest() != loaded.artifact["source_sha256"]:
        raise MetadataError("dart_raw_fingerprint_mismatch")
    rebuilt = corporation_mapping(raw, universe).to_dict()
    if rebuilt != loaded.artifact:
        raise MetadataError("dart_mapping_replay_mismatch")
    if research.coverage.currentIdentityVerified != sum(
        row["current_status"] == "CURRENT_IDENTITY_VERIFIED" for row in rows
    ):
        raise MetadataError("research_corporation_coverage_mismatch")
    by_ticker = {row["ticker"]: row for row in rows}
    dart_ref = "dart:corpCode"
    refs = [
        EvidenceReference(
            evidenceId=dart_ref,
            publisher="OpenDART",
            url="https://opendart.fss.or.kr/api/corpCode.xml",
            rawSha256=loaded.artifact["source_sha256"],
            artifactFingerprint=loaded.fingerprint,
            documentReference="corpCode.zip",
        )
    ]
    corporations: dict[str, CorporationMetadata] = {}
    securities = []
    for stock in stocks:
        row = by_ticker[stock.ticker]
        legacy = _english_name(row)
        if stock.name != legacy:
            raise MetadataError("legacy_stock_name_mismatch")
        linked = row["current_status"] == "CURRENT_IDENTITY_VERIFIED"
        corp_id = None
        reasons = [
            "current_security_name_evidence_unavailable",
            "current_security_type_evidence_unavailable",
            "source_verification_time_not_recorded",
        ]
        if linked:
            match = row["matches"][0]
            corp_id = "DART:" + match["corp_code"]
            modified = match.get("modify_date")
            corporation = CorporationMetadata(
                corporationId=corp_id,
                koreanName=match["corp_name"],
                englishName=legacy,
                nameLanguages=("ko", "en") if legacy else ("ko",),
                sourceModifiedDate=date.fromisoformat(modified) if modified else None,
                evidenceReference=dart_ref,
            )
            if corp_id in corporations and corporations[corp_id] != corporation:
                raise MetadataError("conflicting_corporation_metadata")
            corporations[corp_id] = corporation
        else:
            reasons.append("exact_dart_corporation_mapping_unavailable")
        securities.append(
            SecurityMetadata(
                ticker=stock.ticker,
                corporationId=corp_id,
                corporationRelationship="verified_exact_dart_stock_code"
                if linked
                else "unavailable",
                relationshipEvidenceReference=dart_ref if linked else None,
                market=stock.market,
                unavailableReasons=tuple(reasons),
            )
        )
    boundaries = []
    lifecycle = root / "docs/evidence/baseline-security-lifecycle"
    historical = load_artifact(lifecycle / "artifacts/historical-security-identities.json")
    if historical.artifact["current_corporation_mapping_fingerprint"] != loaded.fingerprint:
        raise MetadataError("historical_corporation_parent_mismatch")
    for boundary in historical.artifact["boundary_identities"]:
        ticker = boundary["ticker"]
        if ticker not in by_ticker:
            raise MetadataError("historical_boundary_outside_universe")
        source = boundary["source"]
        doc = source["document_reference"]
        if (
            boundary["status"] != "BOUNDARY_IDENTITY_VERIFIED"
            or boundary["whole_period_continuity_verified"] is not False
            or re.fullmatch(r"[A-Za-z0-9-]+", doc) is None
            or urlparse(source["url"]).hostname != "kind.krx.co.kr"
            or urlparse(source["url"]).scheme != "https"
        ):
            raise MetadataError("invalid_historical_boundary_scope")
        content = gzip.decompress((lifecycle / "sources" / (doc + ".html.gz")).read_bytes())
        if hashlib.sha256(content).hexdigest() != source["raw_sha256"]:
            raise MetadataError("historical_raw_fingerprint_mismatch")
        ref = "kind:" + doc
        refs.append(
            EvidenceReference(
                evidenceId=ref,
                publisher="KRX KIND",
                url=source["url"],
                rawSha256=source["raw_sha256"],
                artifactFingerprint=historical.fingerprint,
                documentReference=doc,
            )
        )
        boundaries.append(
            HistoricalBoundaryReference(
                ticker=ticker,
                securityIdentity=boundary["security_identity"],
                effectiveBoundary=boundary["effective_boundary"],
                evidenceReference=ref,
            )
        )
    return SecurityMetadataSnapshot(
        researchSnapshotSha256=hashlib.sha256(snapshot_bytes(research)).hexdigest(),
        legacyStocksFingerprint=legacy_fingerprint(stocks),
        sources=tuple(sorted(refs, key=lambda r: r.evidenceId)),
        corporations=tuple(corporations[k] for k in sorted(corporations)),
        securities=tuple(securities),
        historicalBoundaries=tuple(
            sorted(boundaries, key=lambda b: (b.ticker, b.effectiveBoundary, b.evidenceReference))
        ),
        limitations=(
            "corporation_names_are_not_security_names",
            "no_current_security_master_adapter",
            "dart_modify_date_is_not_verification_or_effective_time",
            "source_snapshot_is_not_current_listing_or_historical_continuity",
            "boundary_evidence_does_not_supply_current_security_names_or_types",
            "checksums_do_not_independently_establish_official_authenticity",
            "no_pit_or_operational_qualification_change",
        ),
    )


class MetadataEnvelope(FrozenModel):
    artifact: SecurityMetadataSnapshot
    fingerprint: Fingerprint


class SecurityMetadataDetail(FrozenModel):
    version: Literal["security_metadata_v1"] = "security_metadata_v1"
    fingerprint: Fingerprint
    security: SecurityMetadata
    corporation: CorporationMetadata | None
    sources: tuple[EvidenceReference, ...]
    historicalBoundaries: tuple[HistoricalBoundaryReference, ...]


class SecurityMetadataReader:
    """Revalidated once at startup, indexed reads only afterward."""

    def __init__(
        self, metadata: SecurityMetadataSnapshot, *, root: Path, research: ResearchAPISnapshot
    ) -> None:
        if metadata != build_metadata(root, research):
            raise MetadataError("metadata_source_replay_mismatch")
        self.snapshot = metadata
        self.envelope = MetadataEnvelope(artifact=metadata, fingerprint=fingerprint(metadata))
        self._securities = {s.ticker: s for s in metadata.securities}
        self._corporations = {c.corporationId: c for c in metadata.corporations}
        self._boundaries = {
            ticker: tuple(b for b in metadata.historicalBoundaries if b.ticker == ticker)
            for ticker in self._securities
        }

    @classmethod
    def from_path(
        cls, path: Path, *, expected_fingerprint: str, root: Path, research: ResearchAPISnapshot
    ) -> Self:
        try:
            envelope = MetadataEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise MetadataError("invalid_security_metadata_envelope") from error
        if (
            envelope.fingerprint != expected_fingerprint
            or fingerprint(envelope.artifact) != expected_fingerprint
        ):
            raise MetadataError("security_metadata_fingerprint_mismatch")
        return cls(envelope.artifact, root=root, research=research)

    def validate_research_reader(self, reader: ResearchAPIReader | None) -> None:
        if (
            reader is None
            or legacy_fingerprint(reader.stocks()) != self.snapshot.legacyStocksFingerprint
        ):
            raise MetadataError("metadata_research_reader_mismatch")

    def detail(self, ticker: str) -> SecurityMetadataDetail | None:
        security = self._securities.get(ticker)
        if security is None:
            return None
        boundaries = self._boundaries[ticker]
        refs = {b.evidenceReference for b in boundaries}
        if security.relationshipEvidenceReference:
            refs.add(security.relationshipEvidenceReference)
        return SecurityMetadataDetail(
            fingerprint=self.envelope.fingerprint,
            security=security,
            corporation=self._corporations.get(security.corporationId or ""),
            sources=tuple(s for s in self.snapshot.sources if s.evidenceId in refs),
            historicalBoundaries=boundaries,
        )


def diagnose(metadata: SecurityMetadataSnapshot, research: ResearchAPISnapshot) -> dict[str, Any]:
    if metadata.legacyStocksFingerprint != legacy_fingerprint(SnapshotReader(research).stocks()):
        raise MetadataError("metadata_research_reader_mismatch")
    securities = {s.ticker: s for s in metadata.securities}
    missing = [s for s in research.stocks if s.name is None]
    boundary_tickers = {b.ticker for b in metadata.historicalBoundaries}
    return {
        "version": "security_metadata_diagnostics_v1",
        "metadataFingerprint": fingerprint(metadata),
        "universe": len(securities),
        "legacyNamed": len(research.stocks) - len(missing),
        "legacyMissing": len(missing),
        "missingWithoutExactCorporationMapping": sum(
            securities[s.ticker].corporationId is None for s in missing
        ),
        "missingEnglishNameWithExactMapping": sum(
            securities[s.ticker].corporationId is not None for s in missing
        ),
        "currentSecurityNamesVerified": sum(
            s.nameStatus == "verified_in_source_snapshot" for s in securities.values()
        ),
        "securityTypes": {
            kind: sum(s.securityType == kind for s in securities.values())
            for kind in ("common", "preferred", "other", "unknown")
        },
        "missingWithHistoricalBoundaryOnly": sum(s.ticker in boundary_tickers for s in missing),
        "missing": [
            {
                "ticker": s.ticker,
                "reason": "exact_dart_mapping_unavailable"
                if securities[s.ticker].corporationId is None
                else "dart_english_name_unavailable",
                "securityType": securities[s.ticker].securityType,
                "historicalBoundaryOnly": s.ticker in boundary_tickers,
            }
            for s in missing
        ],
    }


def export_metadata(root: Path, research: ResearchAPISnapshot, output: Path) -> str:
    metadata = build_metadata(root, research)
    try:
        with output.open("xb") as stream:
            stream.write(metadata_bytes(metadata))
    except FileExistsError as error:
        raise MetadataError("metadata_output_already_exists") from error
    return fingerprint(metadata)
