"""Offline end-to-end original custody, future binding and evaluator integration."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.prediction_outcome_e2e_cli import inputs, main
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.prediction_outcome_e2e import (
    RehearsalInputs,
    rehearse,
    verify_rehearsal,
)
from donghak_stock_vision.storage.prospective_evaluation_consumer import EvaluationSources
from donghak_stock_vision.storage.prospective_evidence_audit import EvidenceCase
from donghak_stock_vision.storage.prospective_prediction_workflow import WorkflowInputs
from tests.integration.test_prediction_binding import AS_OF, future_capture
from tests.integration.test_prospective_evaluation_policy import policy as evaluation_policy
from tests.integration.test_prospective_prediction import RECORDED
from tests.integration.test_prospective_prediction_workflow import prediction, record


pytest_plugins = ("tests.integration.test_prospective_prediction_workflow",)


def assembled(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path, *, future: bool = True
) -> RehearsalInputs:
    custody, journal = workflow
    original = prediction(custody, journal, record(workflow))
    plan = ForwardCapturePlan.from_dict(custody.request.to_dict()["sources"][0]["plan"])
    assert plan.split is not None
    cohort = FrozenJSON.freeze(plan.split.to_dict())
    evaluation = evaluation_policy(
        cohort_pin=cohort.identifier,
        horizon_definition_pin=custody.prediction_policy_pin,
        expected_prediction_ids=[original.prediction_id],
        minimum_samples=1,
    )
    f = future_capture(original, tmp_path / "later-capture") if future else None
    return RehearsalInputs(
        custody,
        EvaluationSources(
            evaluation,
            evaluation.identifier,
            cohort,
            cohort.identifier,
            custody.prediction_policy,
            custody.prediction_policy_pin,
            (EvidenceCase(original, f),),
        ),
    )


def manifest(source: RehearsalInputs) -> FrozenJSON:
    case = source.evaluation.cases[0]
    p = case.prediction
    f = case.future
    cases = FrozenJSON.freeze(
        {
            "version": "prospective_evidence_audit_input_v1",
            "cases": [
                {
                    "prediction": {
                        "journal": str(p.journal.resolve()),
                        "journal_head": p.journal_head,
                        "entry_pin": p.entry_pin,
                        "prediction_id": p.prediction_id,
                        "capture_root": str(p.capture_root.resolve()),
                        "request": p.request.to_dict(),
                        "request_pin": p.request_pin,
                        "policy_pin": p.policy_pin,
                    },
                    "future": None
                    if f is None
                    else {
                        "capture_root": str(f.capture_root.resolve()),
                        "request": f.request.to_dict(),
                        "request_pin": f.request_pin,
                    },
                }
            ],
        }
    )

    def pinned(owner: FrozenJSON) -> dict[str, object]:
        return {"owner": owner.to_dict(), "pin": owner.identifier}

    return FrozenJSON.freeze(
        {
            "version": "prediction_outcome_e2e_input_v1",
            "cases": pinned(cases),
            "prediction_policy": pinned(source.custody.prediction_policy),
            "workflow": pinned(source.custody.workflow),
            "evaluation_policy": pinned(source.evaluation.policy),
            "cohort": pinned(source.evaluation.cohort),
            "horizon": pinned(source.evaluation.horizon),
        }
    )


def test_complete_offline_replay_without_network_or_mutation(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path
) -> None:
    source = assembled(workflow, tmp_path)
    original = source.evaluation.cases[0].prediction
    future = source.evaluation.cases[0].future
    assert future is not None
    watched = (original.journal, original.capture_root / DB)
    before = {str(p): p.read_bytes() for p in watched if p.exists()}
    with patch("httpx.Client.send", side_effect=AssertionError("network forbidden")):
        report = rehearse(source, AS_OF)
        verify_rehearsal(report, report.identifier, source, AS_OF)
        assert rehearse(source, AS_OF) == report
    data = report.to_dict()
    assert data["binding_status"] == "verified"
    assert data["evaluation_report"]["result"]["state"] == "evaluated"
    assert data["local_integrity_verified"] is True
    assert data["independent_chronology_verified"] is False
    assert data["genuine_forward_evidence"] is False
    assert data["performance_metrics"] is None
    assert all(v is None for v in data["evaluation_report"]["result"]["metrics"].values())
    assert {str(p): p.read_bytes() for p in watched if p.exists()} == before


@pytest.mark.parametrize("kind", ["head", "policy", "strategy", "future", "future_pin", "report"])
def test_invalid_inputs_fail_closed(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path, kind: str
) -> None:
    source = assembled(workflow, tmp_path)
    case = source.evaluation.cases[0]
    if kind == "head":
        p = replace(case.prediction, journal_head="0" * 64)
        source = replace(
            source, evaluation=replace(source.evaluation, cases=(EvidenceCase(p, case.future),))
        )
    elif kind == "policy":
        source = replace(source, custody=replace(source.custody, prediction_policy_pin="0" * 64))
    elif kind == "strategy":
        doc = source.custody.workflow.to_dict()
        doc["strategy"]["strategy_version"] = "forged"
        forged = FrozenJSON.freeze(doc)
        source = replace(
            source, custody=replace(source.custody, workflow=forged, workflow_pin=forged.identifier)
        )
    elif kind == "future":
        assert case.future is not None
        source = replace(
            source,
            evaluation=replace(source.evaluation, cases=(EvidenceCase(case.prediction, None),))
        )
    elif kind == "future_pin":
        assert case.future is not None
        bad = replace(case.future, request_pin="0" * 64)
        source = replace(
            source,
            evaluation=replace(source.evaluation, cases=(EvidenceCase(case.prediction, bad),))
        )
    if kind == "report":
        report = rehearse(source, AS_OF)
        changed = report.to_dict()
        changed["performance_approved"] = True
        forged = FrozenJSON.freeze(changed)
        with pytest.raises(ValueError):
            verify_rehearsal(forged, forged.identifier, source, AS_OF)
    elif kind == "future":
        report = rehearse(source, AS_OF)
        assert report.to_dict()["binding_status"] == "unavailable"
        assert report.to_dict()["evaluation_report"]["result"]["state"] == "unavailable"
    else:
        with pytest.raises(ValueError):
            rehearse(source, AS_OF)


def test_pending_remains_pending(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path
) -> None:
    source = assembled(workflow, tmp_path, future=False)
    report = rehearse(source, RECORDED).to_dict()
    assert report["binding_status"] == "pending"
    assert report["evaluation_report"]["result"]["state"] == "pending"


def test_pinned_manifest_cli_replay_and_forgery(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = assembled(workflow, tmp_path)
    doc = manifest(source)
    path = tmp_path / "input.json"
    path.write_text(doc.payload_json)
    args = ["--input", str(path), "--input-pin", doc.identifier, "--as-of", AS_OF.isoformat()]
    assert (
        inputs(doc, doc.identifier).evaluation.cases[0].prediction.prediction_id
        == source.evaluation.cases[0].prediction.prediction_id
    )
    assert main(args) == 0
    report = FrozenJSON(capsys.readouterr().out)
    saved = tmp_path / "report.json"
    saved.write_text(report.payload_json)
    assert main(args + ["--verify-report", str(saved), "--report-pin", report.identifier]) == 0
    assert FrozenJSON(capsys.readouterr().out) == report
    with pytest.raises(SystemExit) as blocked:
        main(args + ["--verify-report", str(saved), "--report-pin", "0" * 64])
    assert blocked.value.code == 2


@pytest.mark.parametrize("kind", ["wrong_date", "wrong_ticker", "altered_bytes"])
def test_future_owner_substitution_and_raw_tampering(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path, kind: str
) -> None:
    source = assembled(workflow, tmp_path, future=False)
    original = source.evaluation.cases[0].prediction
    future = future_capture(
        original,
        tmp_path / "substituted-future",
        day=date(2025, 7, 14) if kind == "wrong_date" else date(2025, 7, 11),
        at=AS_OF + timedelta(days=4) if kind == "wrong_date" else AS_OF - timedelta(seconds=3),
    )
    if kind == "wrong_ticker":
        request = future.request.to_dict()
        request["sources"][0]["plan"]["symbol"] = "000001"
        forged = FrozenJSON.freeze(request)
        future = replace(future, request=forged, request_pin=forged.identifier)
    elif kind == "altered_bytes":
        with (future.capture_root / DB).open("ab") as stream:
            stream.write(b"modified original response archive")
    case = EvidenceCase(original, future)
    source = replace(source, evaluation=replace(source.evaluation, cases=(case,)))
    with pytest.raises(ValueError, match="e2e_invalid_outcome_evidence"):
        rehearse(source, AS_OF)


def test_recovery_after_interrupted_verification_preserves_originals(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path
) -> None:
    source = assembled(workflow, tmp_path)
    journal = source.evaluation.cases[0].prediction.journal
    before = journal.read_bytes()
    with patch(
        "donghak_stock_vision.storage.prediction_outcome_e2e.prepare_evaluation",
        side_effect=KeyboardInterrupt,
    ):
        with pytest.raises(KeyboardInterrupt):
            rehearse(source, AS_OF)
    assert journal.read_bytes() == before
    completed = rehearse(source, AS_OF)
    verify_rehearsal(completed, completed.identifier, source, AS_OF)
    assert journal.read_bytes() == before


def test_manifest_rejects_self_rehashed_horizon_substitution(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path
) -> None:
    source = assembled(workflow, tmp_path)
    original = manifest(source)
    changed = original.to_dict()
    changed["horizon"]["owner"]["target_trading_date"] = "2025-07-14"
    forged_horizon = FrozenJSON.freeze(changed["horizon"]["owner"])
    changed["horizon"]["pin"] = forged_horizon.identifier
    forged = FrozenJSON.freeze(changed)
    with pytest.raises(ValueError, match="e2e_original_horizon_mismatch"):
        inputs(forged, forged.identifier)


def test_historical_result_v1_still_semantically_verified(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path
) -> None:
    source = assembled(workflow, tmp_path)
    report = rehearse(source, AS_OF)
    data = report.to_dict()
    assert data["evaluation_report"]["result"]["version"] == "prospective_evaluation_result_v1"
    assert data["evaluation_report"]["version"] == "prospective_evaluation_preparation_v1"
    assert report == rehearse(source, AS_OF)
