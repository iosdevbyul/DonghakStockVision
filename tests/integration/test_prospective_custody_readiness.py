"""Custody gaps from original synthetic owners; no fabricated authentication."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_prediction_binding import AS_OF, future_capture
from tests.integration.test_prospective_evaluation_consumer import sources
from tests.integration.test_prospective_prediction import RECORDED

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.prospective_custody_readiness import (
    GAPS,
    audit_custody,
    verify_custody,
)
from donghak_stock_vision.storage.prospective_evidence_audit import EvidenceCase


def test_replayable_fixture_is_still_unresolved_and_unchanged(tmp_path: Path) -> None:
    s = sources(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch("httpx.Client.send", side_effect=AssertionError("no network")):
        report = audit_custody(s, AS_OF)
        d = report.to_dict()
        verify_custody(report, report.identifier, d["references_pin"], replace(s), AS_OF)
    assert d["version"] == "prospective_custody_readiness_v1" and d["state"] == "unresolved"
    assert d["counts"]["locally_verified_future_pairs"] == 1
    assert d["counts"]["independently_authenticated_cases"] == 0
    assert set(d["cases"][0]["reasons"]) == set(GAPS)
    assert all(count == 1 for count in d["reason_counts"].values())
    for flag in (
        "independently_authenticated_chronology",
        "independent_custody_references_verified",
        "genuine_forward_evidence",
        "pit_qualified",
        "performance_approved",
        "execution_authorized",
    ):
        assert d[flag] is False
    assert audit_custody(replace(s), AS_OF) == FrozenJSON(report.payload_json)
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert '"metrics"' not in report.payload_json and '"correct"' not in report.payload_json


@pytest.mark.parametrize("kind", ["missing", "pending", "tampered", "early", "late"])
def test_future_provenance_and_chronology_gaps(tmp_path: Path, kind: str) -> None:
    s = sources(tmp_path, future=False)
    case = s.cases[0]
    at = AS_OF
    if kind == "pending":
        at = RECORDED
    if kind in {"early", "late", "tampered"}:
        clock = (
            AS_OF - timedelta(hours=2)
            if kind == "early"
            else AS_OF + timedelta(days=4)
            if kind == "late"
            else AS_OF - timedelta(seconds=3)
        )
        f = future_capture(case.prediction, tmp_path / "future", at=clock)
        if kind == "late":
            at = clock + timedelta(seconds=4)
        if kind == "tampered":
            with (f.capture_root / DB).open("ab") as stream:
                stream.write(b"tampered")
        s = replace(s, cases=(EvidenceCase(case.prediction, f),))
    d = audit_custody(s, at).to_dict()
    assert "missing_or_unverifiable_future_observation_provenance" in d["cases"][0]["reasons"]
    if kind in {"early", "late"}:
        assert "inconsistent_chronology" in d["cases"][0]["reasons"]
    assert d["cases"][0]["local_future_timestamps"] is None
    assert d["state"] == "unresolved"


@pytest.mark.parametrize("kind", ["duplicate", "wrong_pin", "too_many", "version", "chronology"])
def test_invalid_original_input_is_rejected(tmp_path: Path, kind: str) -> None:
    s = sources(tmp_path)
    if kind == "duplicate":
        s = replace(s, cases=s.cases * 2)
    elif kind == "too_many":
        s = replace(s, cases=s.cases * 33)
    elif kind == "wrong_pin":
        s = replace(s, policy_pin="0" * 64)
    else:
        d = s.policy.to_dict()
        if kind == "version":
            d["version"] = "unsupported"
        else:
            d["declared_at"] = (RECORDED + timedelta(seconds=1)).isoformat()
        p = FrozenJSON.freeze(d)
        s = replace(s, policy=p, policy_pin=p.identifier)
    with pytest.raises(ValueError):
        audit_custody(s, AS_OF)


def test_altered_original_references_and_rehashed_report_rejected(tmp_path: Path) -> None:
    s = sources(tmp_path)
    report = audit_custody(s, AS_OF)
    d = report.to_dict()
    d["independently_authenticated_chronology"] = True
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_custody(forged, forged.identifier, d["references_pin"], s, AS_OF)
    d = report.to_dict()
    d["cases"][0]["recorded_at"] = "2025-07-09T00:00:00+00:00"
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_custody(forged, forged.identifier, d["references_pin"], s, AS_OF)
    bad = replace(s.cases[0].prediction, request_pin="0" * 64)
    with pytest.raises(ValueError):
        verify_custody(
            report,
            report.identifier,
            d["references_pin"],
            replace(s, cases=(EvidenceCase(bad),)),
            AS_OF,
        )
    with pytest.raises(ValueError, match="reference_pin_mismatch"):
        verify_custody(report, report.identifier, "0" * 64, s, AS_OF)


def test_unsupported_owner_rejected_without_io() -> None:
    with (
        patch("builtins.open", side_effect=AssertionError("no I/O")),
        patch("sqlite3.connect", side_effect=AssertionError("no I/O")),
    ):
        bad: Any = FrozenJSON.freeze({"verified": True, "git_author_date": "2025-07-10"})
        with pytest.raises(ValueError, match="unsupported_evidence_owner_type"):
            audit_custody(bad, AS_OF)


def test_oversized_and_unknown_report_rejected_before_replay() -> None:
    bad: Any = None
    large = FrozenJSON.freeze({"large": "x" * (256 * 1024)})
    with patch(
        "donghak_stock_vision.storage.prospective_custody_readiness.audit_custody",
        side_effect=AssertionError("no replay"),
    ):
        with pytest.raises(ValueError, match="byte_budget"):
            verify_custody(large, large.identifier, "0" * 64, bad, AS_OF)
        owner = FrozenJSON.freeze({"version": "unknown"})
        with pytest.raises(ValueError, match="report_version"):
            verify_custody(owner, owner.identifier, "0" * 64, bad, AS_OF)


def test_large_request_rejected_before_source_io(tmp_path: Path) -> None:
    s = sources(tmp_path)
    case = s.cases[0]
    assert case.future is not None
    large = FrozenJSON.freeze({"body": "x" * (256 * 1024)})
    future = replace(case.future, request=large)
    with (
        patch("pathlib.Path.open", side_effect=AssertionError("no source reads")),
        patch("sqlite3.connect", side_effect=AssertionError("no source reads")),
    ):
        with pytest.raises(ValueError, match="request_byte_budget"):
            audit_custody(replace(s, cases=(EvidenceCase(case.prediction, future),)), AS_OF)


def test_pending_future_reference_is_bound_without_loading_capture(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.prediction_binding import FutureSource

    s = sources(tmp_path, future=False)
    case = s.cases[0]
    future = FutureSource(tmp_path / "not-to-open", FrozenJSON.freeze({}), "0" * 64)
    s = replace(s, cases=(EvidenceCase(case.prediction, future),))
    report = audit_custody(s, RECORDED)
    assert report.to_dict()["counts"]["future_pending"] == 1
    assert not future.capture_root.exists()
    changed = replace(
        s, cases=(EvidenceCase(case.prediction, replace(future, request_pin="1" * 64)),)
    )
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_custody(
            report, report.identifier, report.to_dict()["references_pin"], changed, RECORDED
        )
