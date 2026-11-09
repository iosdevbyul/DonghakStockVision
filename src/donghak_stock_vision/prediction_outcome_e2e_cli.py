"""Deterministic read-only offline outcome rehearsal from retained JSON owners.

Input references are separately pinned by the operator; a locally generated
manifest/pin pair cannot establish independent prediction-time evidence.
"""

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.prediction_outcome_e2e import (
    RehearsalInputs,
    rehearse,
    verify_rehearsal,
)
from donghak_stock_vision.storage.prospective_evaluation_consumer import EvaluationSources
from donghak_stock_vision.storage.prospective_evidence_audit import (
    MAX_INPUT_BYTES,
    input_cases,
)
from donghak_stock_vision.storage.prospective_prediction_workflow import WorkflowInputs


def _read(path: Path, limit: int) -> FrozenJSON:
    require(path.is_file() and not path.is_symlink(), "e2e_regular_file_required")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "e2e_input_byte_budget")
    return FrozenJSON(raw.decode("utf-8"))


def _pinned(raw: object) -> tuple[FrozenJSON, str]:
    doc = fields(raw, "owner pin")
    owner = FrozenJSON.freeze(doc["owner"])
    pin = sha(doc["pin"])
    require(owner.identifier == pin, "e2e_retained_owner_pin_mismatch")
    return owner, pin


def inputs(manifest: FrozenJSON, trusted_pin: str) -> RehearsalInputs:
    require(manifest.identifier == sha(trusted_pin), "e2e_manifest_pin_mismatch")
    d = fields(
        manifest.to_dict(),
        "version cases prediction_policy workflow evaluation_policy cohort horizon",
    )
    require(d["version"] == "prediction_outcome_e2e_input_v1", "e2e_manifest_version")
    cases_owner, cases_pin = _pinned(d["cases"])
    cases = input_cases(cases_owner, cases_pin)
    require(len(cases) == 1, "e2e_single_prediction_required")
    prediction = cases[0].prediction
    prediction_policy, prediction_policy_pin = _pinned(d["prediction_policy"])
    workflow, workflow_pin = _pinned(d["workflow"])
    evaluation_policy, evaluation_policy_pin = _pinned(d["evaluation_policy"])
    cohort, cohort_pin = _pinned(d["cohort"])
    horizon, horizon_pin = _pinned(d["horizon"])
    require(
        prediction.policy_pin == prediction_policy_pin == horizon_pin
        and prediction_policy == horizon,
        "e2e_original_horizon_mismatch",
    )
    custody = WorkflowInputs(
        prediction.capture_root,
        prediction.request,
        prediction.request_pin,
        prediction_policy,
        prediction_policy_pin,
        workflow,
        workflow_pin,
    )
    evaluation = EvaluationSources(
        evaluation_policy,
        evaluation_policy_pin,
        cohort,
        cohort_pin,
        horizon,
        horizon_pin,
        cases,
    )
    return RehearsalInputs(custody, evaluation)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-pin", required=True)
    parser.add_argument("--as-of", type=timestamp, required=True)
    parser.add_argument("--verify-report", type=Path)
    parser.add_argument("--report-pin")
    args = parser.parse_args(argv)
    try:
        require(
            (args.verify_report is None) == (args.report_pin is None),
            "e2e_report_file_and_pin_required_together",
        )
        source = inputs(_read(args.input, MAX_INPUT_BYTES), args.input_pin)
        if args.verify_report is not None:
            report = _read(args.verify_report, MAX_INPUT_BYTES)
            verify_rehearsal(report, args.report_pin, source, args.as_of)
        else:
            report = rehearse(source, args.as_of)
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f"offline E2E blocked: {error}\n")
    print(report.payload_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
