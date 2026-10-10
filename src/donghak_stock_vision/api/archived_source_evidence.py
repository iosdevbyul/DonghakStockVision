"""Read-only adapter from a stored raw page to the source collection contract."""

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from donghak_stock_vision.api.source_collection_evidence import SourceCollectionEvidence


@dataclass(frozen=True)
class ArchivedEvidenceDocument:
    evidence_bytes: bytes
    sha256: str


def build_archived_source_evidence(
    connection: sqlite3.Connection,
    *,
    raw_id: int,
    snapshot_id: str,
    collector_name: str,
    collector_version: str,
    observation_kind: Literal["initial", "revision"],
    previous_raw_sha256: str | None = None,
) -> ArchivedEvidenceDocument:
    """Use an existing archive row; never infer authenticity or revision completeness."""
    if raw_id < 1:
        raise ValueError("raw ID must be positive")
    record = connection.execute(
        "SELECT run_id, provider, ticker, source_date, collected_at, sha256, body "
        "FROM raw_pages WHERE id=?",
        (raw_id,),
    ).fetchone()
    if record is None:
        raise ValueError("archived raw page not found")
    run_id, provider, scope, source_date, collected_at, recorded_sha, body = record
    if not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != recorded_sha:
        raise ValueError("archived raw checksum mismatch")
    try:
        observed = datetime.fromisoformat(collected_at)
        source_day = date.fromisoformat(source_date) if source_date is not None else None
        evidence = SourceCollectionEvidence(
            schemaVersion="source_collection_evidence_v1",
            provider=provider,
            scope=scope,
            sourceDate=source_day,
            collectedAt=observed,
            runId=run_id,
            collectorName=collector_name,
            collectorVersion=collector_version,
            rawSha256=recorded_sha,
            observationKind=observation_kind,
            previousRawSha256=previous_raw_sha256,
            snapshotId=snapshot_id,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("invalid archived collection metadata") from error
    encoded = evidence.model_dump_json().encode("utf-8")
    return ArchivedEvidenceDocument(encoded, hashlib.sha256(encoded).hexdigest())
