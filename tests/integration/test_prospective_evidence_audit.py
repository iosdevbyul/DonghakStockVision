"""Original-owner audit tests, entirely offline and explicitly synthetic."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_prediction_binding import AS_OF, future_capture, make_original
from tests.integration.test_prospective_prediction import RECORDED

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.prospective_evidence_audit_cli import main
from donghak_stock_vision.storage.prediction_binding import FutureSource, PredictionSource
from donghak_stock_vision.storage.prospective_evidence_audit import (
    MAX_CASES,
    MAX_INPUT_BYTES,
    MAX_REQUEST_BYTES,
    EvidenceCase,
    audit_evidence,
    input_cases,
    verify_audit,
)


@pytest.fixture
def original(tmp_path: Path) -> PredictionSource:
    return make_original(tmp_path)


def input_owner(cases: tuple[EvidenceCase, ...]) -> FrozenJSON:
    rows = []
    for case in cases:
        p = case.prediction
        f = case.future
        rows.append(
            {
                "prediction": {
                    "journal": str(p.journal),
                    "journal_head": p.journal_head,
                    "entry_pin": p.entry_pin,
                    "prediction_id": p.prediction_id,
                    "capture_root": str(p.capture_root),
                    "request": p.request.to_dict(),
                    "request_pin": p.request_pin,
                    "policy_pin": p.policy_pin,
                },
                "future": None
                if f is None
                else {
                    "capture_root": str(f.capture_root),
                    "request": f.request.to_dict(),
                    "request_pin": f.request_pin,
                },
            }
        )
    return FrozenJSON.freeze({"version": "prospective_evidence_audit_input_v1", "cases": rows})


def file_bytes(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_eligible_structure_is_not_genuine_performance_and_is_read_only(
    original: PredictionSource, tmp_path: Path
) -> None:
    future = future_capture(original, tmp_path / "future")
    cases = (EvidenceCase(original, future),)
    before = file_bytes(tmp_path)
    with patch("httpx.Client.send", side_effect=AssertionError("network forbidden")):
        report = audit_evidence(cases, AS_OF)
        verify_audit(report, report.identifier, cases, AS_OF)
        restarted = input_cases(
            FrozenJSON(input_owner(cases).payload_json), input_owner(cases).identifier
        )
        assert audit_evidence(restarted, AS_OF) == report
    d = report.to_dict()
    assert d["version"] == "prospective_evidence_eligibility_audit_v1"
    assert d["counts"] == {"eligible": 1, "pending": 0, "unavailable": 0, "invalid": 0}
    assert d["reason_counts"] == {"verified_offline_pair_structure_only": 1}
    assert d["cases"][0]["structure_ready"]
    assert d["cases"][0]["evidence_class"] == "synthetic_offline_only"
    for flag in (
        "genuine_forward_evidence",
        "prospective_performance_approved",
        "pit_qualified",
        "execution_authorized",
    ):
        assert d[flag] is False
    assert d["cases"][0]["prospective_evaluation_eligible"] is False
    assert not any(
        key in report.payload_json
        for key in ('"correct"', '"accuracy"', '"return"', '"outcome"', '"baseline"')
    )
    assert file_bytes(tmp_path) == before


def test_pending_does_not_open_future_capture(original: PredictionSource, tmp_path: Path) -> None:
    future = FutureSource(tmp_path / "must-not-open", FrozenJSON.freeze({}), "0" * 64)
    d = audit_evidence((EvidenceCase(original, future),), RECORDED).to_dict()
    assert d["counts"]["pending"] == 1
    assert d["cases"][0]["reason"] == "frozen_evaluation_gate_not_elapsed"
    assert not future.capture_root.exists()


def test_missing_and_late_observation_remain_unavailable(
    original: PredictionSource, tmp_path: Path
) -> None:
    d = audit_evidence((EvidenceCase(original),), AS_OF).to_dict()
    assert d["counts"]["unavailable"] == 1
    assert d["cases"][0]["reason"] == "future_observation_missing"
    future = future_capture(original, tmp_path / "late", at=AS_OF + timedelta(days=4))
    d = audit_evidence(
        (EvidenceCase(original, future),), AS_OF + timedelta(days=4, seconds=4)
    ).to_dict()
    assert d["counts"]["unavailable"] == 1 and not d["cases"][0]["structure_ready"]


@pytest.mark.parametrize("field", ["journal_head", "entry_pin", "policy_pin", "request_pin"])
def test_wrong_original_references_are_invalid(original: PredictionSource, field: str) -> None:
    updates: dict[str, Any] = {field: "0" * 64}
    d = audit_evidence((EvidenceCase(replace(original, **updates)),), AS_OF).to_dict()
    assert d["counts"]["invalid"] == 1 and not d["cases"][0]["structure_ready"]


def test_tampering_and_rehashed_audit_do_not_become_trusted(
    original: PredictionSource, tmp_path: Path
) -> None:
    future = future_capture(original, tmp_path / "future")
    cases = (EvidenceCase(original, future),)
    report = audit_evidence(cases, AS_OF)
    with pytest.raises(ValueError, match="pin_mismatch"):
        verify_audit(report, "0" * 64, cases, AS_OF)
    forged = report.to_dict()
    forged["genuine_forward_evidence"] = True
    owner = FrozenJSON.freeze(forged)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_audit(owner, owner.identifier, cases, AS_OF)
    changed_cases = (EvidenceCase(replace(original, journal_head="0" * 64), future),)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_audit(report, report.identifier, changed_cases, AS_OF)


def test_modified_source_capture_is_invalid(original: PredictionSource, tmp_path: Path) -> None:
    from donghak_stock_vision.storage.forward_capture import DB

    future = future_capture(original, tmp_path / "future")
    with (future.capture_root / DB).open("ab") as stream:
        stream.write(b"tampered")
    d = audit_evidence((EvidenceCase(original, future),), AS_OF).to_dict()
    assert d["counts"]["invalid"] == 1


@pytest.mark.parametrize("count", [0, MAX_CASES + 1])
def test_case_budget_before_source_io(original: PredictionSource, count: int) -> None:
    with patch(
        "donghak_stock_vision.storage.prospective_evidence_audit.bind_outcome",
        side_effect=AssertionError("must not load"),
    ):
        with pytest.raises(ValueError, match="case_budget"):
            audit_evidence((EvidenceCase(original),) * count, AS_OF)


def test_duplicate_and_oversized_input_rejected_before_replay(original: PredictionSource) -> None:
    with patch(
        "donghak_stock_vision.storage.prospective_evidence_audit.bind_outcome",
        side_effect=AssertionError("must not load"),
    ):
        with pytest.raises(ValueError, match="duplicate_prediction"):
            audit_evidence((EvidenceCase(original), EvidenceCase(original)), AS_OF)
        large = FrozenJSON.freeze({"large": "x" * MAX_REQUEST_BYTES})
        with pytest.raises(ValueError, match="request_byte_budget"):
            audit_evidence((EvidenceCase(replace(original, request=large)),), AS_OF)


def test_multi_case_counts_and_request_order(tmp_path: Path) -> None:
    sources = []
    for i in range(3):
        root = tmp_path / str(i)
        root.mkdir()
        sources.append(make_original(root, (100 + i, 90 + i, 80 + i, 120 + i)))
    future = future_capture(sources[0], tmp_path / "future")
    cases = (
        EvidenceCase(sources[0], future),
        EvidenceCase(sources[1]),
        EvidenceCase(replace(sources[2], policy_pin="0" * 64)),
    )
    d = audit_evidence(cases, AS_OF).to_dict()
    assert d["total_supplied_cases"] == 3
    assert d["counts"] == {"eligible": 1, "pending": 0, "unavailable": 1, "invalid": 1}
    assert sum(d["reason_counts"].values()) == 3
    assert [c["prediction_id"] for c in d["cases"]] == [c.prediction.prediction_id for c in cases]
    assert audit_evidence(cases, AS_OF).to_dict() == d


@pytest.mark.parametrize("kind", ["pin", "version", "status", "relative_path"])
def test_manifest_is_references_not_caller_status(original: PredictionSource, kind: str) -> None:
    owner = input_owner((EvidenceCase(original),))
    pin = owner.identifier
    if kind == "pin":
        pin = "0" * 64
    else:
        d = owner.to_dict()
        if kind == "version":
            d["version"] = "unknown"
        elif kind == "status":
            d["cases"][0]["status"] = "eligible"
        else:
            d["cases"][0]["prediction"]["journal"] = "relative.sqlite3"
        owner = FrozenJSON.freeze(d)
        pin = owner.identifier
    with pytest.raises(ValueError):
        input_cases(owner, pin)


def test_cli_json_readback_and_invalid_input(
    original: PredictionSource, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = input_owner((EvidenceCase(original),))
    path = tmp_path / "references.json"
    path.write_text(owner.payload_json)
    before = file_bytes(tmp_path)
    args = ["--input", str(path), "--input-pin", owner.identifier, "--as-of", AS_OF.isoformat()]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["unavailable"] == 1
    assert file_bytes(tmp_path) == before
    args[3] = "0" * 64
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2 and "audit_input_invalid" in capsys.readouterr().err


def test_cli_bounds_actual_read(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
    with pytest.raises(SystemExit) as error:
        main(["--input", str(path), "--input-pin", "0" * 64, "--as-of", AS_OF.isoformat()])
    assert error.value.code == 2


def test_aggregate_budget_is_checked_before_any_source_replay(original: PredictionSource) -> None:
    request = FrozenJSON.freeze({"body": "x" * (250 * 1024)})
    cases = tuple(
        EvidenceCase(replace(original, prediction_id=f"{i:064x}", request=request))
        for i in range(9)
    )
    with patch(
        "donghak_stock_vision.storage.prospective_evidence_audit.bind_outcome",
        side_effect=AssertionError("must not load"),
    ):
        with pytest.raises(ValueError, match="input_byte_budget"):
            audit_evidence(cases, AS_OF)


def test_modified_original_journal_fails_independent_verification(
    original: PredictionSource,
) -> None:
    import sqlite3

    cases = (EvidenceCase(original),)
    report = audit_evidence(cases, AS_OF)
    with sqlite3.connect(original.journal) as connection:
        connection.execute("DROP TRIGGER signal_entries_no_update")
    result = audit_evidence(cases, AS_OF).to_dict()
    assert result["counts"]["invalid"] == 1
    assert result["cases"][0]["reason"] == "journal_immutability_guards_missing"
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_audit(report, report.identifier, cases, AS_OF)


def test_evaluation_before_recording_is_invalid(original: PredictionSource) -> None:
    d = audit_evidence((EvidenceCase(original),), RECORDED - timedelta(seconds=1)).to_dict()
    assert d["counts"]["invalid"] == 1
    assert d["cases"][0]["reason"] == "evaluation_before_prediction_recording"


def test_cli_symlink_rejected_without_reading(original: PredictionSource, tmp_path: Path) -> None:
    owner = input_owner((EvidenceCase(original),))
    path = tmp_path / "input.json"
    path.write_text(owner.payload_json)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(SystemExit) as error:
        main(["--input", str(link), "--input-pin", owner.identifier, "--as-of", AS_OF.isoformat()])
    assert error.value.code == 2


def test_cli_invalid_evidence_emits_structured_json(
    original: PredictionSource, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = input_owner((EvidenceCase(replace(original, policy_pin="0" * 64)),))
    path = tmp_path / "input.json"
    path.write_text(owner.payload_json)
    assert (
        main(["--input", str(path), "--input-pin", owner.identifier, "--as-of", AS_OF.isoformat()])
        == 2
    )
    d = json.loads(capsys.readouterr().out)
    assert d["counts"]["invalid"] == 1
    assert str(tmp_path) not in json.dumps(d)
