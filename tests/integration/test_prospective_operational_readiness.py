"""Original offline archives/journals; no mock authentication or external operations."""

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_capture import NOW
from tests.integration.test_forward_custody import setup, stage
from tests.integration.test_prediction_binding import AS_OF
from tests.integration.test_prospective_anchor_governance import ISSUER, KEY_ID, anchor
from tests.integration.test_prospective_evaluation_consumer import sources

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.forward_custody import (
    EXPORTS,
    ForwardOperationsConfig,
    ReturnedCustodyEvidence,
)
from donghak_stock_vision.storage.prospective_anchor_governance import AnchorSnapshot
from donghak_stock_vision.storage.prospective_custody_readiness import audit_custody
from donghak_stock_vision.storage.prospective_operational_readiness import (
    GAPS,
    MAX_REPORT,
    CaptureSource,
    assess_operational_evidence,
    verify_operational_evidence,
)

CONFIG = ForwardOperationsConfig(custodian_id="offline-custodian")
AT = NOW + timedelta(days=1)


def capture(root: Path, *, staged: bool = False) -> CaptureSource:
    plan, owner = setup(root)
    if staged:
        stage(root, plan, owner)
    return CaptureSource(root, plan, plan.document.identifier, "a", owner.identifier)


def assess(c: CaptureSource, at: Any = AT, **kwargs: Any) -> FrozenJSON:
    return assess_operational_evidence(c, at, CONFIG, CONFIG.document.identifier, **kwargs)


def returned(c: CaptureSource, **changes: Any) -> ReturnedCustodyEvidence:
    exported = (c.root / EXPORTS / "raw-1.json").read_bytes()
    ack = json.dumps(
        dict(
            schemaVersion="capture_custody_ack_v1",
            custodianId="offline-custodian",
            receiptId="synthetic-returned-receipt",
            rawId=1,
            acceptedAt=AT.isoformat(),
            receiptSha256=sha256(exported).hexdigest(),
            **changes,
        )
    ).encode()
    return ReturnedCustodyEvidence(
        ack, sha256(ack).hexdigest(), exported, sha256(exported).hexdigest()
    )


def test_original_capture_export_readback_and_local_claims(tmp_path: Path) -> None:
    c = capture(tmp_path, staged=True)
    supplied = returned(c)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch("httpx.Client.send", side_effect=AssertionError("no network")):
        report = assess(c, returned=supplied)
        d = report.to_dict()
        verify_operational_evidence(
            report,
            report.identifier,
            d["references_pin"],
            c,
            AT,
            CONFIG,
            CONFIG.document.identifier,
            returned=supplied,
        )
    assert d["capture_replayed"] and d["capture_custody_contract_compatible"]
    assert d["custody"]["original_export_verified"]
    assert d["custody"]["supplied_external_receipt"]["content_consistent"]
    assert d["custody"]["supplied_external_readback"]["status"] == "content_consistent_unverified"
    assert d["issuer_asserted_custody_at"] == AT.isoformat()
    assert d["local_times"]["ingestion_completed_at"] is None
    assert d["independently_authenticated_at"] is None and d["publication_at"] is None
    assert set(GAPS) <= set(d["reason_codes"])
    assert "original_prediction_provenance_not_supplied" in d["reason_codes"]
    assert d["performance_metrics"] is None and d["network_requests"] == 0
    for flag in (
        "genuine_forward_evidence",
        "pit_qualified",
        "prospective_performance_approved",
        "execution_authorized",
        "issuer_independence_verified",
        "independently_authenticated_chronology",
        "operational_persistence_verified",
    ):
        assert d[flag] is False
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert str(tmp_path) not in report.payload_json


def test_missing_custody_and_prediction_are_explicit(tmp_path: Path) -> None:
    c = capture(tmp_path)
    d = assess(c).to_dict()
    assert d["declared_predictions"] == 0 and d["cases"] == []
    assert d["custody"]["supplied_external_receipt"] is None
    assert "external_custody_receipt_missing" in d["reason_codes"]
    assert "external_custody_readback_missing" in d["reason_codes"]
    assert not (tmp_path / EXPORTS).exists()


@pytest.mark.parametrize("future", [False, True])
def test_prediction_input_exact_binding_and_horizon(tmp_path: Path, future: bool) -> None:
    s = sources(tmp_path, future=future)
    original = s.cases[0].prediction
    first = original.request.to_dict()["sources"][0]
    c = CaptureSource(
        original.capture_root,
        ForwardCapturePlan.from_dict(first["plan"]),
        first["plan_pin"],
        first["attempt"],
        first["capture_pin"],
    )
    pin = audit_custody(s, AS_OF).to_dict()["references_pin"]
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = assess(c, AS_OF, source=s, source_references_pin=pin)
    d = report.to_dict()
    row = d["cases"][0]
    assert d["declared_predictions"] == 1
    assert d["policy_pin"] == s.policy_pin and d["cohort_pin"] == s.cohort_pin
    assert d["horizon_pin"] == s.horizon_pin
    assert row["prediction_id"] == original.prediction_id
    assert row["capture_is_original_prediction_input"]
    assert row["local_future_state"] == ("verified" if future else "unavailable")
    assert bool(row["local_future_timestamps"]) == future
    assert assess(replace(c), AS_OF, source=replace(s), source_references_pin=pin) == report
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    if not future:
        with pytest.raises(ValueError, match="operational_source_references_pin"):
            assess(c, AS_OF, source=s, source_references_pin="0" * 64)
        with pytest.raises(ValueError, match="prediction_policy_pin_mismatch"):
            assess(c, AS_OF, source=replace(s, horizon_pin="0" * 64), source_references_pin=pin)


def test_unrelated_capture_cannot_be_relabelled_as_prediction_source(tmp_path: Path) -> None:
    s = sources(tmp_path / "prediction", future=False)
    c = capture(tmp_path / "unrelated-retrospective-record")
    pin = audit_custody(s, AS_OF).to_dict()["references_pin"]
    with pytest.raises(ValueError, match="capture_not_original_prediction_input"):
        assess(c, AS_OF, source=s, source_references_pin=pin)


def test_operational_v2_is_verified_without_fabricated_research_membership(tmp_path: Path) -> None:
    from tests.integration.test_forward_capture_scope import NOW as PROVIDER_NOW
    from tests.integration.test_forward_capture_scope import scoped_package
    from tests.integration.test_krx_operator_policy import record

    # Synthetic policy documents and explicit MockTransport, not actual provider evidence.
    plan, auth, _ = scoped_package(tmp_path)
    root = tmp_path / "capture"
    with patch("tests.integration.test_krx_operator_policy.NOW", PROVIDER_NOW):
        owner = record(root, plan, auth)
    c = CaptureSource(root, plan, plan.document.identifier, "a", owner.identifier)
    d = assess(c, PROVIDER_NOW + timedelta(minutes=1)).to_dict()
    assert d["capture_replayed"] and d["capture_custody_contract_compatible"]
    assert d["capture_plan_version"] == "forward_capture_plan_v2"
    assert d["version"] == "prospective_operational_evidence_readiness_v2"
    assert (
        "capture_custody_v1_requires_research_scope_absent_in_capture_v2_v3"
        not in d["reason_codes"]
    )
    assert "custody_export_missing_or_invalid" in d["reason_codes"]
    assert d["custody"]["original_export_verified"] is False
    assert not d["genuine_forward_evidence"]


@pytest.mark.parametrize("kind", ["capture_pin", "plan_pin", "sequence", "config_pin", "version"])
def test_invalid_original_references(tmp_path: Path, kind: str) -> None:
    c = capture(tmp_path)
    if kind == "config_pin":
        with pytest.raises(ValueError, match="operational_config_pin"):
            assess_operational_evidence(c, AT, CONFIG, "0" * 64)
    elif kind == "version":
        with pytest.raises(ValueError):
            ForwardCapturePlan.from_dict({**c.plan.document.to_dict(), "version": "unknown"})
    else:
        value: Any = 99 if kind == "sequence" else "0" * 64
        with pytest.raises(ValueError):
            assess(replace(c, **{kind: value}))


def test_modified_original_artifact_is_rejected(tmp_path: Path) -> None:
    c = capture(tmp_path)
    connection = sqlite3.connect(tmp_path / DB)
    try:
        connection.execute("UPDATE raw_pages SET body=?", (b"forged",))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError):
        assess(c)


def test_configuration_is_not_independent_issuer_authentication(tmp_path: Path) -> None:
    c = capture(tmp_path)
    a = anchor()
    report = assess(
        c,
        anchor=AnchorSnapshot(a, a.identifier),
        expected_issuer=ISSUER,
        expected_key_id=KEY_ID,
    ).to_dict()
    assert report["governance_report_pin"] is not None
    assert not report["issuer_independence_verified"]
    assert "historical_revocation_completeness_and_freshness_unverified" in report["reason_codes"]
    with pytest.raises(ValueError, match="governance_anchor_pin_mismatch"):
        assess(
            c, anchor=AnchorSnapshot(a, "0" * 64), expected_issuer=ISSUER, expected_key_id=KEY_ID
        )
    with pytest.raises(ValueError, match="governance_issuer_mismatch"):
        assess(
            c,
            anchor=AnchorSnapshot(a, a.identifier),
            expected_issuer="substituted",
            expected_key_id=KEY_ID,
        )


@pytest.mark.parametrize("kind", ["flags", "references", "version", "unknown", "oversize"])
def test_rehashed_report_forgery(tmp_path: Path, kind: str) -> None:
    c = capture(tmp_path)
    original = assess(c)
    d = original.to_dict()
    if kind == "flags":
        d["genuine_forward_evidence"] = True
    elif kind == "references":
        d["capture_id"] = "0" * 64
    elif kind == "version":
        d["version"] = "unknown"
    elif kind == "unknown":
        d["approved"] = True
    else:
        d["extra"] = "x" * MAX_REPORT
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError):
        verify_operational_evidence(
            forged,
            forged.identifier,
            original.to_dict()["references_pin"],
            c,
            AT,
            CONFIG,
            CONFIG.document.identifier,
        )


def test_caller_flags_and_oversized_returned_bytes_are_not_accepted(tmp_path: Path) -> None:
    c = capture(tmp_path)
    with pytest.raises(TypeError):
        assess(c, genuine_forward_evidence=True)
    with pytest.raises(ValueError):
        ReturnedCustodyEvidence(b"x" * 65537, "0" * 64)


def test_issuer_asserted_chronology_is_not_authenticated(tmp_path: Path) -> None:
    c = capture(tmp_path, staged=True)
    value = returned(c)
    doc = json.loads(value.acknowledgement or b"{}")
    doc["acceptedAt"] = (AT + timedelta(days=1)).isoformat()
    raw = json.dumps(doc).encode()
    value = replace(value, acknowledgement=raw, acknowledgement_pin=sha256(raw).hexdigest())
    d = assess(c, returned=value).to_dict()
    assert "issuer_asserted_custody_chronology_inconsistent" in d["reason_codes"]
    assert not d["independently_authenticated_chronology"]


def test_wrong_custodian_and_retained_ack_pin_remain_invalid(tmp_path: Path) -> None:
    c = capture(tmp_path, staged=True)
    value = returned(c)
    doc = json.loads(value.acknowledgement or b"{}")
    doc["custodianId"] = "substituted"
    raw = json.dumps(doc).encode()
    value = replace(value, acknowledgement=raw)
    d = assess(c, returned=value).to_dict()
    assert not d["custody"]["supplied_external_receipt"]["content_consistent"]
    assert d["issuer_asserted_custody_at"] is None
    assert "custody_ack_identity_mismatch" in d["reason_codes"]
    assert "custody_ack_independent_pin_mismatch" in d["reason_codes"]


def test_output_bound_is_enforced(tmp_path: Path) -> None:
    c = capture(tmp_path)
    with (
        patch("donghak_stock_vision.storage.prospective_operational_readiness.MAX_REPORT", 1),
        pytest.raises(ValueError, match="operational_report_byte_budget"),
    ):
        assess(c)


def test_case_bound_and_protected_partition_before_payload_io(tmp_path: Path) -> None:
    s = sources(tmp_path, future=False)
    c = capture(tmp_path / "separate")
    with pytest.raises(ValueError, match="case_budget"):
        assess(c, AS_OF, source=replace(s, cases=s.cases * 33), source_references_pin="0" * 64)
    with patch("sqlite3.connect", side_effect=AssertionError("no payload read")):
        with pytest.raises(ValueError, match="forbidden_partition"):
            replace(c.plan, partition="final_test")
        with pytest.raises(ValueError, match="operational_original_capture_owner_required"):
            assess({"approved": True})  # type: ignore[arg-type]


def test_custody_mutation_between_boundaries_fails(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.forward_custody import assess_forward_operations

    c = capture(tmp_path, staged=True)
    calls = 0

    def changed(*args: Any, **kwargs: Any) -> FrozenJSON:
        nonlocal calls
        calls += 1
        if calls == 2:
            connection = sqlite3.connect(tmp_path / DB)
            try:
                connection.execute("UPDATE capture_custody_outbox SET attempt_count=1")
                connection.commit()
            finally:
                connection.close()
        return assess_forward_operations(*args, **kwargs)

    with (
        patch(
            "donghak_stock_vision.storage.prospective_operational_readiness.assess_forward_operations",
            side_effect=changed,
        ),
        pytest.raises(ValueError, match="operational_capture_changed_during_assessment"),
    ):
        assess(c)


def test_pending_gate_does_not_load_future_archive(tmp_path: Path) -> None:
    from tests.integration.test_prospective_prediction import RECORDED

    s = sources(tmp_path)
    case = s.cases[0]
    assert case.future is not None
    # A future owner may be declared, but its payload cannot be read before the gate.
    s = replace(
        s, cases=(replace(case, future=replace(case.future, capture_root=tmp_path / "absent")),)
    )
    original = case.prediction
    first = original.request.to_dict()["sources"][0]
    c = CaptureSource(
        original.capture_root,
        ForwardCapturePlan.from_dict(first["plan"]),
        first["plan_pin"],
        first["attempt"],
        first["capture_pin"],
    )
    pin = audit_custody(s, RECORDED).to_dict()["references_pin"]
    d = assess(c, RECORDED, source=s, source_references_pin=pin).to_dict()
    assert d["cases"][0]["local_future_state"] == "pending"
    assert d["cases"][0]["local_future_timestamps"] is None
    assert not (tmp_path / "absent").exists()


def test_unrelated_archive_rejected_before_capture_payload_read(tmp_path: Path) -> None:
    s = sources(tmp_path / "original", future=False)
    c = capture(tmp_path / "unrelated")
    pin = audit_custody(s, AS_OF).to_dict()["references_pin"]
    with (
        patch(
            "donghak_stock_vision.storage.prospective_operational_readiness.verify_capture",
            side_effect=AssertionError("unrelated payload must not be read"),
        ),
        pytest.raises(ValueError, match="capture_not_original_prediction_input"),
    ):
        assess(c, AS_OF, source=s, source_references_pin=pin)


def test_retained_report_and_reference_pins_are_required(tmp_path: Path) -> None:
    c = capture(tmp_path)
    report = assess(c)
    for report_pin, reference_pin in (
        ("0" * 64, report.to_dict()["references_pin"]),
        (report.identifier, "0" * 64),
    ):
        with pytest.raises(ValueError, match="operational_report_pin|operational_reference_pin"):
            verify_operational_evidence(
                report,
                report_pin,
                reference_pin,
                c,
                AT,
                CONFIG,
                CONFIG.document.identifier,
            )


def test_provider_mode_does_not_claim_missing_credentials_or_authenticate_origin(
    tmp_path: Path,
) -> None:
    from tests.integration.test_krx_forward_authorization import NOW as PROVIDER_NOW
    from tests.integration.test_krx_operator_policy import package, record

    # Existing synthetic authorization documents and explicitly mocked transport only.
    plan, ref, _ = package(tmp_path)
    root = tmp_path / "capture"
    owner = record(root, plan, ref)
    c = CaptureSource(root, plan, plan.document.identifier, "a", owner.identifier)
    d = assess(c, PROVIDER_NOW + timedelta(minutes=1)).to_dict()
    assert "missing_krx_api_key" not in d["reason_codes"]
    assert "credential_presence_not_assessed" in d["reason_codes"]
    assert not d["credential_presence_assessed"]
    assert d["origin_assurance"] == "declared_capture_mode_not_independently_authenticated"
    assert not d["genuine_forward_evidence"]
