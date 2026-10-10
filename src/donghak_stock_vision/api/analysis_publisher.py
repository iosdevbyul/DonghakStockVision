"""Fail-closed conversion of already approved operational signal records.

This is a publication boundary, not a model runner or approval authority.
"""
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.api.stock_analysis import (
    AnalysisPublication,
    PublishedAnalysisRecord,
    PublishedStockAnalysis,
)


class PublicationRejected(ValueError):
    """The source or its independent approval evidence is unsuitable."""


def publish_approved_signals(
    source: Path, approval: Path, destination: Path
) -> AnalysisPublication:
    """Publish previously registered PIT signals; never infer or self-approve."""
    if destination.exists():
        raise PublicationRejected("destination exists")
    try:
        raw = source.read_bytes()
        receipt: dict[str, Any] = json.loads(approval.read_text(encoding="utf-8"))
        signals: dict[str, Any] = json.loads(raw)
    except (OSError, ValueError, TypeError) as error:
        raise PublicationRejected("missing or invalid publication inputs") from error
    digest = hashlib.sha256(raw).hexdigest()
    if (
        receipt.get("schemaVersion") != "approved_operational_signals_v1"
        or receipt.get("sourceSha256") != digest
        or not isinstance(receipt.get("reviewer"), str)
        or not receipt["reviewer"].strip()
        or receipt.get("approved") is not True
        or receipt.get("sourceMode") != "point_in_time"
        or receipt.get("scope") != "operational"
        or not isinstance(receipt.get("reviewedAt"), str)
    ):
        raise PublicationRejected("independent approval required")
    try:
        reviewed = datetime.fromisoformat(receipt["reviewedAt"])
        if reviewed.utcoffset() is None:
            raise ValueError("review timezone required")
        if signals.get("scope") != "operational" or not isinstance(signals.get("items"), list):
            raise ValueError("not operational signal result")
        items = signals["items"]
        if len(items) > 100_000:
            raise ValueError("publication size limit")
        grouped: dict[str, list[PublishedAnalysisRecord]] = {}
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("malformed signal")
            if (
                item.get("mode") != "point_in_time"
                or item.get("data_origin") != "real"
                or item.get("input_status") != "ok"
                or item.get("operational_status") != "registered"
                or item.get("usage_restriction") != "pit_review_required"
                or item.get("quality_flags")
                or item.get("snapshot_id") is None
                or not isinstance(item.get("input_hash"), str)
                or len(item["input_hash"]) != 64
            ):
                raise ValueError("unapproved signal provenance")
            direction = item.get("signal_state")
            if direction not in ("up", "down") or item.get(f"{direction}_status") != "scored":
                raise ValueError("not an approved directional score")
            other = "down" if direction == "up" else "up"
            if item.get(f"{other}_score") is not None:
                raise ValueError("conflicting conditional scores")
            ticker = item["ticker"]
            model_version = item["model_version"]
            artifact_id = item["analysis_id"]
            if not all(isinstance(v, str) and v for v in (ticker, model_version, artifact_id)):
                raise ValueError("signal identifiers missing")
            if artifact_id in seen:
                raise ValueError("duplicate analysis")
            seen.add(artifact_id)
            anchor = datetime.fromisoformat(item["anchor_at"])
            analyzed = datetime.fromisoformat(item["analyzed_at"])
            if anchor.utcoffset() is None or analyzed.utcoffset() is None or analyzed > reviewed:
                raise ValueError("invalid analysis chronology")
            data_day = date.fromisoformat(item["last_trading_date"])
            if data_day > anchor.date() or anchor > analyzed:
                raise ValueError("future data or event chronology")
            record = PublishedAnalysisRecord(
                id=artifact_id,
                ticker=ticker,
                analyzedAt=analyzed,
                dataThrough=data_day.isoformat(),
                modelId=direction,
                modelVersion=model_version,
                direction=direction,
                score=item[f"{direction}_score"],
                validation="verified",
                explanation="Approved registered PIT signal; not an execution instruction.",
            )
            grouped.setdefault(ticker, []).append(record)
        publication = AnalysisPublication(
            schemaVersion="published_stock_analysis_v1",
            sources=tuple(
                PublishedStockAnalysis(ticker=ticker, records=tuple(grouped[ticker]))
                for ticker in sorted(grouped)
            ),
        )
        destination.write_text(
            publication.model_dump_json(indent=2) + "\n", encoding="utf-8", errors="strict"
        )
        return publication
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise PublicationRejected("publication validation failed") from error
