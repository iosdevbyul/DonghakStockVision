"""Offline capture -> original export -> pending outbox -> local readback; no uploads."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_capture import execute

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.forward_capture_cli import offline_plan
from donghak_stock_vision.storage.capture_custody_idempotency import (
    prepare_custody_submission_identity,
)
from donghak_stock_vision.storage.forward_capture import DB, verify_capture
from donghak_stock_vision.storage.forward_custody import (
    EXPORTS,
    ForwardOperationsConfig,
    ReturnedCustodyEvidence,
    assess_forward_operations,
    stage_forward_custody,
    verify_forward_custody,
    verify_operations_report,
)


def setup(root: Path) -> tuple[Any, FrozenJSON]:
    plan = offline_plan()
    owner = execute(root)
    return plan, owner


def stage(root: Path, plan: Any, owner: FrozenJSON, sequence: int = 0) -> FrozenJSON:
    return stage_forward_custody(
        root, plan, plan.document.identifier, "a", owner.identifier, sequence
    )


def assess(root: Path, plan: Any, owner: FrozenJSON, **kwargs: Any) -> FrozenJSON:
    return assess_forward_operations(
        root, plan, plan.document.identifier, "a", owner.identifier, **kwargs
    )


def test_complete_offline_workflow(tmp_path: Path) -> None:
    plan, original = setup(tmp_path / "pilot")
    root = tmp_path / "pilot"
    before = assess(root, plan, original).to_dict()
    assert before["outbox"] is None and before["offline_staging_ready"]
    binding = stage(root, plan, original)
    verify_forward_custody(
        binding, binding.identifier, root, plan, plan.document.identifier, "a", original.identifier
    )
    report = assess(root, plan, original)
    doc = report.to_dict()
    assert doc["capture_locally_verified"] and doc["original_export_verified"]
    assert doc["outbox"]["state"] == "pending" and doc["outbox"]["attempt_count"] == 0
    assert doc["local_export_readback"]["status"] == "content_consistent_unverified"
    assert doc["supplied_external_receipt"] is None and doc["supplied_external_readback"] is None
    assert doc["external_submission_id"] is None and not doc["independent_custody_verified"]
    assert doc["network_requests"] == 0 and not doc["publication_authenticated"]
    assert "external_custody_receipt_missing" in doc["blockers"]
    assert "backup_restore_not_verified" in doc["blockers"]
    assert doc["binding_pin"] == binding.identifier
    assert verify_capture(root, plan, "a", original.identifier) == original
    verify_operations_report(
        report, report.identifier, root, plan, plan.document.identifier, "a", original.identifier
    )
    exported = root / EXPORTS / "raw-1.json"
    assert json.loads(exported.read_bytes()) == original.to_dict()["responses"][0]["receipt"]
    with closing(sqlite3.connect(root / DB)) as connection:
        identity = prepare_custody_submission_identity(connection, root / EXPORTS, 1)
        assert identity.idempotency_key == binding.to_dict()["idempotency_key"]


def test_duplicate_staging_preserves_outbox_attempts(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    first = stage(tmp_path, plan, owner)
    export = (tmp_path / EXPORTS / "raw-1.json").read_bytes()
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute(
            "UPDATE capture_custody_outbox SET attempt_count=2,state='manual_review',"
            "last_blocker='custodian_not_authenticated'"
        )
    assert stage(tmp_path, plan, owner) == first
    assert (tmp_path / EXPORTS / "raw-1.json").read_bytes() == export
    report = assess(tmp_path, plan, owner).to_dict()
    assert report["outbox"]["attempt_count"] == 2 and report["outbox"]["state"] == "manual_review"
    assert "custody_outbox_manual_review" in report["blockers"]


def test_readiness_status_and_readback_are_read_only(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    # Before staging no schema or directory is automatically provisioned by assessment.
    before = (tmp_path / DB).read_bytes()
    assess(tmp_path, plan, owner)
    assert (tmp_path / DB).read_bytes() == before and not (tmp_path / EXPORTS).exists()
    binding = stage(tmp_path, plan, owner)
    before = (tmp_path / DB).read_bytes()
    report = assess(tmp_path, plan, owner)
    assert report == assess(tmp_path, plan, owner)
    verify_forward_custody(
        binding, binding.identifier, tmp_path, plan, plan.document.identifier, "a", owner.identifier
    )
    assert (tmp_path / DB).read_bytes() == before


def test_missing_manifest_is_blocked_without_export(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute("DROP TRIGGER forward_manifests_no_DELETE")
        connection.execute("DELETE FROM forward_manifests")
    with pytest.raises(ValueError, match="capture_incomplete_or_missing"):
        stage(tmp_path, plan, owner)
    assert not (tmp_path / EXPORTS).exists()


@pytest.mark.parametrize(
    "sql,args",
    [
        ("UPDATE raw_pages SET body=?", (b"modified",)),
        ("UPDATE raw_pages SET ticker='@krx_market:KOSDAQ'", ()),
        ("UPDATE raw_pages SET provider='wrong'", ()),
    ],
)
def test_modified_source_rejected_before_staging(
    tmp_path: Path, sql: str, args: tuple[Any, ...]
) -> None:
    plan, owner = setup(tmp_path)
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute(sql, args)
    with pytest.raises(ValueError):
        stage(tmp_path, plan, owner)
    assert not (tmp_path / EXPORTS).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_id", 2),
        ("capture_manifest_pin", "0" * 64),
        ("export_sha256", "0" * 64),
        ("response_fingerprint", "0" * 64),
        ("ingestion_fingerprint", "0" * 64),
        ("publication_authenticated", True),
    ],
)
def test_rehashed_forged_binding_rejected(tmp_path: Path, field: str, value: Any) -> None:
    plan, owner = setup(tmp_path)
    binding = stage(tmp_path, plan, owner)
    document = binding.to_dict()
    document[field] = value
    forged = FrozenJSON.freeze(document)
    with pytest.raises(ValueError, match="custody_binding_semantic_mismatch"):
        verify_forward_custody(
            forged,
            forged.identifier,
            tmp_path,
            plan,
            plan.document.identifier,
            "a",
            owner.identifier,
        )
    with pytest.raises(ValueError, match="binding_pin_mismatch"):
        verify_forward_custody(
            forged,
            binding.identifier,
            tmp_path,
            plan,
            plan.document.identifier,
            "a",
            owner.identifier,
        )


def test_rehashed_readiness_cannot_authenticate_time_or_custody(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    stage(tmp_path, plan, owner)
    value = assess(tmp_path, plan, owner).to_dict()
    value["independent_custody_verified"] = True
    value["publication_authenticated"] = True
    forged = FrozenJSON.freeze(value)
    with pytest.raises(ValueError, match="readiness_report_semantic_mismatch"):
        verify_operations_report(
            forged,
            forged.identifier,
            tmp_path,
            plan,
            plan.document.identifier,
            "a",
            owner.identifier,
        )


def test_interrupted_enqueue_recovers_existing_export(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    with patch(
        "donghak_stock_vision.storage.forward_custody.enqueue_capture_receipt",
        side_effect=KeyboardInterrupt,
    ):
        with pytest.raises(KeyboardInterrupt):
            stage(tmp_path, plan, owner)
    exported = (tmp_path / EXPORTS / "raw-1.json").read_bytes()
    assert assess(tmp_path, plan, owner).to_dict()["outbox"] is None
    stage(tmp_path, plan, owner)
    assert (tmp_path / EXPORTS / "raw-1.json").read_bytes() == exported
    assert assess(tmp_path, plan, owner).to_dict()["outbox"]["attempt_count"] == 0


def test_partial_export_is_retained_and_never_overwritten(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)

    def partial(*args: Any, **kwargs: Any) -> Any:
        target = tmp_path / EXPORTS / "raw-1.json"
        target.write_bytes(b"partial")
        target.chmod(0o600)
        raise KeyboardInterrupt

    with patch(
        "donghak_stock_vision.storage.forward_custody.export_capture_receipt", side_effect=partial
    ):
        with pytest.raises(KeyboardInterrupt):
            stage(tmp_path, plan, owner)
    with pytest.raises(ValueError, match="existing_export_incomplete_or_mismatched"):
        stage(tmp_path, plan, owner)
    assert (tmp_path / EXPORTS / "raw-1.json").read_bytes() == b"partial"
    assert not assess(tmp_path, plan, owner).to_dict()["original_export_verified"]


@pytest.mark.parametrize("partition", ["final_test", "final_holdout", "oos"])
def test_forbidden_partitions_before_any_io(tmp_path: Path, partition: str) -> None:
    plan = offline_plan()
    object.__setattr__(plan, "partition", partition)
    with pytest.raises(ValueError, match="forbidden_partition"):
        stage_forward_custody(tmp_path / "absent", plan, "0" * 64, "a", "0" * 64)
    with pytest.raises(ValueError, match="forbidden_partition"):
        assess_forward_operations(tmp_path / "absent", plan, "0" * 64, "a", "0" * 64)
    assert not (tmp_path / "absent").exists()


def test_wrong_plan_pin_and_market(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    with pytest.raises(ValueError, match="independent_plan_pin_mismatch"):
        stage_forward_custody(tmp_path, plan, "0" * 64, "a", owner.identifier)
    with pytest.raises(ValueError):
        stage(tmp_path, replace(plan, market="KOSDAQ"), owner)
    assert not (tmp_path / EXPORTS).exists()


def test_unknown_response_index_and_missing_receipt(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    with pytest.raises(ValueError, match="unknown_response"):
        stage(tmp_path, plan, owner, 1)
    with closing(sqlite3.connect(tmp_path / DB)) as connection, connection:
        connection.execute("DROP TRIGGER raw_capture_receipts_no_delete")
        connection.execute("DELETE FROM raw_capture_receipts")
    with pytest.raises(ValueError, match="capture receipt missing"):
        stage(tmp_path, plan, owner)


def test_permissions_block_and_symlink_export_not_read(tmp_path: Path) -> None:
    root = tmp_path / "pilot"
    plan, owner = setup(root)
    root.chmod(0o755)
    assert not assess(root, plan, owner).to_dict()["offline_staging_ready"]
    with pytest.raises(ValueError, match="private_pilot_storage_required"):
        stage(root, plan, owner)
    root.chmod(0o700)
    outside = tmp_path / "unapproved"
    outside.mkdir()
    (root / EXPORTS).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="private_export_directory_required"):
        stage(root, plan, owner)
    report = assess(root, plan, owner).to_dict()
    assert not report["original_export_verified"]
    assert not list(outside.iterdir())


def acknowledgement(raw_id: int, receipt_hash: str) -> bytes:
    # Explicit synthetic negative evidence. Never an actual external custodian response.
    return json.dumps(
        {
            "schemaVersion": "capture_custody_ack_v1",
            "custodianId": "synthetic-custodian",
            "receiptId": "synthetic-receipt",
            "rawId": raw_id,
            "acceptedAt": "2025-07-02T00:00:00+00:00",
            "receiptSha256": receipt_hash,
        }
    ).encode()


def test_supplied_claims_are_not_independent_authority(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    binding = stage(tmp_path, plan, owner)
    exported = (tmp_path / EXPORTS / "raw-1.json").read_bytes()
    ack = acknowledgement(1, binding.to_dict()["export_sha256"])
    returned = ReturnedCustodyEvidence(
        ack, hashlib.sha256(ack).hexdigest(), exported, hashlib.sha256(exported).hexdigest()
    )
    config = ForwardOperationsConfig(
        1,
        "operator-frequency-review",
        "operator-endpoint",
        "synthetic-custodian",
        "operator-retention",
        "operator-backup",
    )
    report = assess(
        tmp_path, plan, owner, config=config, returned=returned, credential_present=True
    ).to_dict()
    assert report["supplied_external_receipt"]["content_consistent"]
    assert report["supplied_external_receipt"]["custodian_receipt_id"] == "synthetic-receipt"
    assert report["supplied_external_readback"]["status"] == "content_consistent_unverified"
    assert not report["independent_custody_verified"] and not report["publication_authenticated"]
    assert not report["retention_independently_verified"] and not report["backup_restore_verified"]
    assert "provider_service_authorization_not_independently_verified" in report["blockers"]


@pytest.mark.parametrize(
    "kind", ["invalid", "wrong-raw", "wrong-receipt", "wrong-pin", "bad-readback", "readback-pin"]
)
def test_invalid_external_claims_and_readback(tmp_path: Path, kind: str) -> None:
    plan, owner = setup(tmp_path)
    binding = stage(tmp_path, plan, owner)
    ack = acknowledgement(
        2 if kind == "wrong-raw" else 1,
        "0" * 64 if kind == "wrong-receipt" else binding.to_dict()["export_sha256"],
    )
    if kind == "invalid":
        ack = b"not-json"
    readback = (
        b"wrong export"
        if kind == "bad-readback"
        else (tmp_path / EXPORTS / "raw-1.json").read_bytes()
    )
    returned = ReturnedCustodyEvidence(
        ack,
        "0" * 64 if kind == "wrong-pin" else hashlib.sha256(ack).hexdigest(),
        readback,
        "0" * 64 if kind == "readback-pin" else hashlib.sha256(readback).hexdigest(),
    )
    report = assess(
        tmp_path,
        plan,
        owner,
        config=ForwardOperationsConfig(custodian_id="synthetic-custodian"),
        returned=returned,
    ).to_dict()
    assert not report["independent_custody_verified"]
    if kind in ("bad-readback", "readback-pin"):
        assert any("readback" in reason and ("mismatch" in reason) for reason in report["blockers"])
    else:
        assert not report["supplied_external_receipt"]["content_consistent"]


def test_bad_permitted_frequency_and_no_secret_output(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    report = assess(
        tmp_path, plan, owner, config=ForwardOperationsConfig(60, "review"), credential_present=True
    )
    assert "configured_collection_frequency_exceeds_limit" in report.to_dict()["blockers"]
    assert report.to_dict()["credential_present"] is True
    assert "AUTH_KEY" not in report.payload_json


def test_deterministic_bindings_in_independent_archives(tmp_path: Path) -> None:
    first_plan, first_owner = setup(tmp_path / "one")
    second_plan, second_owner = setup(tmp_path / "two")
    assert stage(tmp_path / "one", first_plan, first_owner) == stage(
        tmp_path / "two", second_plan, second_owner
    )


def test_cli_offline_custody_operations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan, owner = setup(tmp_path)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.document.payload_json)
    common = [
        "--root",
        str(tmp_path),
        "--plan",
        str(plan_file),
        "--plan-pin",
        plan.document.identifier,
        "--attempt",
        "a",
        "--pin",
        owner.identifier,
    ]
    assert main(["forward-capture", "custody-readiness", *common]) == 0
    assert json.loads(capsys.readouterr().out)["report"]["outbox"] is None
    # Any accidental submission through established entry points would fail this test.
    with patch(
        "donghak_stock_vision.storage.capture_custody_outbox.process_outbox",
        side_effect=AssertionError("network forbidden"),
    ):
        assert main(["forward-capture", "custody-stage", *common]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["submission_performed"] is False
    binding_file = tmp_path / "binding.json"
    binding_file.write_text(json.dumps(result["binding"]))
    assert (
        main(
            [
                "forward-capture",
                "custody-verify",
                *common,
                "--binding",
                str(binding_file),
                "--binding-pin",
                result["binding_pin"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["locally_verified"]
    for action in ("custody-status", "custody-readback"):
        assert main(["forward-capture", action, *common]) == 0
        doc = json.loads(capsys.readouterr().out)["report"]
        assert doc["outbox"]["attempt_count"] == 0 and not doc["independent_custody_verified"]
    assert main(["forward-capture", "custody-stage", *common, "--allow-network"]) == 2


def test_wrong_export_payload_and_missing_export(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    binding = stage(tmp_path, plan, owner)
    path = tmp_path / EXPORTS / "raw-1.json"
    original = path.read_bytes()
    document = json.loads(original)
    document["rawId"] = 2
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        verify_forward_custody(
            binding,
            binding.identifier,
            tmp_path,
            plan,
            plan.document.identifier,
            "a",
            owner.identifier,
        )
    with pytest.raises(ValueError, match="existing_export_incomplete_or_mismatched"):
        stage(tmp_path, plan, owner)
    path.unlink()
    report = assess(tmp_path, plan, owner).to_dict()
    assert "custody_export_missing_or_invalid" in report["blockers"]


def test_configuration_and_evidence_fail_closed() -> None:
    config = ForwardOperationsConfig()
    assert ForwardOperationsConfig.from_dict(config.document.to_dict()) == config
    with pytest.raises(ValueError):
        ForwardOperationsConfig(permitted_interval_seconds=0)
    with pytest.raises(ValueError):
        ReturnedCustodyEvidence(b"receipt", None)
    with pytest.raises(ValueError):
        ReturnedCustodyEvidence(readback=b"x" * 65537, readback_pin="0" * 64)


def test_private_exports_and_oversize_guard(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    stage(tmp_path, plan, owner)
    path = tmp_path / EXPORTS / "raw-1.json"
    path.chmod(0o644)
    assert not assess(tmp_path, plan, owner).to_dict()["offline_staging_ready"]
    with pytest.raises(ValueError, match="existing_export_incomplete_or_mismatched"):
        stage(tmp_path, plan, owner)
    path.chmod(0o600)
    path.write_bytes(b"x" * 65537)
    assert not assess(tmp_path, plan, owner).to_dict()["original_export_verified"]
    with pytest.raises(ValueError):
        stage(tmp_path, plan, owner)


def test_stage_recovery_after_enqueue_commit(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    with patch(
        "donghak_stock_vision.storage.forward_custody._binding", side_effect=KeyboardInterrupt
    ):
        with pytest.raises(KeyboardInterrupt):
            stage(tmp_path, plan, owner)
    resumed = stage(tmp_path, plan, owner)
    verify_forward_custody(
        resumed, resumed.identifier, tmp_path, plan, plan.document.identifier, "a", owner.identifier
    )
    assert assess(tmp_path, plan, owner).to_dict()["outbox"]["attempt_count"] == 0


def test_returned_receipt_requires_original_export(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    ack = acknowledgement(1, "0" * 64)
    report = assess(
        tmp_path,
        plan,
        owner,
        config=ForwardOperationsConfig(custodian_id="synthetic-custodian"),
        returned=ReturnedCustodyEvidence(ack, hashlib.sha256(ack).hexdigest()),
    ).to_dict()
    assert report["supplied_external_receipt"] is None
    assert "external_receipt_not_inspected_without_verified_export" in report["blockers"]


def test_operations_config_wrong_version_and_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    value = ForwardOperationsConfig().document.to_dict()
    value["version"] = "unknown"
    with pytest.raises(ValueError, match="operations_config_version"):
        ForwardOperationsConfig.from_dict(value)
    plan, owner = setup(tmp_path)
    file = tmp_path / "plan.json"
    file.write_text(plan.document.payload_json)
    config = tmp_path / "ops.json"
    config.write_text(ForwardOperationsConfig().document.payload_json)
    assert (
        main(
            [
                "forward-capture",
                "custody-readiness",
                "--root",
                str(tmp_path),
                "--plan",
                str(file),
                "--plan-pin",
                plan.document.identifier,
                "--attempt",
                "a",
                "--pin",
                owner.identifier,
                "--operations-config",
                str(config),
                "--operations-config-pin",
                "0" * 64,
            ]
        )
        == 2
    )


def test_retry_responses_have_distinct_exact_bindings(tmp_path: Path) -> None:
    import httpx

    from donghak_stock_vision.forward_capture_cli import fixture_transport

    plan = replace(offline_plan(), max_requests=2)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, content=b"retryable failure")
        return fixture_transport(plan).handle_request(request)

    owner = execute(tmp_path, plan, transport=httpx.MockTransport(handler))
    first = stage(tmp_path, plan, owner, 0)
    second = stage(tmp_path, plan, owner, 1)
    assert first.to_dict()["raw_id"] == 1 and second.to_dict()["raw_id"] == 2
    assert first.to_dict()["response_sequence"] == 0 and second.to_dict()["response_sequence"] == 1
    assert first.to_dict()["idempotency_key"] != second.to_dict()["idempotency_key"]
    verify_forward_custody(
        second,
        second.identifier,
        tmp_path,
        plan,
        plan.document.identifier,
        "a",
        owner.identifier,
        1,
    )
    with pytest.raises(ValueError, match="custody_binding_semantic_mismatch"):
        verify_forward_custody(
            first,
            first.identifier,
            tmp_path,
            plan,
            plan.document.identifier,
            "a",
            owner.identifier,
            1,
        )


def test_external_evidence_cannot_change_source_rows(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    stage(tmp_path, plan, owner)
    before = hashlib.sha256((tmp_path / DB).read_bytes()).hexdigest()
    ack = acknowledgement(1, "0" * 64)
    assess(
        tmp_path,
        plan,
        owner,
        config=ForwardOperationsConfig(custodian_id="synthetic-custodian"),
        returned=ReturnedCustodyEvidence(
            ack,
            hashlib.sha256(ack).hexdigest(),
            b"bad-readback",
            hashlib.sha256(b"bad-readback").hexdigest(),
        ),
    )
    assert hashlib.sha256((tmp_path / DB).read_bytes()).hexdigest() == before


def test_missing_optional_contract_dependency_is_actionable(tmp_path: Path) -> None:
    plan, owner = setup(tmp_path)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.document.payload_json)
    before = (tmp_path / DB).read_bytes()
    with patch("donghak_stock_vision.forward_custody_cli.find_spec", return_value=None):
        assert (
            main(
                [
                    "forward-capture",
                    "custody-stage",
                    "--root",
                    str(tmp_path),
                    "--plan",
                    str(plan_file),
                    "--plan-pin",
                    plan.document.identifier,
                    "--attempt",
                    "a",
                    "--pin",
                    owner.identifier,
                ]
            )
            == 2
        )
    assert not (tmp_path / EXPORTS).exists() and (tmp_path / DB).read_bytes() == before
