"""Synthetic sources and fake approval attestations; sockets are blocked by conftest."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from donghak_stock_vision.backtest import dart_batch_orchestrator as orchestrator
from donghak_stock_vision.backtest import dart_document_batch as batch
from donghak_stock_vision.backtest import dart_multi_batch_session as session
from donghak_stock_vision.backtest import dart_transport as transport
from tests.backtest.test_dart_batch_orchestrator import SECRET, approved, files, zipped
from tests.backtest.test_dart_batch_orchestrator import root as base_root


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    wrapped_name = "__wrapped__"
    initial_root = cast(
        Callable[[Path, pytest.MonkeyPatch], Path], getattr(base_root, wrapped_name)
    )(tmp_path, monkeypatch)
    record = approved(initial_root, [f"20240101{i:06d}" for i in range(25, 50)], "batch-0002")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=zipped()))
    ) as http:
        batch.execute(initial_root, record, initial_root / record["execution_path"], http=http)
    orchestrator.prepare_next(initial_root)
    return initial_root


def parent(root: Path, budget: int = 50) -> dict[str, Any]:
    result = session.prepare(root, budget)
    return {
        **batch.load(root / result["draft_path"]),
        "approved_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "access_confirmed": True,
        "quota_confirmed": True,
    }


def run(root: Path, value: dict[str, Any], handler: Any, *, resume: bool = False) -> dict[str, Any]:
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return session.execute(root, value, confirmed=True, resume=resume, http=http)


@pytest.mark.parametrize("budget", [25, 50, 100, 51])
def test_bounded_session_success_replay_preserves_history(root: Path, budget: int) -> None:
    before = files(root)
    value = parent(root, budget)
    assert value["children"][0] == batch.load(root / ".data/dart-batch-0003-draft.json")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        return httpx.Response(200, content=zipped())

    result = run(root, value, handler)
    assert calls == value["receipts"] and len(calls) == len(set(calls)) == budget
    assert all(len(c["receipts"]) <= 25 for c in value["children"])
    assert result["state"] == "COMPLETE" and result["offline_replay"] == "VERIFIED"
    assert result["counts"]["Verified Local"] == 51 + budget
    assert (
        result["corporate_actions"]
        == result["runner_tail"]
        == result["run_input_freeze"]
        == "blocked"
    )
    assert result["corporate_action_coverage"] == "0/993"
    after = files(root)
    assert all(after[p] == data for p, data in before.items())
    assert SECRET not in json.dumps(value)
    next_session = session.prepare(root, 25)
    assert next_session["session"] == "session-0002"


@pytest.mark.parametrize(
    "damage",
    ["unapproved", "unconfirmed", "budget", "child_scope", "duplicate", "endpoint", "plan", "id"],
)
def test_invalid_parent_no_http(root: Path, damage: str) -> None:
    value = parent(root)
    if damage == "unapproved":
        value.update(approved_at=None, access_confirmed=False, quota_confirmed=False)
    elif damage == "budget":
        value["max_requests"] = 49
    elif damage == "child_scope":
        value["children"][1]["receipts"] = value["children"][0]["receipts"]
    elif damage == "duplicate":
        value["receipts"][1] = value["receipts"][0]
    elif damage == "endpoint":
        value["endpoint"] = "https://example.invalid"
    elif damage == "plan":
        value["plan_fingerprint"] = "0" * 64
    elif damage == "id":
        value["session_id"] = "session-0002"
        value["execution_path"] = session.DIRECTORY + "/session-0002"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unauthorized request"))
    ) as http:
        with pytest.raises(ValueError):
            session.execute(root, value, confirmed=damage != "unconfirmed", http=http)


@pytest.mark.parametrize("failure", ["429", "800", "012", "zip", "crc"])
def test_middle_batch_failure_stops_parent_and_cannot_bypass(root: Path, failure: str) -> None:
    value = parent(root, 100)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        if len(calls) != 27:
            return httpx.Response(200, content=zipped())
        if failure == "429":
            return httpx.Response(429)
        if failure in {"800", "012"}:
            return httpx.Response(
                200, content=f"<result><status>{failure}</status></result>".encode()
            )
        if failure == "zip":
            return httpx.Response(200, content=b"PK broken")
        body = zipped().replace(b"synthetic fixture", b"synthetic fixturX")
        return httpx.Response(200, content=body)

    with pytest.raises(ValueError, match="session_execution_stopped"):
        run(root, value, handler)
    assert len(calls) == 27
    assert session.report(root)["state"] == "STOPPED"
    with pytest.raises(ValueError):
        session.prepare(root, 25)
    with pytest.raises(ValueError):
        run(root, value, lambda _: pytest.fail("no retry"), resume=True)
    standalone = approved(root, value["children"][2]["receipts"], "batch-0005")
    with pytest.raises(ValueError, match="session_operator_action_required"):
        batch.execute(root, standalone, root / standalone["execution_path"])


class Crash(BaseException):
    pass


@pytest.mark.parametrize("phase", ["reservation", "received", "body", "receipt", "completed_child"])
def test_crash_resume_only_unambiguous_sources(
    root: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    value = parent(root)
    original_write = batch.write_once
    original_fetch = transport.DartEvidenceClient.fetch
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        if phase == "received":
            raise Crash()
        return httpx.Response(200, content=zipped())

    def write(path: Path, record: dict[str, Any]) -> None:
        original_write(path, record)
        if phase == "reservation" and path.parent.name == "attempts":
            raise Crash()
        if phase == "completed_child" and path.name == "complete.json":
            raise Crash()

    def fetch(self: transport.DartEvidenceClient, api: str, params: dict[str, str]) -> Any:
        result = original_fetch(self, api, params)
        if phase == "body":
            next(self.output.glob("*.json")).unlink()
            raise Crash()
        if phase == "receipt":
            raise Crash()
        return result

    monkeypatch.setattr(batch, "write_once", write)
    monkeypatch.setattr(transport.DartEvidenceClient, "fetch", fetch)
    with pytest.raises(Crash):
        run(root, value, handler)
    monkeypatch.setattr(batch, "write_once", original_write)
    monkeypatch.setattr(transport.DartEvidenceClient, "fetch", original_fetch)
    if phase in {"receipt", "completed_child"}:
        run(root, value, handler, resume=True)
        assert len(calls) == 50 and len(set(calls)) == 50
    else:
        with pytest.raises(ValueError):
            run(root, value, lambda _: pytest.fail("ambiguous result cannot repeat"), resume=True)
    with pytest.raises(ValueError):
        run(root, value, lambda _: pytest.fail("duplicate session"))


def test_parent_approval_tampering_and_lock(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = parent(root)
    original_write = batch.write_once

    def write(path: Path, record: dict[str, Any]) -> None:
        original_write(path, record)
        if path.name == "binding.json":
            raise Crash()

    monkeypatch.setattr(batch, "write_once", write)
    with pytest.raises(Crash):
        run(root, value, lambda _: pytest.fail("crash before first request"))
    monkeypatch.setattr(batch, "write_once", original_write)
    path = root / value["execution_path"] / "approval.json"
    mutated = batch.load(path)
    mutated["approved_at"] = "2001-01-01T00:00:00+00:00"
    path.write_text(json.dumps(mutated))
    with pytest.raises(ValueError, match="session_approval_changed"):
        session.report(root)
    with batch.execution_lock(root):
        with pytest.raises(ValueError, match="batch_execution_active"):
            session.prepare(root, 25)


def test_prepare_offline_draft_and_duplicate_preserve(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = files(root)
    monkeypatch.setattr(batch, "dotenv_values", lambda _: pytest.fail("offline must not load key"))
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("offline HTTP"))
    result = session.prepare(root, 100)
    draft = batch.load(root / result["draft_path"])
    assert draft["approved_at"] is None
    assert draft["access_confirmed"] is draft["quota_confirmed"] is False
    assert result["existing_batch_draft_reused"]
    assert result["actual_api_requests_this_operation"] == 0
    after = files(root)
    assert all(after[p] == data for p, data in before.items())
    with pytest.raises(ValueError, match="session_operator_action_required"):
        session.prepare(root, 100)
    for child in draft["children"]:
        assert not (root / child["execution_path"]).exists()


@pytest.mark.parametrize("budget", [0, -1, 3108, True])
def test_invalid_budget(root: Path, budget: int) -> None:
    with pytest.raises(ValueError, match="session_budget_invalid"):
        session.prepare(root, budget)


@pytest.mark.parametrize("damage", ["source", "receipt", "draft", "binding", "child_approval"])
def test_evidence_and_linkage_corruption_blocks_new_requests(
    root: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    value = parent(root)
    original_write = batch.write_once

    def write(path: Path, record: dict[str, Any]) -> None:
        original_write(path, record)
        if path.name == "complete.json":
            raise Crash()

    monkeypatch.setattr(batch, "write_once", write)
    with pytest.raises(Crash):
        run(root, value, lambda _: httpx.Response(200, content=zipped()))
    monkeypatch.setattr(batch, "write_once", original_write)
    unit = root / value["children"][0]["execution_path"]
    parent_path = root / value["execution_path"]
    if damage == "source":
        next((unit / "sources").glob("*.gz")).write_bytes(b"broken")
    elif damage == "receipt":
        next((unit / "sources").glob("*.json")).unlink()
    else:
        path = parent_path / (damage + ".json")
        if damage == "child_approval":
            path = unit / "approval.json"
        record = batch.load(path)
        if damage == "binding":
            record["parent_fingerprint"] = "0" * 64
        elif damage == "draft":
            record["max_requests"] = 49
        else:
            record["approved_at"] = "2001-01-01T00:00:00+00:00"
        path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        run(root, value, lambda _: pytest.fail("corrupt evidence must not request"), resume=True)


def test_read_only_cli_and_prepare_do_not_request(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    before = files(root)
    monkeypatch.setattr(sys, "argv", ["session", "--root", str(root), "--status"])
    monkeypatch.setattr(batch, "dotenv_values", lambda _: pytest.fail("key access"))
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("HTTP construction"))
    session.main()
    text = capsys.readouterr().out
    assert "51" in text and "3057" in text and "20240101" not in text
    assert files(root) == before
    monkeypatch.setattr(
        sys, "argv", ["session", "--root", str(root), "--prepare", "--max-requests", "100"]
    )
    session.main()
    assert "APPROVAL REQUIRED" in capsys.readouterr().out


def test_existing_draft_scope_conflict_is_preserved(root: Path) -> None:
    before = files(root)
    with pytest.raises(ValueError, match="session_existing_draft_conflict"):
        session.prepare(root, 1)
    assert files(root) == before


def test_small_session_without_existing_next_draft(root: Path) -> None:
    # Removing a synthetic test draft only; actual batch-0003 remains untouched.
    (root / ".data/dart-batch-0003-draft.json").unlink()
    value = parent(root, 3)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        return httpx.Response(200, content=zipped())

    assert run(root, value, handler)["state"] == "COMPLETE"
    assert len(calls) == 3


def test_parent_completion_durability_failure_is_not_silently_complete(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = parent(root, 25)
    original = batch.write_once

    def failed_fsync(path: Path, record: dict[str, Any]) -> None:
        original(path, record)
        if path.name == "complete.json" and path.parent.name == "session-0001":
            raise OSError("synthetic parent fsync failure")

    monkeypatch.setattr(batch, "write_once", failed_fsync)
    with pytest.raises(ValueError, match="session_execution_stopped"):
        run(root, value, lambda _: httpx.Response(200, content=zipped()))
    with pytest.raises(ValueError, match="session_terminal_record_conflict"):
        session.prepare(root, 25)


@pytest.mark.parametrize("phase", ["completed_child", "saved_receipt", "missing_binding"])
def test_explicit_resume_recovers_safe_recorded_stop(
    root: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    value = parent(root)
    original = batch.write_once
    calls: list[str] = []
    fired = False

    def write(path: Path, record: dict[str, Any]) -> None:
        nonlocal fired
        if (
            not fired
            and phase == "saved_receipt"
            and path.parent.name == "events"
            and path.name != "complete.json"
        ):
            fired = True
            raise OSError("synthetic event persistence interruption")
        if not fired and phase == "missing_binding" and path.name == "binding.json":
            fired = True
            raise Crash()
        original(path, record)
        if not fired and phase == "completed_child" and path.name == "complete.json":
            fired = True
            raise OSError("synthetic batch boundary interruption")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        return httpx.Response(200, content=zipped())

    monkeypatch.setattr(batch, "write_once", write)
    with pytest.raises((ValueError, Crash)):
        run(root, value, handler)
    monkeypatch.setattr(batch, "write_once", original)
    before = files(root)
    result = run(root, value, handler, resume=True)
    assert result["state"] == "COMPLETE"
    assert calls == value["receipts"] and len(set(calls)) == 50
    after = files(root)
    assert all(after[p] == body for p, body in before.items())
    with pytest.raises(ValueError):
        run(root, value, lambda _: pytest.fail("completed session cannot repeat"), resume=True)


@pytest.mark.parametrize("phase", ["intent", "approval", "binding", "reservation"])
def test_kernel_releases_lock_after_forced_process_death(root: Path, phase: str) -> None:
    import multiprocessing
    import os
    import signal

    value = parent(root, 25)

    def killed_child() -> None:
        original = batch.write_once

        def write(path: Path, value: dict[str, Any]) -> None:
            original(path, value)
            if (
                (phase == "intent" and path.name == "approval-intent.json")
                or (
                    phase == "approval"
                    and path.name == "approval.json"
                    and path.parent.name == "session-0001"
                )
                or (phase == "binding" and path.name == "binding.json")
                or (phase == "reservation" and path.parent.name == "attempts")
            ):
                os.kill(os.getpid(), signal.SIGKILL)

        batch.write_once = write
        run(root, value, lambda _: pytest.fail("killed before document request"))

    process = multiprocessing.get_context("fork").Process(target=killed_child)
    process.start()
    process.join(30)
    assert not process.is_alive()
    assert process.exitcode == -signal.SIGKILL
    with batch.execution_lock(root):
        pass  # The child did not leak the kernel lock.
    before = files(root)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        return httpx.Response(200, content=zipped())

    if phase == "reservation":
        with pytest.raises(ValueError, match="session_ambiguous_or_failed_attempt"):
            run(
                root,
                value,
                lambda _: pytest.fail("ambiguous reservation cannot retry"),
                resume=True,
            )
        assert not calls
        with pytest.raises(ValueError):
            session.prepare(root, 25)
    else:
        assert run(root, value, handler, resume=True)["state"] == "COMPLETE"
        assert calls == value["receipts"]
    after = files(root)
    assert all(after[p] == body for p, body in before.items())


def test_repeated_safe_stops_append_history_without_overwriting(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = parent(root)
    original = batch.write_once
    fail = True

    def write(path: Path, record: dict[str, Any]) -> None:
        if fail and path.name != "complete.json" and path.parent.name == "events":
            raise OSError("synthetic repeated safe persistence interruption")
        original(path, record)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        return httpx.Response(200, content=zipped())

    monkeypatch.setattr(batch, "write_once", write)
    with pytest.raises(ValueError):
        run(root, value, handler)
    first = files(root)
    with pytest.raises(ValueError):
        run(root, value, handler, resume=True)
    second = files(root)
    assert all(second[p] == body for p, body in first.items())
    fail = False
    assert run(root, value, handler, resume=True)["state"] == "COMPLETE"
    assert calls == value["receipts"]
    assert len(list((root / value["execution_path"] / "resumes").glob("*.json"))) == 2


@pytest.mark.parametrize("damage", ["intent_mismatch", "legacy_missing", "binding_after_execution"])
def test_partial_binding_recovery_requires_original_scope_and_no_execution(
    root: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    value = parent(root, 25)
    original = batch.write_once

    def write(path: Path, record: dict[str, Any]) -> None:
        if damage != "binding_after_execution" and path.name == "binding.json":
            raise Crash()
        original(path, record)
        if damage == "binding_after_execution" and path.name == "complete.json":
            raise Crash()

    monkeypatch.setattr(batch, "write_once", write)
    with pytest.raises(Crash):
        run(root, value, lambda _: httpx.Response(200, content=zipped()))
    monkeypatch.setattr(batch, "write_once", original)
    directory = root / value["execution_path"]
    if damage == "intent_mismatch":
        value["approved_at"] = "2001-01-01T00:00:00+00:00"
        (directory / "approval.json").write_text(json.dumps(value))
    elif damage == "legacy_missing":
        # Synthetic migration fixture for an original PR103 approval without intent.
        (directory / "approval-intent.json").unlink()
    else:
        (directory / "binding.json").unlink()
    before = files(root)
    if damage == "legacy_missing":
        assert (
            run(root, value, lambda _: httpx.Response(200, content=zipped()), resume=True)["state"]
            == "COMPLETE"
        )
    else:
        with pytest.raises(ValueError):
            run(
                root,
                value,
                lambda _: pytest.fail("invalid binding recovery must not request"),
                resume=True,
            )
    after = files(root)
    assert all(after[p] == body for p, body in before.items())
