"""Read-only preflight of operational inference publication prerequisites.

This is an advisory diagnostic, NEVER an approval or publishing authority.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PublicationPreflight:
    ready_for_manual_review: bool
    source_sha256: str | None
    candidate_records: int
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "readyForManualReview": self.ready_for_manual_review,
            "sourceSha256": self.source_sha256,
            "candidateRecords": self.candidate_records,
            "blockers": list(self.blockers),
            "publicationAuthorized": False,
        }


def inspect_publication_inputs(
    source: Path | None,
    approval: Path | None,
) -> PublicationPreflight:
    """No writes, no model loading, no score generation, and no approval inference."""
    blockers: list[str] = []
    digest: str | None = None
    count = 0
    if source is None:
        blockers.append("missing_operational_signal_export")
    else:
        try:
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            payload = json.loads(data)
            if (
                not isinstance(payload, dict)
                or payload.get("scope") != "operational"
                or not isinstance(payload.get("items"), list)
            ):
                blockers.append("invalid_operational_export_scope")
            else:
                items = payload["items"]
                count = len(items)
                if count > 100_000:
                    blockers.append("publication_size_exceeded")
                for item in items:
                    if not isinstance(item, dict):
                        blockers.append("malformed_signal_record")
                        break
                    if (
                        item.get("mode") != "point_in_time"
                        or item.get("data_origin") != "real"
                        or item.get("operational_status") != "registered"
                        or item.get("usage_restriction") != "pit_review_required"
                        or item.get("input_status") != "ok"
                        or item.get("quality_flags")
                        or not item.get("snapshot_id")
                    ):
                        blockers.append("unregistered_or_unverified_pit_signal")
                        break
        except (OSError, ValueError, TypeError):
            blockers.append("unreadable_operational_export")

    if approval is None:
        blockers.append("missing_independent_review_receipt")
    else:
        try:
            receipt = json.loads(approval.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or (
                receipt.get("schemaVersion") != "approved_operational_signals_v1"
                or receipt.get("sourceSha256") != digest
                or receipt.get("approved") is not True
                or receipt.get("scope") != "operational"
                or receipt.get("sourceMode") != "point_in_time"
                or not isinstance(receipt.get("reviewer"), str)
                or not receipt["reviewer"].strip()
            ):
                blockers.append("review_receipt_mismatch")
            else:
                reviewed = datetime.fromisoformat(receipt["reviewedAt"])
                if reviewed.utcoffset() is None:
                    blockers.append("review_timestamp_missing_timezone")
        except (OSError, ValueError, KeyError, TypeError):
            blockers.append("unreadable_review_receipt")

    # An ordinary JSON reviewer receipt asserts approval but cannot prove reviewer identity.
    blockers.append("independent_reviewer_signature_not_verified")
    return PublicationPreflight(
        ready_for_manual_review=not (
            set(blockers) - {"independent_reviewer_signature_not_verified"}
        ),
        source_sha256=digest,
        candidate_records=count,
        blockers=tuple(dict.fromkeys(blockers)),
    )
