"""Complete original capture -> frozen prediction -> journal -> custody replay.

Only interrupted I/O/clock injection is mocked. All successful verification uses
original parsers, cryptographic pins, SQLite receipts and semantic validators.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_signal import CALCULATED, NOW, IngestionClock, repin
from tests.integration.test_prospective_prediction import RECORDED, policy
from tests.integration.test_prospective_prediction import source as source

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.storage.capture_raw_source_s3 import RawSourceS3Observation
from donghak_stock_vision.storage.forward_capture import DB, run_capture
from donghak_stock_vision.storage.forward_raw_source_custody import (
    SuppliedRawSourceEvidence,
    link_forward_raw_source,
    observation_document,
)
from donghak_stock_vision.storage.forward_signal import request_contract
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, history
from donghak_stock_vision.storage.prediction_binding import PredictionSource
from donghak_stock_vision.storage.prospective_operational_readiness import CaptureSource
from donghak_stock_vision.storage.prospective_prediction_workflow import (
    WorkflowInputs,
    freeze_workflow,
    record_workflow,
    verify_workflow,
    verify_workflow_report,
)

NS = "synthetic-prediction-rehearsal"


@pytest.fixture
def workflow(source: tuple[Path, FrozenJSON, Path]) -> tuple[WorkflowInputs, Path]:
    root, request, journal = source
    p = policy()
    owner = freeze_workflow(
        root, request, request.identifier, p, p.identifier, archive_namespace=NS
    )
    return WorkflowInputs(
        root, request, request.identifier, p, p.identifier, owner, owner.identifier
    ), journal


def record(
    workflow: tuple[WorkflowInputs, Path], head: str = GENESIS, at: datetime = RECORDED
) -> FrozenJSON:
    s, journal = workflow
    return record_workflow(s, journal, head, at)


def prediction(s: WorkflowInputs, journal: Path, result: FrozenJSON) -> PredictionSource:
    a = result.to_dict()["append"]
    return PredictionSource(
        journal,
        a["head_pin"],
        a["entry"]["entry_pin"],
        a["entry"]["entry"]["prediction"]["prediction_id"],
        s.capture_root,
        s.request,
        s.request_pin,
        s.prediction_policy_pin,
    )


def frozen(s: WorkflowInputs, **updates: Any) -> WorkflowInputs:
    owner = FrozenJSON.freeze(s.workflow.to_dict() | updates)
    return replace(s, workflow=owner, workflow_pin=owner.identifier)


def test_full_chain_and_idempotent_replay(workflow: tuple[WorkflowInputs, Path]) -> None:
    s, journal = workflow
    before = (s.capture_root / DB).read_bytes()
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("httpx.Client.send", side_effect=AssertionError("provider call")),
        patch(
            "donghak_stock_vision.trading.broker.PaperBroker.submit_paper",
            side_effect=AssertionError("broker call"),
        ),
        patch(
            "donghak_stock_vision.storage.capture_raw_source_s3.create_s3_custody_session",
            side_effect=AssertionError("AWS call"),
        ),
    ):
        first = record(workflow)
        p = prediction(s, journal, first)
        report = FrozenJSON.freeze(first.to_dict()["report"])
        saved = journal.read_bytes()
        duplicate = record(workflow, p.journal_head, RECORDED + timedelta(days=20))
        assert duplicate.to_dict()["append"]["duplicate"]
        assert duplicate.to_dict()["report_pin"] == report.identifier
        assert duplicate.to_dict()["report"] == report.to_dict()
        verify_workflow_report(report, report.identifier, s, p, RECORDED)
        assert verify_workflow(s, p, RECORDED) == report
        assert journal.read_bytes() == saved
    d = report.to_dict()
    assert d["prediction_state"]["state"] == "pending"
    assert d["prediction_state"]["outcome"] is None
    assert d["local_evidence_verified"] and d["prediction_journal_integrity_verified"]
    assert d["source_to_prediction_lineage_verified"]
    assert d["strategy"] == s.workflow.to_dict()["strategy"]
    assert d["parameters"] == {"fast": 2, "slow": 3}
    assert len(d["observations"]) == 4
    assert all(row["custody"]["original_raw_bytes_locally_verified"] for row in d["observations"])
    assert len({row["custody"]["binding"]["raw_id"] for row in d["observations"]}) == 4
    assert history(journal, p.journal_head).to_dict()["total"] == 1
    assert not any(
        d[k]
        for k in (
            "independent_timestamp_authenticated",
            "independent_custody_verified",
            "genuine_forward_evidence",
            "pit_qualified",
            "performance_approved",
            "execution_authorized",
        )
    )
    assert d["performance_metrics"] is None and d["network_requests"] == 0
    assert d["rejected_evidence"] == []
    assert "external_prediction_custody_not_supplied" in d["reason_codes"]
    assert (s.capture_root / DB).read_bytes() == before


def test_interrupted_insert_rolls_back_and_replay_recovers(
    workflow: tuple[WorkflowInputs, Path],
) -> None:
    s, journal = workflow
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal._commit", side_effect=KeyboardInterrupt
    ):
        with pytest.raises(KeyboardInterrupt):
            record(workflow)
    assert history(journal, GENESIS).to_dict()["total"] == 0
    completed = record(workflow)
    assert history(journal, completed.to_dict()["append"]["head_pin"]).to_dict()["total"] == 1
    assert verify_workflow(s, prediction(s, journal, completed), RECORDED).to_dict()[
        "local_evidence_verified"
    ]


def test_post_commit_interruption_never_publishes_completed_report(
    workflow: tuple[WorkflowInputs, Path],
) -> None:
    s, journal = workflow
    retained_heads: list[str] = []

    def commit_and_interrupt(connection: sqlite3.Connection) -> None:
        connection.commit()
        retained_heads.append(
            connection.execute("SELECT entry_pin FROM signal_entries").fetchone()[0]
        )
        raise KeyboardInterrupt

    with patch("donghak_stock_vision.storage.forward_signal_journal._commit", commit_and_interrupt):
        with pytest.raises(KeyboardInterrupt):
            record(workflow)
    before = journal.read_bytes()
    with pytest.raises(ValueError, match="journal_head_pin_mismatch"):
        record(workflow)  # Never adopt an unretained head automatically.
    complete = record(workflow, retained_heads[0])
    assert complete.to_dict()["append"]["duplicate"]
    assert journal.read_bytes() == before


def test_incomplete_initialization_not_repaired(workflow: tuple[WorkflowInputs, Path]) -> None:
    _, journal = workflow
    journal.touch()
    with pytest.raises(ValueError, match="interrupted_initialization"):
        record(workflow)
    assert journal.read_bytes() == b""


@pytest.mark.parametrize(
    "field,value",
    [
        ("strategy", {"strategy_id": "wrong", "strategy_version": "1", "code_sha256": "0" * 64}),
        ("parameters", {"fast": 1, "slow": 3}),
        ("signal_policy", "wrong-v2"),
        ("prediction_policy_pin", "0" * 64),
        ("source_database_pin", "0" * 64),
        ("observations", []),
        ("version", "unknown"),
        ("origin", "real_provider"),
    ],
)
def test_rehashed_frozen_input_substitutions(
    workflow: tuple[WorkflowInputs, Path], field: str, value: Any
) -> None:
    s, journal = workflow
    with pytest.raises(ValueError):
        record((frozen(s, **{field: value}), journal))
    assert not journal.exists()


@pytest.mark.parametrize("kind", ["missing", "digest", "owner", "capture", "reordered"])
def test_custody_reference_substitution(workflow: tuple[WorkflowInputs, Path], kind: str) -> None:
    s, journal = workflow
    rows = s.workflow.to_dict()["observations"]
    if kind == "missing":
        del rows[0]["custody_pin"]
    elif kind == "digest":
        rows[0]["custody_pin"] = "0" * 64
    elif kind == "owner":
        rows[0]["observation_id"] = rows[1]["observation_id"]
    elif kind == "capture":
        rows[0]["custody"] = rows[1]["custody"]
        rows[0]["custody_pin"] = rows[1]["custody_pin"]
    else:
        rows.reverse()
    with pytest.raises(ValueError, match="frozen_inputs_mismatch"):
        record((frozen(s, observations=rows), journal))
    assert not journal.exists()


@pytest.mark.parametrize(
    "kind", ["body", "raw_owner", "receipt", "missing_receipt", "manifest", "ingestion_time"]
)
def test_original_mutation_even_with_rehashed_database_pin(
    workflow: tuple[WorkflowInputs, Path], kind: str
) -> None:
    s, journal = workflow
    with closing(sqlite3.connect(s.capture_root / DB)) as db, db:
        if kind == "body":
            db.execute("UPDATE raw_pages SET body=? WHERE id=1", (b"altered original",))
        elif kind == "raw_owner":
            db.execute("UPDATE raw_pages SET run_id='wrong:0' WHERE id=1")
        elif kind == "receipt":
            db.execute("DROP TRIGGER raw_capture_receipts_no_update")
            db.execute("UPDATE raw_capture_receipts SET raw_sha256=? WHERE raw_id=1", ("0" * 64,))
        elif kind == "missing_receipt":
            db.execute("DROP TRIGGER raw_capture_receipts_no_delete")
            db.execute("DELETE FROM raw_capture_receipts WHERE raw_id=1")
        elif kind == "manifest":
            db.execute("DROP TRIGGER forward_manifests_no_DELETE")
            db.execute("DELETE FROM forward_manifests WHERE rowid=1")
        else:
            db.execute("UPDATE collection_runs SET finished_at='2030-01-01T00:00:00+00:00'")
    req = repin(s.capture_root, s.request)
    s = replace(s, request=req, request_pin=req.identifier)
    with pytest.raises(ValueError):
        record((s, journal))
    assert not journal.exists()


@pytest.mark.parametrize("kind", ["request", "policy", "workflow"])
def test_independently_retained_pins_required(
    workflow: tuple[WorkflowInputs, Path], kind: str
) -> None:
    s, journal = workflow
    updates: dict[str, Any] = {
        "request_pin"
        if kind == "request"
        else "prediction_policy_pin"
        if kind == "policy"
        else "workflow_pin": "0" * 64
    }
    with pytest.raises(ValueError):
        record((replace(s, **updates), journal))
    assert not journal.exists()


@pytest.mark.parametrize(
    "updates",
    [
        {"target_trading_date": "2025-07-10"},
        {"version": "unsupported"},
        {"evaluate_not_before": "2025-07-11T00:00:00+00:00"},
        {"evaluate_not_before": "2025-07-12T09:00:00+09:00"},
        {"outcome_deadline": "2025-07-11T00:00:00+00:00"},
    ],
)
def test_invalid_horizons(workflow: tuple[WorkflowInputs, Path], updates: dict[str, Any]) -> None:
    s, journal = workflow
    p = policy(**updates)
    changed = replace(s, prediction_policy=p, prediction_policy_pin=p.identifier)
    with pytest.raises(ValueError):
        record((changed, journal))
    assert not journal.exists()


def test_stale_observation_and_recording_times(workflow: tuple[WorkflowInputs, Path]) -> None:
    s, journal = workflow
    request = FrozenJSON.freeze(
        s.request.to_dict() | {"calculated_at": (CALCULATED + timedelta(hours=2)).isoformat()}
    )
    with pytest.raises(ValueError, match="latest_capture_stale"):
        freeze_workflow(
            s.capture_root,
            request,
            request.identifier,
            s.prediction_policy,
            s.prediction_policy_pin,
            archive_namespace=NS,
        )
    for at in (CALCULATED - timedelta(seconds=1), CALCULATED + timedelta(seconds=11)):
        with pytest.raises(ValueError):
            record(workflow, at=at)
    assert not journal.exists()


def test_changed_horizon_conflict_preserves_original(workflow: tuple[WorkflowInputs, Path]) -> None:
    s, journal = workflow
    first = record(workflow)
    before = journal.read_bytes()
    p = policy(outcome_deadline="2025-07-16T00:00:00+00:00")
    owner = freeze_workflow(
        s.capture_root, s.request, s.request_pin, p, p.identifier, archive_namespace=NS
    )
    changed = replace(
        s,
        prediction_policy=p,
        prediction_policy_pin=p.identifier,
        workflow=owner,
        workflow_pin=owner.identifier,
    )
    with pytest.raises(ValueError, match="different_prediction_contract"):
        record((changed, journal), first.to_dict()["append"]["head_pin"])
    assert journal.read_bytes() == before


@pytest.mark.parametrize("field", ["prediction_id", "journal_head", "entry_pin", "policy_pin"])
def test_forged_prediction_owners(workflow: tuple[WorkflowInputs, Path], field: str) -> None:
    s, journal = workflow
    result = record(workflow)
    original = prediction(s, journal, result)
    with pytest.raises(ValueError):
        verify_workflow(s, replace(original, **dict[str, Any]({field: "0" * 64})), RECORDED)


@pytest.mark.parametrize(
    "kind", ["prediction_id", "recorded_at", "strategy", "decision_time", "truncate"]
)
def test_modified_journal_rejected(workflow: tuple[WorkflowInputs, Path], kind: str) -> None:
    s, journal = workflow
    result = record(workflow)
    original = prediction(s, journal, result)
    with closing(sqlite3.connect(journal)) as db, db:
        if kind == "truncate":
            db.execute("DROP TRIGGER signal_entries_no_delete")
            db.execute("DELETE FROM signal_entries")
            db.execute(
                "CREATE TRIGGER signal_entries_no_delete BEFORE DELETE ON signal_entries "
                "BEGIN SELECT RAISE(ABORT,'immutable'); END"
            )
        else:
            entry = result.to_dict()["append"]["entry"]["entry"]
            if kind == "prediction_id":
                entry["prediction"]["prediction_id"] = "0" * 64
            elif kind == "recorded_at":
                entry["prediction"]["recorded_at"] = (RECORDED + timedelta(seconds=1)).isoformat()
            elif kind == "decision_time":
                entry["decision"]["calculated_at"] = NOW.isoformat()
            else:
                entry["prediction"]["predictor"]["strategy_version"] = "999"
            changed = FrozenJSON.freeze(entry)
            db.execute("DROP TRIGGER signal_entries_no_update")
            db.execute(
                "UPDATE signal_entries SET document=?,entry_pin=?",
                (changed.payload_json, changed.identifier),
            )
            db.execute(
                "CREATE TRIGGER signal_entries_no_update BEFORE UPDATE ON signal_entries "
                "BEGIN SELECT RAISE(ABORT,'immutable'); END"
            )
    with pytest.raises(ValueError):
        verify_workflow(s, original, RECORDED)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "unsupported"),
        ("genuine_forward_evidence", True),
        ("independent_custody_verified", True),
        ("performance_metrics", {"accuracy": 1}),
        ("observations", []),
        ("prediction_id", "0" * 64),
    ],
)
def test_rehashed_completion_reports_rejected(
    workflow: tuple[WorkflowInputs, Path], field: str, value: Any
) -> None:
    s, journal = workflow
    result = record(workflow)
    owner = prediction(s, journal, result)
    report = FrozenJSON.freeze(result.to_dict()["report"] | {field: value})
    with pytest.raises(ValueError):
        verify_workflow_report(report, report.identifier, s, owner, RECORDED)


def test_unsupported_capture_scope_is_not_promoted(tmp_path: Path) -> None:
    from tests.integration.test_forward_custody_versions import original

    for version in (2, 3):
        root = tmp_path / str(version)
        root.mkdir()
        c, at = original(root, version)
        p = policy()
        request = FrozenJSON.freeze(
            {
                "version": "forward_signal_request_v1",
                "symbol": "005930",
                "market": "KOSPI",
                "currency": "KRW",
                "mode": "offline_fixture",
                "start": c.plan.day.isoformat(),
                "end": c.plan.day.isoformat(),
                "calculated_at": at.isoformat(),
                "fast": 2,
                "slow": 3,
                "policy": "forward_local_capture_observed_bars_v1",
                "max_latest_age_seconds": 3600,
                "database_pin": "0" * 64,
                "sources": [
                    {
                        "plan": c.plan.document.to_dict(),
                        "plan_pin": c.plan_pin,
                        "attempt": c.attempt,
                        "capture_pin": c.capture_pin,
                    }
                ],
            }
        )
        with pytest.raises(ValueError, match="read_only_forward_scope_not_signal_eligible"):
            freeze_workflow(
                c.root, request, request.identifier, p, p.identifier, archive_namespace=NS
            )


def test_supplied_readback_remains_untrusted(workflow: tuple[WorkflowInputs, Path]) -> None:
    s, journal = workflow
    supplied = []
    for plan, ref in zip(
        request_contract(s.request, s.request_pin), s.request.to_dict()["sources"], strict=True
    ):
        c = CaptureSource(s.capture_root, plan, ref["plan_pin"], ref["attempt"], ref["capture_pin"])
        raw = link_forward_raw_source(c, archive_namespace=NS).source
        observation = RawSourceS3Observation(
            raw.identifier,
            NS,
            raw.raw_id,
            raw.source_object_key,
            raw.raw_sha256,
            raw.byte_length,
            "synthetic-version",
            datetime(2030, 1, 1, tzinfo=UTC),
        )
        ref_owner = FrozenJSON.freeze(
            {
                "version": "forward_raw_source_object_reference_v1",
                "source_identifier": raw.identifier,
                "bucket": observation.bucket,
                "key": raw.source_object_key,
                "version_id": observation.version_id,
            }
        )
        supplied.append(
            SuppliedRawSourceEvidence(
                observation,
                observation_document(observation).identifier,
                ref_owner,
                ref_owner.identifier,
                raw.body,
                raw.raw_sha256,
            )
        )
    owner = freeze_workflow(
        s.capture_root,
        s.request,
        s.request_pin,
        s.prediction_policy,
        s.prediction_policy_pin,
        archive_namespace=NS,
        supplied=tuple(supplied),
    )
    changed = replace(s, workflow=owner, workflow_pin=owner.identifier, supplied=tuple(supplied))
    result = record((changed, journal))
    assert all(
        row["custody"]["supplied_external_readback"]["status"] == "content_consistent_unverified"
        for row in result.to_dict()["report"]["observations"]
    )
    assert not result.to_dict()["report"]["independent_custody_verified"]
    with pytest.raises(ValueError, match="frozen_inputs_mismatch"):
        record((replace(changed, supplied=()), journal), result.to_dict()["append"]["head_pin"])
    swapped = tuple(reversed(supplied))
    with pytest.raises(ValueError, match="source_mismatch"):
        record(
            (replace(changed, supplied=swapped), journal), result.to_dict()["append"]["head_pin"]
        )


def test_cli_rehearsal_and_retained_report_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Clock":
            return cls.fromtimestamp(CALCULATED.timestamp(), UTC)

    def captured(*args: Any, **kwargs: Any) -> FrozenJSON:
        return run_capture(*args, **kwargs, clock=lambda: NOW, sleep=lambda _: None)

    root, journal = tmp_path / "originals", tmp_path / "journal.sqlite3"
    with (
        patch("donghak_stock_vision.forward_signal_demo_cli.datetime", Clock),
        patch("donghak_stock_vision.prospective_prediction_workflow_cli.datetime", Clock),
        patch("donghak_stock_vision.forward_signal_demo_cli.run_capture", side_effect=captured),
        patch("donghak_stock_vision.storage.sqlite.datetime", IngestionClock),
    ):
        assert (
            main(
                [
                    "forward-signal",
                    "offline-prediction-custody-demo",
                    "--capture-root",
                    str(root),
                    "--journal",
                    str(journal),
                    "--archive-namespace",
                    NS,
                ]
            )
            == 0
        )
    result = json.loads(capsys.readouterr().out)
    assert result["duplicate_detected"] and result["origin"] == "offline_fixture"
    report = tmp_path / "report.json"
    report.write_text(json.dumps(result["report"]))
    args = [
        "forward-signal",
        "prediction-custody-verify",
        "--capture-root",
        str(root),
        "--request",
        result["request_path"],
        "--request-pin",
        result["request_pin"],
        "--prediction-policy",
        result["prediction_policy_path"],
        "--prediction-policy-pin",
        result["prediction_policy_pin"],
        "--workflow",
        result["workflow_path"],
        "--workflow-pin",
        result["workflow_pin"],
        "--journal",
        str(journal),
        "--journal-pin",
        result["journal_head_pin"],
        "--entry-pin",
        result["entry_pin"],
        "--prediction-id",
        result["prediction_id"],
        "--report",
        str(report),
        "--report-pin",
        result["report_pin"],
    ]
    before = {p: sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["locally_verified"]
    assert before == {p: sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    assert main(args[:-1] + ["0" * 64]) == 2


def test_legacy_entry_preserved_and_cannot_gain_prediction(
    workflow: tuple[WorkflowInputs, Path],
) -> None:
    from donghak_stock_vision.storage.forward_signal_journal import append_signal

    s, journal = workflow
    old = append_signal(journal, s.capture_root, s.request, s.request_pin, GENESIS, RECORDED)
    head = old.to_dict()["head_pin"]
    before = journal.read_bytes()
    with pytest.raises(ValueError, match="different_prediction_contract"):
        record(workflow, head)
    assert journal.read_bytes() == before
    request = FrozenJSON.freeze(s.request.to_dict() | {"fast": 1})
    owner = freeze_workflow(
        s.capture_root,
        request,
        request.identifier,
        s.prediction_policy,
        s.prediction_policy_pin,
        archive_namespace=NS,
    )
    changed = replace(
        s,
        request=request,
        request_pin=request.identifier,
        workflow=owner,
        workflow_pin=owner.identifier,
    )
    new = record((changed, journal), head)
    entries = history(journal, new.to_dict()["append"]["head_pin"]).to_dict()["entries"]
    assert entries[0] == old.to_dict()["entry"]
    assert entries[1]["entry"]["version"] == "forward_signal_entry_v2"


def test_no_journal_inside_original_archive_or_unretained_new_head(
    workflow: tuple[WorkflowInputs, Path],
) -> None:
    s, journal = workflow
    with pytest.raises(ValueError, match="separate_prediction_journal_required"):
        record_workflow(s, s.capture_root / "wrong.sqlite3", GENESIS, RECORDED)
    assert not (s.capture_root / "wrong.sqlite3").exists()
    with pytest.raises(ValueError, match="new_journal_requires_genesis"):
        record(workflow, "0" * 64)
    assert not journal.exists()


def test_source_changes_after_append_publish_no_report(
    workflow: tuple[WorkflowInputs, Path],
) -> None:
    from donghak_stock_vision.storage.forward_signal_journal import append_signal

    s, journal = workflow
    retained_head: list[str] = []

    def changed_after_commit(*args: Any, **kwargs: Any) -> FrozenJSON:
        result = append_signal(*args, **kwargs)
        retained_head.append(result.to_dict()["head_pin"])
        with closing(sqlite3.connect(s.capture_root / DB)) as db, db:
            db.execute("UPDATE raw_pages SET body=? WHERE id=1", (b"corrupted after commit",))
        return result

    with patch(
        "donghak_stock_vision.storage.prospective_prediction_workflow.append_signal",
        changed_after_commit,
    ):
        with pytest.raises(ValueError, match="capture_database_pin_mismatch"):
            record(workflow)
    # A complete original journal record survives; it cannot masquerade as a
    # completed E2E chain because original-source verification still fails.
    assert history(journal, retained_head[0]).to_dict()["total"] == 1
    with pytest.raises(ValueError, match="capture_database_pin_mismatch"):
        record(workflow, retained_head[0])


@pytest.mark.parametrize("change", ["strategy", "calculation_time"])
def test_self_consistent_rehashed_journal_still_requires_semantic_replay(
    workflow: tuple[WorkflowInputs, Path], change: str
) -> None:
    from donghak_stock_vision.storage.prospective_prediction import evidence

    s, journal = workflow
    result = record(workflow)
    entry = result.to_dict()["append"]["entry"]["entry"]
    decision_doc = entry["decision"]
    if change == "strategy":
        decision_doc["strategy"]["strategy_version"] = "999"
    else:
        decision_doc["calculated_at"] = (CALCULATED - timedelta(seconds=1)).isoformat()
    decision = FrozenJSON.freeze(decision_doc)
    entry["decision_pin"] = decision.identifier
    entry["prediction"] = evidence(
        decision, s.prediction_policy, s.prediction_policy_pin, RECORDED
    ).to_dict()
    forged = FrozenJSON.freeze(entry)
    with closing(sqlite3.connect(journal)) as db, db:
        db.execute("DROP TRIGGER signal_entries_no_update")
        db.execute(
            "UPDATE signal_entries SET document=?,entry_pin=?",
            (forged.payload_json, forged.identifier),
        )
        db.execute(
            "CREATE TRIGGER signal_entries_no_update BEFORE UPDATE ON signal_entries "
            "BEGIN SELECT RAISE(ABORT,'immutable'); END"
        )
    owner = prediction(s, journal, result)
    forged_source = replace(
        owner,
        journal_head=forged.identifier,
        entry_pin=forged.identifier,
        prediction_id=entry["prediction"]["prediction_id"],
    )
    # Even if all attacker-supplied hashes are mutually consistent, original
    # signal replay binds the actual strategy implementation and request time.
    with pytest.raises(ValueError, match="signal_semantic_mismatch"):
        verify_workflow(s, forged_source, RECORDED)


def test_interrupt_second_record_preserves_first_committed_entry(
    workflow: tuple[WorkflowInputs, Path],
) -> None:
    s, journal = workflow
    first = record(workflow)
    head = first.to_dict()["append"]["head_pin"]
    request = FrozenJSON.freeze(s.request.to_dict() | {"fast": 1})
    owner = freeze_workflow(
        s.capture_root,
        request,
        request.identifier,
        s.prediction_policy,
        s.prediction_policy_pin,
        archive_namespace=NS,
    )
    changed = replace(
        s,
        request=request,
        request_pin=request.identifier,
        workflow=owner,
        workflow_pin=owner.identifier,
    )
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal._commit", side_effect=KeyboardInterrupt
    ):
        with pytest.raises(KeyboardInterrupt):
            record((changed, journal), head)
    records = history(journal, head).to_dict()
    assert records["total"] == 1
    assert records["entries"][0] == first.to_dict()["append"]["entry"]
    complete = record((changed, journal), head)
    assert history(journal, complete.to_dict()["append"]["head_pin"]).to_dict()["total"] == 2


def test_cli_freeze_then_record_existing_originals(
    workflow: tuple[WorkflowInputs, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s, journal = workflow
    request_path, policy_path = tmp_path / "request.json", tmp_path / "policy.json"
    request_path.write_text(s.request.payload_json)
    policy_path.write_text(s.prediction_policy.payload_json)
    common = [
        "--capture-root",
        str(s.capture_root),
        "--request",
        str(request_path),
        "--request-pin",
        s.request_pin,
        "--prediction-policy",
        str(policy_path),
        "--prediction-policy-pin",
        s.prediction_policy_pin,
    ]
    assert (
        main(["forward-signal", "prediction-custody-freeze", *common, "--archive-namespace", NS])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["workflow_pin"] == s.workflow_pin
    assert not journal.exists()
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(result["workflow"]))

    class Clock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return RECORDED

    args = [
        "forward-signal",
        "prediction-custody-record",
        *common,
        "--workflow",
        str(path),
        "--workflow-pin",
        result["workflow_pin"],
        "--journal",
        str(journal),
        "--journal-pin",
        GENESIS,
    ]
    with patch("donghak_stock_vision.prospective_prediction_workflow_cli.datetime", Clock):
        assert main(args) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["report"]["local_evidence_verified"]
    assert history(journal, completed["append"]["head_pin"]).to_dict()["total"] == 1
