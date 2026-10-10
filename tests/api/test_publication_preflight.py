"""Read-only publication gate diagnostics do not create a prediction or approval."""

import hashlib
import json
from pathlib import Path

from donghak_stock_vision.api.publication_preflight import inspect_publication_inputs


def test_missing_inputs_report_explicit_blockers() -> None:
    report = inspect_publication_inputs(None, None)
    assert report.ready_for_manual_review is False
    assert "missing_operational_signal_export" in report.blockers
    assert "missing_independent_review_receipt" in report.blockers
    assert report.to_dict()["publicationAuthorized"] is False


def test_research_only_export_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "research.json"
    source.write_text('{"scope":"research","items":[]}', encoding="utf-8")
    report = inspect_publication_inputs(source, None)
    assert "invalid_operational_export_scope" in report.blockers


def test_matching_empty_export_is_only_ready_for_manual_review(tmp_path: Path) -> None:
    source = tmp_path / "signals.json"
    source.write_text('{"scope":"operational","items":[]}', encoding="utf-8")
    receipt = tmp_path / "review.json"
    receipt.write_text(
        json.dumps(
            {
                "schemaVersion": "approved_operational_signals_v1",
                "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "approved": True,
                "scope": "operational",
                "sourceMode": "point_in_time",
                "reviewer": "sample-reviewer",
                "reviewedAt": "2026-10-10T11:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    report = inspect_publication_inputs(source, receipt)
    assert report.ready_for_manual_review is True
    assert report.candidate_records == 0
    assert report.to_dict()["publicationAuthorized"] is False
    assert "independent_reviewer_signature_not_verified" in report.blockers
    assert not (tmp_path / "published.json").exists()
