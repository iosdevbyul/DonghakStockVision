"""Original scoped archives and real verifiers; synthetic transport is never authority."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_capture_scope import NOW as V2_AT
from tests.integration.test_forward_capture_scope import scoped_package
from tests.integration.test_forward_custody import setup
from tests.integration.test_krx_forward_authorization import (
    NOW as SIGNED_AT,
)
from tests.integration.test_krx_forward_authorization import (
    mock_capture,
    permission_fixture,
)
from tests.integration.test_krx_operator_policy import record
from tests.integration.test_krx_recurring import AT as V3_AT
from tests.integration.test_krx_recurring import execute as record_market
from tests.integration.test_krx_recurring import fixture as market_package
from tests.integration.test_prospective_operational_readiness import AT, CONFIG, assess, returned

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardObservationScope
from donghak_stock_vision.forward_capture_cli import offline_plan
from donghak_stock_vision.storage.capture_raw_source_contract import prepare_raw_source_custody
from donghak_stock_vision.storage.forward_capture import DB, _project, verify_capture
from donghak_stock_vision.storage.forward_custody import (
    EXPORTS,
    _custody_scope,
    assess_forward_operations,
    stage_forward_custody,
    verify_forward_custody,
    verify_operations_report,
)
from donghak_stock_vision.storage.prospective_operational_readiness import (
    CaptureSource,
    verify_operational_evidence,
)


def original(tmp: Path, version: int) -> tuple[CaptureSource, datetime]:
    if version == 1:
        plan, owner = setup(tmp / "capture")
        at = AT
    elif version == 2:
        plan, ref, _ = scoped_package(tmp)
        with patch("tests.integration.test_krx_operator_policy.NOW", V2_AT):
            owner = record(tmp / "capture", plan, ref)
        at = V2_AT + timedelta(minutes=1)
    else:
        plan, ref = market_package(tmp)
        owner = record_market(tmp / "capture", plan, ref, count=3)
        at = V3_AT + timedelta(minutes=1)
    return CaptureSource(tmp / "capture", plan, plan.document.identifier, "a", owner.identifier), at


def stage(c: CaptureSource) -> FrozenJSON:
    return stage_forward_custody(c.root, c.plan, c.plan_pin, c.attempt, c.capture_pin)


def operations(c: CaptureSource, **kwargs: Any) -> FrozenJSON:
    return assess_forward_operations(
        c.root, c.plan, c.plan_pin, c.attempt, c.capture_pin, config=CONFIG, **kwargs
    )


def verify(c: CaptureSource, binding: FrozenJSON) -> None:
    verify_forward_custody(
        binding, binding.identifier, c.root, c.plan, c.plan_pin, c.attempt, c.capture_pin
    )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_versioned_original_export_and_canonical_readiness(tmp_path: Path, version: int) -> None:
    c, at = original(tmp_path, version)
    original_capture = verify_capture(c.root, c.plan, c.attempt, c.capture_pin)
    binding = stage(c)
    b = binding.to_dict()
    assert b["capture_manifest_pin"] == c.capture_pin and b["plan_pin"] == c.plan_pin
    assert b["version"] == f"forward_capture_custody_binding_v{1 if version == 1 else 2}"
    scope = b["scope"]
    if version == 1:
        assert scope["partition"] == c.plan.partition and scope["split_pin"] == c.plan.split_pin
        assert "version" not in scope
        # Values recorded from the pre-change implementation using this fixed fixture.
        assert c.capture_pin == "092171d2a33aa997e1391a9f4d852a344ba8fcbde72c8effd5fa434abc361e80"
        assert (
            binding.identifier == "d1cca0d3dc0d0eec9b5f107e3f98e6abcec3f48b48c99fca5c082ef8bcbff4f7"
        )
        assert (
            assess_forward_operations(
                c.root, c.plan, c.plan_pin, c.attempt, c.capture_pin
            ).identifier
            == "97bbb2dab1bfb04d039d5413b1b7a17035f4909c491c050668b81841e785c237"
        )
    else:
        assert scope["version"] == "forward_capture_custody_scope_v2"
        assert scope["capture_plan_version"] == f"forward_capture_plan_v{version}"
        assert not {"split", "split_pin", "partition"}.intersection(scope)
        key = "observation_scope" if version == 2 else "full_market_scope"
        assert scope[key] == c.plan.document.to_dict()[key]
        assert scope["kind"] == ("observation" if version == 2 else "full_market")
    before = {p: p.read_bytes() for p in c.root.rglob("*") if p.is_file()}
    supplied = returned(c)
    with patch("socket.create_connection", side_effect=AssertionError("no network")):
        verify(c, binding)
        assert stage(c) == binding  # Existing idempotent local staging only.
        report = operations(c, returned=supplied)
        verify_operations_report(
            report,
            report.identifier,
            c.root,
            c.plan,
            c.plan_pin,
            c.attempt,
            c.capture_pin,
            config=CONFIG,
            returned=supplied,
        )
        readiness = assess(c, at, returned=supplied)
        d = readiness.to_dict()
        verify_operational_evidence(
            readiness,
            readiness.identifier,
            d["references_pin"],
            c,
            at,
            CONFIG,
            CONFIG.document.identifier,
            returned=supplied,
        )
    assert verify_capture(c.root, c.plan, c.attempt, c.capture_pin) == original_capture
    assert report.to_dict()["scope"] == scope
    assert (
        report.to_dict()["version"]
        == f"forward_capture_operational_readiness_v{1 if version == 1 else 2}"
    )
    assert d["capture_custody_contract_compatible"] and d["custody"]["original_export_verified"]
    assert d["custody"]["supplied_external_receipt"]["content_consistent"]
    assert d["custody"]["supplied_external_readback"]["status"] == "content_consistent_unverified"
    assert d["publication_at"] is None and d["performance_metrics"] is None
    for flag in (
        "genuine_forward_evidence",
        "pit_qualified",
        "execution_authorized",
        "prospective_performance_approved",
        "issuer_independence_verified",
        "independently_authenticated_chronology",
    ):
        assert d[flag] is False
    assert {p: p.read_bytes() for p in c.root.rglob("*") if p.is_file()} == before
    if version == 3:
        assert original_capture.to_dict()["ingestion"] == []
        assert len(original_capture.to_dict()["responses"][0]["parsed_bars"]) == 3
    with closing(sqlite3.connect(f"file:{c.root / DB}?mode=ro", uri=True)) as connection:
        raw = prepare_raw_source_custody(connection, 1, archive_namespace="synthetic")
    assert raw.raw_sha256 == b["raw_sha256"]
    assert raw.receipt_payload() == b["export_payload"]


def test_legacy_signed_review_is_reused_not_reinterpreted(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(
        tmp_path, [replace(offline_plan(), mode="authorized_provider", daily_limit=1)]
    )
    plan = plans[0]
    owner = mock_capture(tmp_path / "capture", plan, ref, trust, at=SIGNED_AT)
    c = CaptureSource(tmp_path / "capture", plan, plan.document.identifier, "a", owner.identifier)
    binding = stage(c)
    verify(c, binding)
    assert binding.to_dict()["version"] == "forward_capture_custody_binding_v1"
    assert not operations(c).to_dict()["independent_custody_verified"]


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("kind", ["scope", "version", "raw", "sequence", "flag", "extra"])
def test_rehashed_custody_binding_tampering(tmp_path: Path, version: int, kind: str) -> None:
    c, _ = original(tmp_path, version)
    d = stage(c).to_dict()
    if kind == "scope":
        d["scope"]["partition"] = "training"
    elif kind == "version":
        d["version"] = "forward_capture_custody_binding_v1"
    elif kind == "raw":
        d["raw_sha256"] = "0" * 64
    elif kind == "sequence":
        d["response_sequence"] = 1
    elif kind == "flag":
        d["independent_custody_verified"] = True
    else:
        d["approved"] = True
    with pytest.raises(ValueError, match="custody_binding_semantic_mismatch"):
        verify(c, FrozenJSON.freeze(d))


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize(
    "kind", ["pin", "attempt", "day", "sequence", "export", "body", "checksum"]
)
def test_wrong_original_identities_and_bytes(tmp_path: Path, version: int, kind: str) -> None:
    c, _ = original(tmp_path, version)
    b = stage(c)
    if kind == "pin":
        c = replace(c, capture_pin="0" * 64)
    elif kind == "attempt":
        c = replace(c, attempt="substituted")
    elif kind == "day":
        day = c.plan.day - timedelta(days=1)
        with pytest.raises(ValueError):
            new = replace(c.plan, day=day)
            verify(replace(c, plan=new, plan_pin=new.document.identifier), b)
        return
    elif kind == "sequence":
        with pytest.raises(ValueError, match="unknown_response"):
            verify_forward_custody(
                b, b.identifier, c.root, c.plan, c.plan_pin, "a", c.capture_pin, 1
            )
        return
    elif kind == "export":
        (c.root / EXPORTS / "raw-1.json").write_bytes(b"{}")
    else:
        with closing(sqlite3.connect(c.root / DB)) as connection, connection:
            column, value = ("body", b"substituted") if kind == "body" else ("sha256", "0" * 64)
            connection.execute(f"UPDATE raw_pages SET {column}=?", (value,))
    with pytest.raises(ValueError):
        verify(c, b)


@pytest.mark.parametrize("version", [2, 3])
def test_missing_invalid_external_claims_and_report_forgery(tmp_path: Path, version: int) -> None:
    c, at = original(tmp_path, version)
    missing = operations(c).to_dict()
    assert not missing["original_export_verified"]
    assert "external_custody_receipt_missing" in missing["blockers"]
    assert not assess(c, at).to_dict()["custody"]["original_export_verified"]
    stage(c)
    value = returned(c)
    raw = b"not an acknowledgement"
    value = replace(value, acknowledgement=raw, acknowledgement_pin=sha256(raw).hexdigest())
    value = replace(value, readback=b"substituted", readback_pin=sha256(b"substituted").hexdigest())
    report = operations(c, returned=value)
    d = report.to_dict()
    assert not d["supplied_external_receipt"]["content_consistent"]
    assert d["supplied_external_readback"]["status"] == "blocked"
    assert "readback_digest_mismatch" in d["blockers"]
    d["scope"]["kind"] = "research"
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="readiness_report_semantic_mismatch"):
        verify_operations_report(
            forged,
            forged.identifier,
            c.root,
            c.plan,
            c.plan_pin,
            "a",
            c.capture_pin,
            config=CONFIG,
            returned=value,
        )


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("kind", ["unknown", "provider", "market", "extra", "cross_version"])
def test_strict_plan_scope_rejection_before_archive_io(
    tmp_path: Path, version: int, kind: str
) -> None:
    c, _ = original(tmp_path, version)
    d = c.plan.document.to_dict()
    if kind == "unknown":
        d["version"] = "forward_capture_plan_v99"
    elif kind == "provider":
        d["provider"] = "substituted"
    elif kind == "market":
        d["market"] = "unsupported"
    elif kind == "extra":
        d["trusted"] = True
    else:
        d["version"] = "forward_capture_plan_v2" if version != 2 else "forward_capture_plan_v3"
    with (
        patch("sqlite3.connect", side_effect=AssertionError("no archive IO")),
        pytest.raises(ValueError),
    ):
        _custody_scope(d)


def test_v2_window_boundary_and_self_rehashed_out_of_window_capture(tmp_path: Path) -> None:
    c, _ = original(tmp_path, 2)
    assert c.plan.observation_scope is not None
    start = c.plan.observation_scope.valid_from
    assert (
        _custody_scope(c.plan.document.to_dict())["observation_scope"]["valid_from"]
        == start.isoformat()
    )
    with pytest.raises(ValueError, match="forward_scope_window_invalid"):
        ForwardObservationScope(start, start + timedelta(hours=24, microseconds=1))
    # Alter original metadata and rehash a locally supplied manifest. This cannot
    # manufacture compatibility with the original declared collection window.
    with closing(sqlite3.connect(c.root / DB)) as connection, connection:
        connection.execute("DROP TRIGGER forward_responses_no_UPDATE")
        connection.execute("DROP TRIGGER forward_manifests_no_UPDATE")
        row = connection.execute("SELECT metadata FROM forward_responses").fetchone()
        metadata = json.loads(row[0])
        metadata["request_started_at"] = (start - timedelta(microseconds=1)).isoformat()
        connection.execute(
            "UPDATE forward_responses SET metadata=?", (FrozenJSON.freeze(metadata).payload_json,)
        )
        capture_id = connection.execute("SELECT capture_id FROM forward_manifests").fetchone()[0]
        owner = _project(connection, c.plan, capture_id)
        connection.execute(
            "UPDATE forward_manifests SET document=?,pin=?", (owner.payload_json, owner.identifier)
        )
    changed = replace(c, capture_pin=owner.identifier)
    with pytest.raises(ValueError, match="custody_capture_outside_declared_scope_window"):
        stage(changed)
    assert not (c.root / EXPORTS).exists()


@pytest.mark.parametrize("version", [2, 3])
def test_original_219_unsupported_scope_reports_still_reconstruct(
    tmp_path: Path, version: int
) -> None:
    from donghak_stock_vision.storage.prospective_operational_readiness import GAPS

    c, at = original(tmp_path, version)
    current = assess(c, at)
    d = current.to_dict()
    # Original #219 v1 contract had explicit unsupported custody scope, not a
    # successful legacy binding. Preserve precisely that historical meaning.
    d.update(
        version="prospective_operational_evidence_readiness_v1",
        operations_report_pin=None,
        capture_custody_contract_compatible=False,
        custody=dict.fromkeys(d["custody"]),
        reason_codes=sorted(
            set(GAPS)
            | {
                "capture_custody_v1_requires_research_scope_absent_in_capture_v2_v3",
                "returned_custody_evidence_not_inspected_for_unsupported_scope",
                "trust_anchor_owner_not_supplied",
                "original_prediction_provenance_not_supplied",
                "future_observation_provenance_unavailable",
                "capture_manifest_has_no_ingestion_completion_timestamp",
                "provider_retrieval_does_not_establish_publication_time_or_prospective_eligibility",
            }
        ),
    )
    legacy = FrozenJSON.freeze(d)
    verify_operational_evidence(
        legacy,
        legacy.identifier,
        d["references_pin"],
        c,
        at,
        CONFIG,
        CONFIG.document.identifier,
    )
    assert not legacy.to_dict()["capture_custody_contract_compatible"]
    for version_label in ("unknown", "prospective_operational_evidence_readiness_v1"):
        forged = FrozenJSON.freeze(current.to_dict() | {"version": version_label})
        with pytest.raises(
            ValueError, match="operational_report_version|operational_semantic_mismatch"
        ):
            verify_operational_evidence(
                forged,
                forged.identifier,
                d["references_pin"],
                c,
                at,
                CONFIG,
                CONFIG.document.identifier,
            )


def test_operational_capture_never_becomes_an_offline_prediction_owner(tmp_path: Path) -> None:
    from tests.integration.test_prospective_evaluation_consumer import sources

    from donghak_stock_vision.storage.prospective_custody_readiness import audit_custody

    (tmp_path / "operational").mkdir()
    (tmp_path / "prediction").mkdir()
    c, at = original(tmp_path / "operational", 3)
    s = sources(tmp_path / "prediction", future=False)
    # The original source owner must pass its own chronology first.
    from tests.integration.test_prediction_binding import AS_OF

    pin = audit_custody(s, AS_OF).to_dict()["references_pin"]
    with pytest.raises(
        ValueError, match="operational_scope_cannot_map_to_offline_prediction_contract"
    ):
        assess(c, max(at, AS_OF), source=s, source_references_pin=pin)


@pytest.mark.parametrize("kind", ["duplicate_day", "nonmember", "endpoint", "extra", "market"])
def test_full_market_allowlist_is_authoritative(tmp_path: Path, kind: str) -> None:
    c, _ = original(tmp_path, 3)
    d = c.plan.document.to_dict()
    scope = d["full_market_scope"]
    if kind == "duplicate_day":
        scope["permitted_days"] *= 2
    elif kind == "nonmember":
        d["day"] = "2025-07-04"
    elif kind == "endpoint":
        scope["endpoint"] = "https://untrusted.invalid/substitute"
    elif kind == "extra":
        scope["independently_verified"] = True
    else:
        scope["market"] = "KOSDAQ"
    with pytest.raises(ValueError):
        _custody_scope(d)


@pytest.mark.parametrize("version", [2, 3])
def test_expired_collection_window_prevents_even_mock_request(tmp_path: Path, version: int) -> None:
    # These are original authorization checks, not a new custody authorization rule.
    if version == 2:
        plan, ref, _ = scoped_package(tmp_path)
        assert plan.observation_scope is not None
        with patch(
            "tests.integration.test_krx_operator_policy.NOW", plan.observation_scope.valid_until
        ):
            with pytest.raises(ValueError):
                record(tmp_path / "capture", plan, ref)
    else:
        plan, ref = market_package(tmp_path)
        assert plan.full_market_scope is not None
        calls: list[int] = []
        with pytest.raises(ValueError):
            record_market(
                tmp_path / "capture",
                plan,
                ref,
                at=plan.full_market_scope.valid_until,
                counter=calls,
            )
        assert not calls


@pytest.mark.parametrize("version", [2, 3])
def test_existing_offline_cli_uses_original_plan_version(
    tmp_path: Path, capsys: Any, version: int
) -> None:
    from donghak_stock_vision.cli import main

    c, _ = original(tmp_path, version)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(c.plan.document.payload_json)
    args = [
        "forward-capture",
        "custody-stage",
        "--root",
        str(c.root),
        "--plan",
        str(plan_file),
        "--plan-pin",
        c.plan_pin,
        "--attempt",
        c.attempt,
        "--pin",
        c.capture_pin,
    ]
    with patch("socket.create_connection", side_effect=AssertionError("no network")):
        assert main(args) == 0
        staged = json.loads(capsys.readouterr().out)
        assert staged["binding"]["version"] == "forward_capture_custody_binding_v2"
        args[1] = "custody-readiness"
        assert main(args) == 0
        report = json.loads(capsys.readouterr().out)["report"]
    assert report["version"] == "forward_capture_operational_readiness_v2"
    assert report["scope"]["capture_plan_version"] == f"forward_capture_plan_v{version}"
    assert report["original_export_verified"] and not report["independent_custody_verified"]
