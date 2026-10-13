"""One-shot pilot orchestration with fake keys and MockTransport only."""

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from donghak_stock_vision.backtest import collect_corporate_action_documents as collector
from donghak_stock_vision.data.learning import digest

SECRET = "p" * 40


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "docs/evidence/baseline-dart/artifacts"
    parent.mkdir(parents=True)
    artifact = {
        "version": "dart_document_review_queue_v1",
        "candidates": [
            {
                "filing": {"rcept_no": receipt},
                "repository_ticker": "005930",
                "review_categories": ["split"],
                "lifecycle_review": False,
            }
            for receipt in (collector.PILOT_RECEIPT, "20240701000001")
        ],
    }
    (parent / "corporate-action-events.json").write_text(
        json.dumps({"artifact": artifact, "fingerprint": digest(artifact)})
    )
    monkeypatch.setattr(collector, "dotenv_values", lambda _: {"DART_API_KEY": SECRET})
    monkeypatch.setattr("donghak_stock_vision.backtest.dart_transport.time.sleep", lambda _: None)
    return tmp_path


def zip_body() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("filing.xml", "synthetic fixture")
    return output.getvalue()


@pytest.mark.parametrize("mime", ["application/zip", "application/unrecognized", None])
def test_pilot_is_fixed_one_request_and_replays_only_selected_scope(
    root: Path, mime: str | None
) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/document.xml"
        assert request.url.params["rcept_no"] == collector.PILOT_RECEIPT
        assert request.headers["accept-encoding"] == "identity"
        headers = {} if mime is None else {"Content-Type": mime}
        return httpx.Response(200, headers=headers, content=zip_body())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        report = collector.pilot(root, access_confirmed=True, http=http)
        with pytest.raises(ValueError, match="pilot_output_must_be_new"):
            collector.pilot(root, access_confirmed=True, http=http)
    assert len(seen) == 1
    assert report["success"] is True
    assert report["attempt_count"] == 1
    assert report["offline_replay_complete"] is True
    assert report["full_plan_complete"] is False
    assert report["full_plan_missing_document_count"] == 1
    assert report["api_status"] is None
    assert report["corporate_action_gate_verified"] is False
    output = root / collector.PILOT_DIRECTORY
    for path in output.rglob("*.json"):
        assert SECRET not in path.read_text()
        assert "crtfc_key" not in path.read_text()


@pytest.mark.parametrize("kind", ["xml_800", "http_error", "timeout"])
def test_failure_records_safe_status_and_never_retries_or_promotes(root: Path, kind: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if kind == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if kind == "http_error":
            return httpx.Response(503, content=b"synthetic failure")
        return httpx.Response(200, content=b"<result><status>800</status></result>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        report = collector.pilot(root, access_confirmed=True, http=http)
    assert len(calls) == 1
    assert report["success"] is False
    assert report["normal_zip_verified"] is False
    assert report["attempt_count"] == 1
    assert report["api_status"] == ("800" if kind == "xml_800" else None)
    assert report["http_status"] == (
        None if kind == "timeout" else 503 if kind == "http_error" else 200
    )
    assert SECRET not in json.dumps(report)
    output = root / collector.PILOT_DIRECTORY
    assert list(output.glob("*.json")) == []
    assert list(output.glob("*.gz")) == []
    assert (output / "audit/pilot-report.json").exists()


@pytest.mark.parametrize("confirmed", [False])
def test_unconfirmed_access_stops_before_key_loading_and_network(
    root: Path, confirmed: bool
) -> None:
    with pytest.raises(ValueError, match="pilot_access_and_quota_not_confirmed"):
        collector.pilot(root, access_confirmed=confirmed)
    assert not (root / collector.PILOT_DIRECTORY).exists()


def test_existing_empty_directory_stops_before_network(root: Path) -> None:
    output = root / collector.PILOT_DIRECTORY
    output.mkdir(parents=True)
    with pytest.raises(ValueError, match="pilot_output_must_be_new"):
        collector.pilot(root, access_confirmed=True)
    assert list(output.iterdir()) == []


def test_legacy_failure_cache_is_never_modified(root: Path) -> None:
    old = root / ".data/corporate-action-documents-status-800"
    old.mkdir(parents=True)
    stored = old / "sentinel.json"
    stored.write_bytes(b"synthetic old cache")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as http:
        collector.pilot(root, access_confirmed=True, http=http)
    assert stored.read_bytes() == b"synthetic old cache"


def test_legacy_batch_cannot_use_reserved_pilot_path(root: Path) -> None:
    with pytest.raises(ValueError, match="pilot_path_requires_single_request_mode"):
        collector.collect(root, root / collector.PILOT_DIRECTORY, offset=0, limit=25)
    assert not (root / collector.PILOT_DIRECTORY).exists()


def test_owned_http_client_configuration_has_no_transport_retries(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    calls = []
    configured: dict[str, Any] = {}

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=b"<result><status>800</status></result>")

    fake = httpx.Client(transport=httpx.MockTransport(handler))
    stub_transport = object()

    def make_transport(*, retries: int) -> object:
        assert retries == 0
        return stub_transport

    def make_client(**kwargs: Any) -> httpx.Client:
        configured.update(kwargs)
        return fake

    monkeypatch.setattr(
        "donghak_stock_vision.backtest.collect_corporate_action_documents.httpx.HTTPTransport",
        make_transport,
    )
    monkeypatch.setattr(
        "donghak_stock_vision.backtest.collect_corporate_action_documents.httpx.Client", make_client
    )
    report = collector.pilot(root, access_confirmed=True)
    assert configured == {"timeout": 45, "transport": stub_transport, "trust_env": False}
    assert calls == [1]
    assert fake.is_closed
    assert report["attempt_count"] == 1


def test_client_initialization_failure_is_audited_as_zero_attempts(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_transport(*, retries: int) -> object:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(
        "donghak_stock_vision.backtest.collect_corporate_action_documents.httpx.HTTPTransport",
        fail_transport,
    )
    report = collector.pilot(root, access_confirmed=True)
    assert report["attempt_count"] == 0
    assert report["http_status"] is None
    assert report["failure_code"] == "pilot_unexpected_failure"
    assert SECRET not in json.dumps(report)
    assert (root / collector.PILOT_DIRECTORY / "audit/pilot-report.json").exists()


def test_client_close_failure_does_not_lose_safe_audit(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    fake = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"<result><status>800</status></result>")
        )
    )
    original_close = fake.close

    def close_failure() -> None:
        original_close()
        raise RuntimeError(SECRET)

    def make_client(**kwargs: Any) -> httpx.Client:
        return fake

    monkeypatch.setattr(fake, "close", close_failure)
    monkeypatch.setattr(
        "donghak_stock_vision.backtest.collect_corporate_action_documents.httpx.Client", make_client
    )
    report = collector.pilot(root, access_confirmed=True)
    assert report["client_close_failed"] is True
    assert report["api_status"] == "800"
    assert SECRET not in json.dumps(report)
    assert (root / collector.PILOT_DIRECTORY / "audit/pilot-report.json").exists()
