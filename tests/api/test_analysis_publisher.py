"""Offline publication guards: no approval, no scored output."""

import hashlib
import json
from pathlib import Path

import pytest

from donghak_stock_vision.api.analysis_publisher import (
    PublicationRejected,
    publish_approved_signals,
)


def _files(
    tmp_path: Path, signals: dict[str, object], *, approved: bool = True
) -> tuple[Path, Path, Path]:
    source = tmp_path / "signals.json"
    source.write_text(json.dumps(signals), encoding="utf-8")
    review = tmp_path / "approval.json"
    review.write_text(
        json.dumps(
            {
                "schemaVersion": "approved_operational_signals_v1",
                "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "reviewer": "reviewed-test",
                "approved": approved,
                "scope": "operational",
                "sourceMode": "point_in_time",
                "reviewedAt": "2026-10-02T08:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    return source, review, tmp_path / "published.json"


def test_empty_approved_export(tmp_path: Path) -> None:
    src, review, out = _files(tmp_path, {"scope": "operational", "items": []})
    result = publish_approved_signals(src, review, out)
    assert result.sources == ()
    assert json.loads(out.read_text())["sources"] == []


def test_missing_approval_never_creates_publication(tmp_path: Path) -> None:
    src, review, out = _files(tmp_path, {"scope": "operational", "items": []}, approved=False)
    with pytest.raises(PublicationRejected, match="independent approval"):
        publish_approved_signals(src, review, out)
    assert not out.exists()


def test_rejects_research_or_unregistered_signals(tmp_path: Path) -> None:
    src, review, out = _files(tmp_path, {"scope": "research", "items": []})
    with pytest.raises(PublicationRejected):
        publish_approved_signals(src, review, out)
    assert not out.exists()


def test_approved_operational_score_preserves_provenance(tmp_path: Path) -> None:
    item = {
        "mode": "point_in_time",
        "data_origin": "real",
        "input_status": "ok",
        "operational_status": "registered",
        "usage_restriction": "pit_review_required",
        "quality_flags": [],
        "snapshot_id": "reviewed-snapshot",
        "input_hash": "a" * 64,
        "signal_state": "up",
        "up_status": "scored",
        "up_score": 0.74,
        "down_score": None,
        "ticker": "005930",
        "model_version": "locked-model-v1",
        "analysis_id": "reviewed-analysis",
        "anchor_at": "2026-10-01T16:00:00+09:00",
        "analyzed_at": "2026-10-01T18:00:00+09:00",
        "last_trading_date": "2026-10-01",
    }
    src, review, out = _files(tmp_path, {"scope": "operational", "items": [item]})
    publication = publish_approved_signals(src, review, out)
    record = publication.sources[0].records[0]
    assert record.ticker == "005930"
    assert record.score == 0.74
    assert record.modelVersion == "locked-model-v1"
    assert record.dataThrough == "2026-10-01"
    assert record.validation == "verified"
    assert json.loads(out.read_text(encoding="utf-8"))["sources"][0]["records"][0]["score"] == 0.74
