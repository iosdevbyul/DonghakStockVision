"""Offline orchestrator fixtures are synthetic, never official DART evidence."""

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from donghak_stock_vision.backtest import dart_batch_orchestrator as orchestrator
from donghak_stock_vision.backtest import dart_document_batch as batch
from donghak_stock_vision.backtest import dart_transport as transport
from donghak_stock_vision.backtest.collect_corporate_action_documents import (
    PILOT_DIRECTORY,
    PILOT_RECEIPT,
)
from donghak_stock_vision.data.learning import digest

SECRET = "s" * 40


def zipped() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("filing.xml", "synthetic fixture")
    return buffer.getvalue()


def approved(root: Path, receipts: list[str], identifier: str) -> dict[str, Any]:
    # Only test setup promotes fake fixtures, never the orchestrator or local real records.
    return {
        **orchestrator.draft_for(batch.plan_for(root).identifier, receipts, identifier),
        "approved_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "access_confirmed": True,
        "quota_confirmed": True,
    }


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "docs/evidence/baseline-dart/artifacts"
    parent.mkdir(parents=True)
    artifact = {
        "version": "dart_document_review_queue_v1",
        "candidates": [
            {
                "filing": {"rcept_no": r},
                "repository_ticker": "005930",
                "review_categories": ["split"],
                "lifecycle_review": False,
            }
            for r in [PILOT_RECEIPT] + [f"20240101{i:06d}" for i in range(3107)]
        ],
    }
    (parent / "corporate-action-events.json").write_text(
        json.dumps({"artifact": artifact, "fingerprint": digest(artifact)})
    )
    monkeypatch.setattr(batch, "dotenv_values", lambda _: {"DART_API_KEY": SECRET})
    monkeypatch.setattr(
        "donghak_stock_vision.backtest.dart_document_batch.time.sleep", lambda _: None
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=zipped()))
    ) as http:
        transport.DartEvidenceClient(
            SECRET,
            tmp_path / PILOT_DIRECTORY,
            http,
            max_attempts=1,
            limits=transport.DartTransferLimits(),
        ).fetch("document.xml", {"rcept_no": PILOT_RECEIPT})
        record = approved(tmp_path, [f"20240101{i:06d}" for i in range(25)], "batch-0001")
        batch.execute(tmp_path, record, tmp_path / record["execution_path"], http=http)
    (tmp_path / ".data/dart-batch-0001-approved.json").write_text(json.dumps(record))
    old_draft = {**record, "approved_at": None, "access_confirmed": False, "quota_confirmed": False}
    (tmp_path / ".data/dart-batch-0001-draft.json").write_text(json.dumps(old_draft))
    return tmp_path


def files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_prepare_26_verified_next25_batch2_no_network_and_immutable_history(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = files(root)

    def forbidden(*args: object, **kwargs: object) -> Any:
        pytest.fail("prepare/status must never load keys, construct HTTP clients or execute")

    monkeypatch.setattr(batch, "execute", forbidden)
    monkeypatch.setattr(batch, "dotenv_values", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    report = orchestrator.prepare_next(root)
    assert report["blocked"] is False
    assert report["counts"]["Verified Local"] == 26
    assert report["counts"]["Pending"] == 3082
    assert report["next_batch"] == "batch-0002"
    assert report["selected_count"] == 25
    assert report["approval"] == "REQUIRED"
    assert report["execution"] == "NOT STARTED"
    assert report["offline_replay"] == "VERIFIED"
    draft = batch.load(root / report["draft_path"])
    assert draft["receipts"] == [f"20240101{i:06d}" for i in range(25, 50)]
    assert draft["approved_at"] is None
    assert draft["access_confirmed"] is draft["quota_confirmed"] is False
    assert not (root / draft["execution_path"]).exists()
    assert set(draft) == batch.FIELDS
    after = files(root)
    assert all(after[name] == value for name, value in before.items())
    assert set(after) - set(before) == {report["draft_path"]}
    assert SECRET not in orchestrator.render(report)
    assert SECRET not in json.dumps(draft)
    assert (
        report["corporate_actions"]
        == report["runner_tail"]
        == report["run_input_freeze"]
        == "blocked"
    )
    assert report["corporate_action_coverage"] == "0/993"
    assert "documents" not in report and "receipts" not in report


def test_status_is_read_only_and_repeated_prepare_cannot_skip_or_overwrite(root: Path) -> None:
    before = files(root)
    assert not orchestrator.status(root)["blocked"]
    assert before == files(root)
    first = orchestrator.prepare_next(root)
    draft = root / first["draft_path"]
    saved = draft.read_bytes()
    second = orchestrator.prepare_next(root)
    assert second["blocked"]
    assert second["block_reason"] == "orchestrator_draft_exists"
    assert saved == draft.read_bytes()
    assert orchestrator.status(root)["next_batch"] == "batch-0002"
    assert not (root / ".data/dart-batch-0003-draft.json").exists()


def test_unapproved_draft_is_rejected_by_existing_execute_before_key(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = orchestrator.prepare_next(root)
    draft = batch.load(root / report["draft_path"])

    def forbidden(_: Path) -> dict[str, str]:
        pytest.fail("key must not be loaded")

    monkeypatch.setattr(batch, "dotenv_values", forbidden)
    with pytest.raises(ValueError, match="batch_access_unconfirmed"):
        batch.execute(root, draft, root / draft["execution_path"])


@pytest.mark.parametrize(
    "damage",
    ["incomplete", "failed", "attempted", "body_missing", "corrupt", "receipt_missing", "gap"],
)
def test_existing_execution_damage_blocks_new_draft(root: Path, damage: str) -> None:
    unit = root / batch.DIRECTORY / "runs/batch-0001"
    receipts = approved(root, ["20240101000000"], "batch-0002")
    if damage == "incomplete":
        (unit / "events/complete.json").unlink()
    elif damage in {"failed", "attempted"}:
        # A real executor reserves these synthetic interrupted/failed test units.
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"<result><status>800</status></result>")
            )
        ) as http:
            batch.execute(
                root,
                approved(root, ["20240101000025"], "batch-0002"),
                root / batch.DIRECTORY / "runs/batch-0002",
                http=http,
            )
        if damage == "attempted":
            (root / batch.DIRECTORY / "runs/batch-0002/events/20240101000025.json").unlink()
    elif damage == "gap":
        # Invalid numbering cannot be solved by simply advancing to a larger identifier.
        (root / ".data/dart-batch-0003-approved.json").write_text(
            json.dumps({**receipts, "execution_path": batch.DIRECTORY + "/runs/batch-0003"})
        )
    else:
        sources = unit / "sources"
        path = next(sources.glob("*.json" if damage == "receipt_missing" else "*.gz"))
        if damage == "corrupt":
            path.write_bytes(b"corrupt")
        else:
            path.unlink()
    report = orchestrator.prepare_next(root)
    assert report["blocked"] is True
    assert report["execution"] == "NOT STARTED"
    assert not (root / ".data/dart-batch-0002-draft.json").exists()


@pytest.mark.parametrize(
    "collision",
    [
        "batch_directory",
        "approval_pending",
        "draft_ahead",
        "draft_wrong_plan",
        "draft_duplicate_scope",
        "approved_wrong_plan",
    ],
)
def test_control_record_collisions_are_not_bypassed(root: Path, collision: str) -> None:
    record = approved(root, ["20240101000025"], "batch-0002")
    if collision == "batch_directory":
        (root / record["execution_path"]).mkdir()
    elif collision == "approval_pending":
        (root / ".data/dart-batch-0002-approved.json").write_text(json.dumps(record))
    elif collision == "approved_wrong_plan":
        p = root / ".data/dart-batch-0001-approved.json"
        value = batch.load(p)
        value["plan_fingerprint"] = "0" * 64
        p.write_text(json.dumps(value))
    else:
        draft = orchestrator.draft_for(
            batch.plan_for(root).identifier, ["20240101000025"], "batch-0002"
        )
        p = root / ".data/dart-batch-0002-draft.json"
        if collision == "draft_ahead":
            p = root / ".data/dart-batch-0003-draft.json"
        elif collision == "draft_wrong_plan":
            draft["plan_fingerprint"] = "0" * 64
        else:
            draft["receipts"] = ["20240101000000"]
        p.write_text(json.dumps(draft))
    before = files(root)
    assert orchestrator.prepare_next(root)["blocked"]
    assert files(root) == before
    assert not (root / ".data/dart-batch-0004-draft.json").exists()


@pytest.mark.parametrize("size", [0, 26, -1, True])
def test_budget_above25_or_invalid_never_creates_draft(root: Path, size: int) -> None:
    report = orchestrator.prepare_next(root, size=size)
    assert report["block_reason"] == "orchestrator_batch_size_invalid"
    assert not (root / ".data/dart-batch-0002-draft.json").exists()


@pytest.mark.parametrize("remaining", [0, 3])
def test_selection_terminal_and_short_batches_use_validated_inventory_contract(
    root: Path, remaining: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pure selection contract test: these mocked states are NOT actual source evidence.
    view = batch.inventory(root, batch.plan_for(root))
    pending = [r for r, s in view["states"].items() if s == "Pending"]
    for receipt in pending[remaining:]:
        view["states"][receipt] = "Verified Local"
    monkeypatch.setattr(batch, "inventory", lambda *_: view)
    report = orchestrator.prepare_next(root)
    assert not report["blocked"]
    assert report["selected_count"] == remaining
    if remaining == 0:
        assert report["complete"] is True
        assert report["next_batch"] is None
        assert report["draft_path"] is None
    else:
        draft = batch.load(root / report["draft_path"])
        assert draft["receipts"] == pending[:remaining]
        assert draft["max_requests"] == remaining


def test_plan_mutation_and_secret_exception_are_fail_closed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = root / "docs/evidence/baseline-dart/artifacts/corporate-action-events.json"
    wrapped = json.loads(p.read_text())
    wrapped["artifact"]["candidates"][0]["repository_ticker"] = "000660"
    wrapped["fingerprint"] = digest(wrapped["artifact"])
    p.write_text(json.dumps(wrapped))
    report = orchestrator.prepare_next(root)
    assert report["blocked"]
    assert report["block_reason"] == "batch_plan_fingerprint_mismatch"

    def failed(_: Path) -> Any:
        raise RuntimeError(SECRET + " https://example.invalid/?crtfc_key=" + SECRET)

    monkeypatch.setattr(batch, "plan_for", failed)
    assert SECRET not in orchestrator.render(orchestrator.status(root))


def test_read_only_lock_refuses_active_execution(root: Path) -> None:
    with batch.execution_lock(root):
        report = orchestrator.status(root)
        assert report["blocked"]
        assert report["block_reason"] == "orchestrator_execution_active"


def test_cli_status_and_prepare_print_only_compact_summary(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", ["orchestrator", "--root", str(root), "--status"])
    orchestrator.main()
    text = capsys.readouterr().out
    assert "Verified: 26" in text and "Next batch: batch-0002" in text
    assert len(text.splitlines()) < 30
    assert "20240101000000" not in text
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--root", str(root), "--prepare-next"])
    orchestrator.main()
    assert "Execution: NOT STARTED" in capsys.readouterr().out


def test_completed_duplicate_approval_cannot_be_skipped(root: Path) -> None:
    record = approved(root, ["20240101000000"], "batch-0002")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("already verified source"))
    ) as http:
        batch.execute(root, record, root / record["execution_path"], http=http)
    report = orchestrator.prepare_next(root)
    assert report["block_reason"] == "orchestrator_duplicate_approval_scope"
    assert not (root / ".data/dart-batch-0003-draft.json").exists()


def test_next_identifier_advances_only_after_completed_second_batch(root: Path) -> None:
    record = approved(root, [f"20240101{i:06d}" for i in range(25, 50)], "batch-0002")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=zipped()))
    ) as http:
        batch.execute(root, record, root / record["execution_path"], http=http)
    report = orchestrator.prepare_next(root)
    assert report["next_batch"] == "batch-0003"
    assert report["counts"]["Verified Local"] == 51
    assert batch.load(root / report["draft_path"])["receipts"] == [
        f"20240101{i:06d}" for i in range(50, 75)
    ]
