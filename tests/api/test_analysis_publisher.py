"""Offline publication guards: no approval, no scored output."""

import hashlib
import json
from pathlib import Path

import pytest

from donghak_stock_vision.api.analysis_publisher import (
    PublicationRejected,
    publish_approved_signals,
)


def _files(tmp_path: Path, signals: dict[str, object], *, approved: bool = True) -> tuple[Path, Path, Path]:
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
