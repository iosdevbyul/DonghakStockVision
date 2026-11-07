"""Offline orchestration of existing source, prediction, journal and custody owners.

The frozen workflow is a retained reference bundle, not a new prediction/journal
or an authentication authority. Only append_signal may write a prediction.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.forward_raw_source_custody import (
    SuppliedRawSourceEvidence,
    assess_forward_raw_source,
)
from donghak_stock_vision.storage.forward_signal import generate_signal, request_contract
from donghak_stock_vision.storage.forward_signal_journal import (
    GENESIS,
    MAX_ENTRIES,
    append_signal,
    history,
)
from donghak_stock_vision.storage.prediction_binding import PredictionSource, inspect_prediction
from donghak_stock_vision.storage.prospective_operational_readiness import CaptureSource
from donghak_stock_vision.storage.prospective_prediction import authorize_horizon, evidence, state

VERSION = "prospective_prediction_custody_workflow_v1"
REPORT_VERSION = "prospective_prediction_custody_verification_v1"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
_NO_EXTERNAL = SuppliedRawSourceEvidence()


@dataclass(frozen=True)
class WorkflowInputs:
    capture_root: Path
    request: FrozenJSON
    request_pin: str
    prediction_policy: FrozenJSON
    prediction_policy_pin: str
    workflow: FrozenJSON
    workflow_pin: str
    supplied: tuple[SuppliedRawSourceEvidence, ...] = ()


def _decision(
    root: Path, request: FrozenJSON, request_pin: str, policy: FrozenJSON, policy_pin: str
) -> FrozenJSON:
    # Existing guards reject protected partitions and v2/v3 operational capture
    # owners before archive I/O. Provider-backed predictions remain unsupported.
    authorize_horizon(request, request_pin, policy, policy_pin)
    require(request.to_dict()["mode"] == "offline_fixture", "workflow_offline_fixture_required")
    return generate_signal(root, request, request_pin)


def _bundle(
    root: Path,
    request: FrozenJSON,
    request_pin: str,
    policy_pin: str,
    archive_namespace: str,
    decision: FrozenJSON,
    supplied: tuple[SuppliedRawSourceEvidence, ...],
) -> FrozenJSON:
    plans = request_contract(request, request_pin)
    require(type(supplied) is tuple and len(supplied) in (0, len(plans)), "workflow_supplied_count")
    rows = []
    doc = decision.to_dict()
    for index, (plan, source, observation) in enumerate(
        zip(plans, request.to_dict()["sources"], doc["inputs"]["observations"], strict=True)
    ):
        # The observation carries an original raw receipt; its run suffix binds
        # the response sequence. The original capture/custody verifiers below
        # authenticate that suffix against forward_responses and the manifest.
        run = observation["raw_receipt"]["runId"]
        sequence = int(run.rsplit(":", 1)[-1])
        capture = CaptureSource(
            root, plan, source["plan_pin"], source["attempt"], source["capture_pin"], sequence
        )
        custody = assess_forward_raw_source(
            capture,
            archive_namespace=archive_namespace,
            supplied=supplied[index] if supplied else _NO_EXTERNAL,
        )
        binding = custody.to_dict()["binding"]
        require(
            binding["raw_id"] == observation["raw_id"]
            and binding["receipt"] == observation["raw_receipt"]
            and binding["capture_id"] == observation["capture_id"]
            and binding["capture_manifest_pin"] == observation["capture_pin"]
            and binding["plan_pin"] == observation["plan_pin"],
            "workflow_observation_custody_ownership",
        )
        rows.append(
            {
                "observation_id": observation["observation_id"],
                "custody": custody.to_dict(),
                "custody_pin": custody.identifier,
            }
        )
    owner = FrozenJSON.freeze(
        {
            "version": VERSION,
            "origin": "offline_fixture",
            "request_pin": request_pin,
            "prediction_policy_pin": policy_pin,
            "strategy": doc["strategy"],
            "parameters": doc["parameters"],
            "signal_policy": request.to_dict()["policy"],
            "archive_namespace": archive_namespace,
            "source_database_pin": request.to_dict()["database_pin"],
            "observations": rows,
        }
    )
    require(len(owner.payload_json.encode()) <= MAX_BUNDLE_BYTES, "workflow_bundle_budget")
    return owner


def freeze_workflow(
    root: Path,
    request: FrozenJSON,
    request_pin: str,
    prediction_policy: FrozenJSON,
    prediction_policy_pin: str,
    *,
    archive_namespace: str,
    supplied: tuple[SuppliedRawSourceEvidence, ...] = (),
) -> FrozenJSON:
    """Verify original inputs and freeze references before recording; no writes."""
    decision = _decision(root, request, request_pin, prediction_policy, prediction_policy_pin)
    require(
        decision.to_dict()["calculation"]["signal"] in {"BUY", "SELL"},
        "directional_prediction_required",
    )
    return _bundle(
        root, request, request_pin, prediction_policy_pin, archive_namespace, decision, supplied
    )


def _inputs(source: WorkflowInputs) -> FrozenJSON:
    require(type(source) is WorkflowInputs, "workflow_original_inputs_required")
    require(
        type(source.workflow) is FrozenJSON
        and len(source.workflow.payload_json.encode()) <= MAX_BUNDLE_BYTES,
        "workflow_bundle_budget",
    )
    require(source.workflow.identifier == sha(source.workflow_pin), "workflow_retained_pin")
    doc = source.workflow.to_dict()
    require(doc.get("version") == VERSION, "workflow_version")
    namespace = doc.get("archive_namespace")
    require(type(namespace) is str, "workflow_archive_namespace_required")
    assert isinstance(namespace, str)
    decision = _decision(
        source.capture_root,
        source.request,
        source.request_pin,
        source.prediction_policy,
        source.prediction_policy_pin,
    )
    fresh = _bundle(
        source.capture_root,
        source.request,
        source.request_pin,
        source.prediction_policy_pin,
        namespace,
        decision,
        source.supplied,
    )
    require(fresh == source.workflow, "workflow_frozen_inputs_mismatch")
    return decision


def _journal_path(journal: Path, root: Path) -> None:
    require(
        not journal.resolve().is_relative_to(root.resolve()), "separate_prediction_journal_required"
    )


def verify_workflow(
    source: WorkflowInputs,
    prediction: PredictionSource,
    as_of: datetime,
) -> FrozenJSON:
    """Read back originals using retained head/entry/ID pins; no outcome resolution."""
    decision = _inputs(source)
    _journal_path(prediction.journal, source.capture_root)
    require(
        prediction.capture_root.resolve() == source.capture_root.resolve()
        and prediction.request == source.request
        and prediction.request_pin == source.request_pin
        and prediction.policy_pin == source.prediction_policy_pin,
        "workflow_original_prediction_owner_mismatch",
    )
    original = inspect_prediction(prediction)
    p = original.to_dict()["prediction"]
    require(
        original.to_dict()["decision_pin"] == decision.identifier
        and p["policy"] == source.prediction_policy.to_dict(),
        "workflow_prediction_mismatch",
    )
    lifecycle = state(FrozenJSON.freeze(p), timestamp(as_of))
    report = FrozenJSON.freeze(
        {
            "version": REPORT_VERSION,
            "origin": "offline_fixture",
            "workflow_pin": source.workflow_pin,
            "request_pin": source.request_pin,
            "prediction_policy_pin": source.prediction_policy_pin,
            "journal_head_pin": prediction.journal_head,
            "entry_pin": prediction.entry_pin,
            "prediction_id": prediction.prediction_id,
            "decision_pin": decision.identifier,
            "prediction_artifact_pin": FrozenJSON.freeze(p).identifier,
            "original_prediction_verification": original.to_dict(),
            "strategy": p["predictor"],
            "parameters": p["parameters"],
            "input_fingerprint": p["input_fingerprint"],
            "observations": source.workflow.to_dict()["observations"],
            "local_evidence_verified": True,
            "prediction_journal_integrity_verified": True,
            "source_to_prediction_lineage_verified": True,
            "prediction_state": lifecycle.to_dict(),
            "rejected_evidence": [],
            "reason_codes": [
                "synthetic_offline_rehearsal_only",
                "independent_policy_predeclaration_not_authenticated",
                "prediction_existence_time_not_authenticated",
                "external_prediction_custody_not_supplied",
                "external_custodian_independence_not_authenticated",
                "original_publication_time_not_authenticated",
            ],
            "independent_timestamp_authenticated": False,
            "independent_custody_verified": False,
            "genuine_forward_evidence": False,
            "pit_qualified": False,
            "performance_approved": False,
            "execution_authorized": False,
            "performance_metrics": None,
            "network_requests": 0,
        }
    )
    require(len(report.payload_json.encode()) <= MAX_BUNDLE_BYTES, "workflow_report_budget")
    # Detect changed journal heads across source reads without claiming a
    # transaction spanning SQLite files or the retained workflow document.
    history(prediction.journal, prediction.journal_head, limit=MAX_ENTRIES)
    return report


def record_workflow(
    source: WorkflowInputs,
    journal: Path,
    expected_head: str,
    recorded_at: datetime,
) -> FrozenJSON:
    """Validate all retained references before the original atomic append.

    Post-commit verification failure publishes no completion report. The original
    journal remains append-only; retry needs the independently retained current
    head. Unknown/stale heads never cause repair, truncation or implicit adoption.
    """
    decision = _inputs(source)
    _journal_path(journal, source.capture_root)
    sha(expected_head)
    require(journal.exists() or expected_head == GENESIS, "workflow_new_journal_requires_genesis")
    # Existing duplicates preserve the original timestamp; validate a new record's
    # clock/horizon before opening a writable journal, using the original contract.
    records = (
        history(journal, expected_head, limit=MAX_ENTRIES).to_dict() if journal.exists() else None
    )
    duplicate = (
        []
        if records is None
        else [
            e
            for e in records["entries"]
            if e["entry"]["decision"]["signal_id"] == decision.to_dict()["signal_id"]
        ]
    )
    if not duplicate:
        evidence(decision, source.prediction_policy, source.prediction_policy_pin, recorded_at)
    appended = append_signal(
        journal,
        source.capture_root,
        source.request,
        source.request_pin,
        expected_head,
        recorded_at,
        prediction_policy=source.prediction_policy,
        prediction_policy_pin=source.prediction_policy_pin,
    )
    a = appended.to_dict()
    p = a["entry"]["entry"]["prediction"]
    owner = PredictionSource(
        journal,
        a["head_pin"],
        a["entry"]["entry_pin"],
        p["prediction_id"],
        source.capture_root,
        source.request,
        source.request_pin,
        source.prediction_policy_pin,
    )
    # Use the original recording instant so duplicate replay produces the exact
    # same report rather than silently changing a pending forecast's chronology.
    report = verify_workflow(source, owner, timestamp(p["recorded_at"]))
    return FrozenJSON.freeze(
        {
            "version": "prospective_prediction_custody_record_v1",
            "append": a,
            "report": report.to_dict(),
            "report_pin": report.identifier,
        }
    )


def verify_workflow_report(
    report: FrozenJSON,
    report_pin: str,
    source: WorkflowInputs,
    prediction: PredictionSource,
    as_of: datetime,
) -> None:
    require(
        type(report) is FrozenJSON and len(report.payload_json.encode()) <= MAX_BUNDLE_BYTES,
        "workflow_report_budget",
    )
    require(report.identifier == sha(report_pin), "workflow_report_pin")
    require(report.to_dict().get("version") == REPORT_VERSION, "workflow_report_version")
    require(
        report == verify_workflow(source, prediction, as_of), "workflow_report_semantic_mismatch"
    )
