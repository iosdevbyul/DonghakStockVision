"""Operator go/no-go: independently unverified evidence must never become ready."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.integration.test_prediction_binding import AS_OF
from tests.integration.test_prediction_outcome_e2e import assembled, manifest
from tests.integration.test_prospective_anchor_governance import ISSUER, KEY_ID, anchor
from tests.integration.test_prospective_prediction import RECORDED

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.genuine_forward_readiness_cli import main
from donghak_stock_vision.storage.genuine_forward_readiness import (
    ReadinessInputs,
    assess_readiness,
    verify_readiness,
)
from donghak_stock_vision.storage.prospective_anchor_governance import AnchorSnapshot
from donghak_stock_vision.storage.prospective_evidence_audit import EvidenceCase
from donghak_stock_vision.storage.prospective_prediction_workflow import WorkflowInputs

pytest_plugins = ("tests.integration.test_prospective_prediction_workflow",)


def test_reproducible_offline_report_and_independent_blockers(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path
) -> None:
    source = ReadinessInputs(assembled(workflow, tmp_path))
    before = source.rehearsal.evaluation.cases[0].prediction.journal.read_bytes()
    with patch("httpx.Client.send", side_effect=AssertionError("network prohibited")):
        report = assess_readiness(source, AS_OF)
        verify_readiness(report, report.identifier, source, AS_OF)
        assert assess_readiness(source, AS_OF) == report
    d = report.to_dict()
    assert d["overall"] == "blocked" and d["offline_software_readiness"] == "verified_local"
    assert d["capabilities"]["future_observation_and_binding"]["status"] == "verified_local"
    assert d["future_binding_status"] == "verified"
    assert d["phase7"]["phase7_a"]["live_collection"] == "blocked"
    assert d["phase7"]["phase7_c"]["genuine_forward_evaluation"] == "blocked"
    assert d["performance_metrics"] is None and d["network_requests"] == 0
    assert all(
        d[key] is False
        for key in (
            "genuine_forward_evidence",
            "pit_qualified",
            "performance_approved",
            "execution_authorized",
        )
    )
    assert source.rehearsal.evaluation.cases[0].prediction.journal.read_bytes() == before


def test_missing_future_remains_blocked_and_pending_preserved(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path
) -> None:
    source = ReadinessInputs(assembled(workflow, tmp_path, future=False))
    report = assess_readiness(source, RECORDED).to_dict()
    assert report["future_binding_status"] == "pending"
    assert "future_observation_pending" in report["missing_evidence_and_blockers"]
    assert report["capabilities"]["future_observation_and_binding"]["status"] == "unavailable"


@pytest.mark.parametrize("status", ["active", "revoked", "inactive"])
def test_configured_or_revoked_anchor_never_conveys_independent_authority(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path, status: str
) -> None:
    owner = anchor(status=status)
    source = ReadinessInputs(
        assembled(workflow, tmp_path), AnchorSnapshot(owner, owner.identifier), ISSUER, KEY_ID
    )
    report = assess_readiness(source, AS_OF).to_dict()
    assert report["capabilities"]["issuer_key_governance_consistency"]["status"] == "verified_local"
    assert report["overall"] == "blocked"
    assert (
        "historical_revocation_completeness_and_freshness_unverified"
        in (report["missing_evidence_and_blockers"])
    )
    if status != "active":
        assert "declared_key_" + status in report["missing_evidence_and_blockers"]


@pytest.mark.parametrize("kind", ["journal", "horizon", "future", "report", "anchor"])
def test_modified_owners_and_forged_reports_fail_closed(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path, kind: str
) -> None:
    source = ReadinessInputs(assembled(workflow, tmp_path))
    case = source.rehearsal.evaluation.cases[0]
    if kind == "journal":
        bad = replace(case.prediction, journal_head="0" * 64)
        evaluation = replace(source.rehearsal.evaluation, cases=(EvidenceCase(bad, case.future),))
        source = replace(source, rehearsal=replace(source.rehearsal, evaluation=evaluation))
    elif kind == "horizon":
        custody = replace(source.rehearsal.custody, prediction_policy_pin="0" * 64)
        source = replace(source, rehearsal=replace(source.rehearsal, custody=custody))
    elif kind == "future":
        assert case.future is not None
        future = replace(case.future, request_pin="0" * 64)
        evaluation = replace(
            source.rehearsal.evaluation, cases=(EvidenceCase(case.prediction, future),)
        )
        source = replace(source, rehearsal=replace(source.rehearsal, evaluation=evaluation))
    elif kind == "anchor":
        owner = anchor()
        source = replace(
            source,
            anchor=AnchorSnapshot(owner, "0" * 64),
            expected_issuer=ISSUER,
            expected_key_id=KEY_ID,
        )
    if kind == "report":
        report = assess_readiness(source, AS_OF)
        d = report.to_dict()
        d["overall"] = "ready"
        forged = FrozenJSON.freeze(d)
        with pytest.raises(ValueError, match="readiness_report_semantic_mismatch"):
            verify_readiness(forged, forged.identifier, source, AS_OF)
    else:
        with pytest.raises(ValueError):
            assess_readiness(source, AS_OF)


def test_cli_pinned_replay_and_reject_wrong_report_pin(
    workflow: tuple[WorkflowInputs, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = assembled(workflow, tmp_path)
    owner = manifest(source)
    path = tmp_path / "readiness-input.json"
    path.write_text(owner.payload_json)
    args = ["--input", str(path), "--input-pin", owner.identifier, "--as-of", AS_OF.isoformat()]
    with patch("httpx.Client.send", side_effect=AssertionError("network prohibited")):
        assert main(args) == 0
        report = FrozenJSON(capsys.readouterr().out)
        output = tmp_path / "readiness-report.json"
        output.write_text(report.payload_json)
        assert main(args + ["--verify-report", str(output), "--report-pin", report.identifier]) == 0
        assert FrozenJSON(capsys.readouterr().out) == report
        with pytest.raises(SystemExit) as blocked:
            main(args + ["--verify-report", str(output), "--report-pin", "0" * 64])
        assert blocked.value.code == 2
