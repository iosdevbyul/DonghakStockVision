"""Synthetic approval-bound acquisition; no real credentials or network."""

import gzip
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from donghak_stock_vision.backtest import collect_corporate_action_documents as collector
from donghak_stock_vision.backtest import dart_document_batch as batch
from donghak_stock_vision.backtest import dart_transport as transport
from donghak_stock_vision.data.learning import digest

SECRET = "s" * 40


def zipped() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("filing.xml", "synthetic")
    return output.getvalue()


def success_transport(calls: list[int]) -> httpx.MockTransport:
    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=zipped())

    return httpx.MockTransport(handler)


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
            for r in [collector.PILOT_RECEIPT] + [f"20240101{i:06d}" for i in range(3107)]
        ],
    }
    (parent / "corporate-action-events.json").write_text(
        json.dumps({"artifact": artifact, "fingerprint": digest(artifact)})
    )
    monkeypatch.setattr(batch, "dotenv_values", lambda _: {"DART_API_KEY": SECRET})
    monkeypatch.setattr(
        "donghak_stock_vision.backtest.dart_document_batch.time.sleep", lambda _: None
    )
    pilot = tmp_path / collector.PILOT_DIRECTORY
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=zipped()))
    ) as http:
        transport.DartEvidenceClient(
            SECRET, pilot, http, limits=transport.DartTransferLimits(), max_attempts=1
        ).fetch("document.xml", {"rcept_no": collector.PILOT_RECEIPT})
    return tmp_path


def approval(root: Path, count: int = 25, unit: int = 1) -> dict[str, Any]:
    plan = batch.plan_for(root)
    return {
        "version": batch.VERSION,
        "endpoint": transport.ENDPOINT + "document.xml",
        "plan_fingerprint": plan.identifier,
        "receipts": [f"20240101{i:06d}" for i in range(count)],
        "max_requests": count,
        "execution_path": f"{batch.DIRECTORY}/runs/batch-{unit:04d}",
        "interval_seconds": 1,
        "retries": 0,
        "approved_at": datetime.now(UTC).isoformat(),
        "access_confirmed": True,
        "quota_confirmed": True,
    }


def run(
    root: Path, record: dict[str, Any], http: httpx.Client, *, resume: bool = False
) -> dict[str, Any]:
    return batch.execute(root, record, root / record["execution_path"], http=http, resume=resume)


def test_normal_25_budget_spacing_replay_and_blocked_research(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "donghak_stock_vision.backtest.dart_document_batch.time.sleep", sleeps.append
    )
    calls: list[str] = []
    raw = zipped()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["rcept_no"])
        assert request.url.path == "/api/document.xml"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Type": "application/nonstandard"}, content=raw)

    record = approval(root)
    pilot_before = {p.name: p.read_bytes() for p in (root / collector.PILOT_DIRECTORY).iterdir()}
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = run(root, record, http)
        with pytest.raises(ValueError):
            run(root, record, http)
        with pytest.raises(ValueError, match="batch_execution_complete"):
            run(root, record, http, resume=True)
    assert calls == record["receipts"]
    assert sleeps.count(1) == 25
    assert result["reserved_requests"] == 25
    assert result["selected_replay_complete"] is True
    assert result["counts"]["Verified Local"] == 26
    assert result["counts"]["Pending"] == 3082
    assert result["full_document_sources_complete"] is False
    assert (
        result["corporate_actions"]
        == result["runner_tail"]
        == result["run_input_freeze"]
        == "blocked"
    )
    assert result["corporate_action_coverage"] == "0/993"
    assert pilot_before == {
        p.name: p.read_bytes() for p in (root / collector.PILOT_DIRECTORY).iterdir()
    }
    sources = root / record["execution_path"] / "sources"
    for p in sources.glob("*.json"):
        meta = json.loads(p.read_text())
        assert meta["raw_sha256"] == hashlib.sha256(raw).hexdigest()
        assert SECRET not in p.read_text()
    assert all(SECRET not in p.read_text() for p in (root / batch.DIRECTORY).rglob("*.json"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_requests", 24),
        ("max_requests", 26),
        ("max_requests", True),
        ("plan_fingerprint", "0" * 64),
        ("endpoint", transport.ENDPOINT + "list.json"),
        ("retries", 1),
        ("interval_seconds", 0.9),
        ("interval_seconds", True),
        ("approved_at", None),
        ("approved_at", "2999-01-01T00:00:00+00:00"),
        ("access_confirmed", False),
        ("quota_confirmed", False),
        ("receipts", [collector.PILOT_RECEIPT]),
        ("receipts", ["99999999999999"]),
        ("execution_path", ".data/arbitrary"),
    ],
)
def test_invalid_approval_never_dispatches(root: Path, field: str, value: Any) -> None:
    record = {**approval(root), field: value}
    calls: list[int] = []
    with httpx.Client(transport=success_transport(calls)) as http:
        with pytest.raises((ValueError, TypeError)):
            run(root, record, http)
    assert calls == []


@pytest.mark.parametrize("failure_at", [0, 12, 24])
@pytest.mark.parametrize("kind", ["xml800", "auth", "quota", "429", "http503", "badzip", "timeout"])
def test_errors_stop_immediately_and_never_retry(root: Path, failure_at: int, kind: str) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        index = len(calls)
        calls.append(1)
        if index != failure_at:
            return httpx.Response(200, content=zipped())
        if kind == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if kind in {"xml800", "auth", "quota"}:
            code = {"xml800": "800", "auth": "012", "quota": "020"}[kind]
            return httpx.Response(200, content=f"<result><status>{code}</status></result>".encode())
        if kind in {"429", "http503"}:
            return httpx.Response(429 if kind == "429" else 503, content=b"synthetic")
        return httpx.Response(200, content=b"PKbroken")

    record = approval(root)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = run(root, record, http)
        with pytest.raises(ValueError, match="batch_ambiguous_or_failed_attempt"):
            run(root, record, http, resume=True)
    assert len(calls) == failure_at + 1
    assert result["success"] is False
    assert result["counts"]["Failed"] == 1
    assert result["reserved_requests"] == failure_at + 1
    event = json.loads(
        (
            root / record["execution_path"] / "events" / f"{record['receipts'][failure_at]}.json"
        ).read_text()
    )
    assert SECRET not in json.dumps(event)
    if kind in {"xml800", "auth", "quota"}:
        assert event["api_status"] == {"xml800": "800", "auth": "012", "quota": "020"}[kind]


class Crash(BaseException):
    pass


@pytest.mark.parametrize("point", ["before_save", "body_only", "receipt_saved", "after_event"])
def test_crash_recovery_never_repeats_ambiguous_request(
    root: Path, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = approval(root, 2)
    calls: list[int] = []
    original_fetch = transport.DartEvidenceClient.fetch
    original_write = batch.write_once

    def fetch(
        client: transport.DartEvidenceClient, api: str, params: dict[str, str]
    ) -> tuple[bytes, dict[str, Any]]:
        if point == "body_only":
            calls.append(1)
            identity = digest(transport.request_identity(api, params))
            (client.output / f"{identity}.gz").write_bytes(gzip.compress(zipped()))
            raise Crash()
        if point == "before_save":
            calls.append(1)
            raise Crash()
        result = original_fetch(client, api, params)
        if point == "receipt_saved":
            raise Crash()
        return result

    def write(path: Path, value: dict[str, Any]) -> None:
        original_write(path, value)
        if point == "after_event" and path.parent.name == "events" and path.name != "complete.json":
            raise Crash()

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=zipped())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with monkeypatch.context() as m:
            m.setattr(transport.DartEvidenceClient, "fetch", fetch)
            m.setattr(batch, "write_once", write)
            with pytest.raises(Crash):
                run(root, record, http)
        if point in {"before_save", "body_only"}:
            with pytest.raises(ValueError):
                run(root, record, http, resume=True)
            assert len(calls) == 1
        else:
            result = run(root, record, http, resume=True)
            assert result["success"] is True
            assert result["reserved_requests"] == 2
            assert len(calls) == 2


def test_verified_reuse_new_approval_needs_no_new_request(root: Path) -> None:
    calls: list[int] = []
    with httpx.Client(transport=success_transport(calls)) as http:
        first = approval(root, 1)
        run(root, first, http)
        second = {
            **first,
            "execution_path": batch.DIRECTORY + "/runs/batch-0002",
            "approved_at": datetime.now(UTC).isoformat(),
        }
        result = run(root, second, http)
    assert len(calls) == 1
    assert result["reserved_requests"] == 0
    assert result["selected_replay_complete"] is True


@pytest.mark.parametrize("damage", ["body", "receipt", "claim"])
def test_lost_evidence_blocks_automatic_recollection(root: Path, damage: str) -> None:
    record = approval(root, 1)
    calls: list[int] = []
    with httpx.Client(transport=success_transport(calls)) as http:
        run(root, record, http)
        if damage == "claim":
            (root / batch.DIRECTORY / "claims" / f"{record['receipts'][0]}.json").unlink()
        else:
            sources = root / record["execution_path"] / "sources"
            next(sources.glob("*.gz" if damage == "body" else "*.json")).unlink()
        with pytest.raises(ValueError):
            run(root, {**record, "execution_path": batch.DIRECTORY + "/runs/batch-0002"}, http)
    assert len(calls) == 1


def test_output_collision_path_bypass_and_legacy_gate(root: Path) -> None:
    record = approval(root, 1)
    with pytest.raises(ValueError, match="batch_output_scope_mismatch"):
        batch.execute(root, record, root / "arbitrary")
    with pytest.raises(ValueError, match="batch_approval_record_required"):
        collector.collect(root, root / "arbitrary", offset=0, limit=25)
    output = root / record["execution_path"]
    output.mkdir(parents=True)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("no request"))) as http:
        with pytest.raises(ValueError):
            run(root, record, http)


def test_pilot_corruption_blocks_batch(root: Path) -> None:
    record = approval(root, 1)
    next((root / collector.PILOT_DIRECTORY).glob("*.gz")).write_bytes(b"corrupt")
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("no request"))) as http:
        with pytest.raises((ValueError, OSError)):
            run(root, record, http)


def test_inventory_is_read_only_with_one_verified_pilot(root: Path) -> None:
    plan = batch.plan_for(root)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = batch.summary(plan, batch.inventory(root, plan))
    assert report["counts"]["Verified Local"] == 1
    assert report["counts"]["Pending"] == 3107
    assert not (root / batch.DIRECTORY).exists()
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_global_kernel_lock_blocks_concurrent_execution(root: Path) -> None:
    record = approval(root, 1)
    with batch.execution_lock(root):
        with pytest.raises(ValueError, match="batch_execution_active"):
            batch.execute(root, record, root / record["execution_path"])


@pytest.mark.parametrize("wrong", [{}, {"version": batch.VERSION}])
def test_missing_approval_blocks_before_key_and_http(
    root: Path, wrong: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(_: Path) -> dict[str, str]:
        pytest.fail("key must not be read without approval")

    monkeypatch.setattr(batch, "dotenv_values", forbidden)
    with pytest.raises(ValueError, match="batch_approval_required"):
        batch.execute(root, wrong, root / "arbitrary")


def test_same_approval_cannot_be_redirected_to_another_path(root: Path) -> None:
    record = approval(root, 1)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("no request"))) as http:
        with pytest.raises(ValueError, match="batch_output_scope_mismatch"):
            batch.execute(root, record, root / batch.DIRECTORY / "runs/batch-0002", http=http)


def test_receipt_corruption_stops_before_any_new_request(root: Path) -> None:
    record = approval(root, 1)
    calls: list[int] = []
    with httpx.Client(transport=success_transport(calls)) as http:
        run(root, record, http)
        receipt = next((root / record["execution_path"] / "sources").glob("*.json"))
        meta = json.loads(receipt.read_text())
        meta["params"]["rcept_no"] = "20240101999999"
        receipt.write_text(json.dumps(meta))
        with pytest.raises(ValueError):
            run(root, approval(root, 2, unit=2), http)
    assert calls == [1]


def test_partial_completed_cache_reuse_respects_smaller_approved_budget(root: Path) -> None:
    calls: list[int] = []
    with httpx.Client(transport=success_transport(calls)) as http:
        run(root, approval(root, 1), http)
        record = {**approval(root, 2, unit=2), "max_requests": 1}
        result = run(root, record, http)
    assert calls == [1, 1]
    assert result["reserved_requests"] == 1
    assert result["selected_replay_complete"] is True


def test_immutable_markers_reject_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    batch.write_once(path, {"state": "Attempted"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        batch.write_once(path, {"state": "Verified Local"})
    assert path.read_bytes() == before


def test_approval_is_detached_from_caller_mutations(root: Path) -> None:
    record = approval(root, 2)
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        record["max_requests"] = 100
        record["receipts"].append("99999999999999")
        return httpx.Response(200, content=zipped())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = run(root, record, http)
    assert calls == [1, 1]
    assert result["reserved_requests"] == 2
    stored = batch.load(root / record["execution_path"] / "approval.json")
    assert stored["max_requests"] == 2
    assert len(stored["receipts"]) == 2


def test_duplicate_approval_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "approval.json"
    path.write_text('{"max_requests":1,"max_requests":25}')
    with pytest.raises(ValueError, match="duplicate_evidence_key"):
        batch.load(path)
