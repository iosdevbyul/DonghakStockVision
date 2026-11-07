"""Offline workflow actions on the existing forward-signal CLI."""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.forward_signal_journal import GENESIS
from donghak_stock_vision.storage.prediction_binding import PredictionSource
from donghak_stock_vision.storage.prospective_prediction_workflow import (
    MAX_BUNDLE_BYTES,
    WorkflowInputs,
    _journal_path,
    freeze_workflow,
    record_workflow,
    verify_workflow_report,
)


def _owner(path: Path | None, budget: int) -> FrozenJSON:
    require(path is not None, "workflow_evidence_path_required")
    assert path is not None
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= budget,
        "workflow_bounded_evidence_file_required",
    )
    with path.open("rb") as stream:
        data = stream.read(budget + 1)
    require(len(data) <= budget, "workflow_bounded_evidence_file_required")
    return FrozenJSON(data.decode())


def demonstrate(root: Path, journal: Path, namespace: str) -> FrozenJSON:
    """Create synthetic original captures with the existing demo; no live requests.

    Retained inputs reproduce the report exactly. Fresh fixture creation uses
    local current clocks, which are explicitly not authenticated chronology.
    """
    from donghak_stock_vision.forward_signal_demo_cli import prepare_prediction_fixture

    require(not journal.exists(), "demo_requires_new_journal")
    _journal_path(journal, root)
    request, policy = prepare_prediction_fixture(root)
    workflow = freeze_workflow(
        root, request, request.identifier, policy, policy.identifier, archive_namespace=namespace
    )
    source = WorkflowInputs(
        root, request, request.identifier, policy, policy.identifier, workflow, workflow.identifier
    )
    # Retain the complete frozen references BEFORE the atomic prediction append.
    # The journal is the only mutable workflow owner, not this reference file.
    path = root / "prediction-custody-workflow.json"
    with path.open("x") as stream:
        stream.write(workflow.payload_json)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    result = record_workflow(source, journal, GENESIS, datetime.now(UTC))
    a = result.to_dict()["append"]
    duplicate = record_workflow(source, journal, a["head_pin"], datetime.now(UTC))
    require(duplicate.to_dict()["append"]["duplicate"], "workflow_demo_duplicate_not_detected")
    require(
        result.to_dict()["report_pin"] == duplicate.to_dict()["report_pin"],
        "workflow_demo_replay_changed",
    )
    report = FrozenJSON.freeze(result.to_dict()["report"])
    prediction = PredictionSource(
        journal,
        a["head_pin"],
        a["entry"]["entry_pin"],
        report.to_dict()["prediction_id"],
        root,
        request,
        request.identifier,
        policy.identifier,
    )
    verify_workflow_report(
        report, report.identifier, source, prediction, timestamp(a["entry"]["entry"]["inserted_at"])
    )
    return FrozenJSON.freeze(
        {
            "version": "prospective_prediction_custody_demo_v1",
            "origin": "offline_fixture",
            "request_path": str(root / "signal-request.json"),
            "request_pin": request.identifier,
            "prediction_policy_path": str(root / "prediction-policy.json"),
            "prediction_policy_pin": policy.identifier,
            "workflow_path": str(path),
            "workflow_pin": workflow.identifier,
            "journal_head_pin": a["head_pin"],
            "entry_pin": a["entry"]["entry_pin"],
            "prediction_id": report.to_dict()["prediction_id"],
            "duplicate_detected": True,
            "report": report.to_dict(),
            "report_pin": report.identifier,
            "network_requests": 0,
        }
    )


def run(args: argparse.Namespace) -> int:
    require(args.capture_root is not None, "workflow_capture_root_required")
    if args.action == "offline-prediction-custody-demo":
        require(
            args.journal is not None and args.archive_namespace is not None,
            "workflow_journal_and_namespace_required",
        )
        print(demonstrate(args.capture_root, args.journal, args.archive_namespace).payload_json)
        return 0
    from donghak_stock_vision.forward_signal_cli import _request

    request = _request(args)
    policy = _owner(args.prediction_policy, 4096)
    require(args.prediction_policy_pin is not None, "workflow_prediction_policy_pin_required")
    if args.action == "prediction-custody-freeze":
        require(args.archive_namespace is not None, "workflow_namespace_required")
        workflow = freeze_workflow(
            args.capture_root,
            request,
            args.request_pin,
            policy,
            args.prediction_policy_pin,
            archive_namespace=args.archive_namespace,
        )
        print(
            FrozenJSON.freeze(
                {"workflow": workflow.to_dict(), "workflow_pin": workflow.identifier}
            ).payload_json
        )
        return 0
    workflow = _owner(args.workflow, MAX_BUNDLE_BYTES)
    require(args.workflow_pin is not None, "workflow_independent_pin_required")
    source = WorkflowInputs(
        args.capture_root,
        request,
        args.request_pin,
        policy,
        args.prediction_policy_pin,
        workflow,
        args.workflow_pin,
    )
    require(
        args.journal is not None and args.journal_pin is not None,
        "workflow_journal_and_head_required",
    )
    if args.action == "prediction-custody-record":
        print(
            record_workflow(source, args.journal, args.journal_pin, datetime.now(UTC)).payload_json
        )
        return 0
    require(
        args.entry_pin is not None
        and args.prediction_id is not None
        and args.report_pin is not None,
        "workflow_retained_prediction_and_report_pins_required",
    )
    report = _owner(args.report, MAX_BUNDLE_BYTES)
    prediction = PredictionSource(
        args.journal,
        args.journal_pin,
        args.entry_pin,
        args.prediction_id,
        args.capture_root,
        request,
        args.request_pin,
        args.prediction_policy_pin,
    )
    # Reconstruct the record-time report, with time taken from the verified
    # original journal through inspect_prediction, never from a report assertion.
    from donghak_stock_vision.storage.prediction_binding import inspect_prediction

    original = inspect_prediction(prediction)
    at = timestamp(original.to_dict()["prediction"]["recorded_at"])
    verify_workflow_report(report, args.report_pin, source, prediction, at)
    print(
        FrozenJSON.freeze(
            {
                "report_pin": args.report_pin,
                "locally_verified": True,
                "independent_custody_verified": False,
            }
        ).payload_json
    )
    return 0
