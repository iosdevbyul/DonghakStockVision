"""Offline document transport diagnostics; all keys and responses are synthetic."""

import gzip
import hashlib
import io
import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from donghak_stock_vision.backtest import dart_transport as transport
from donghak_stock_vision.data.learning import digest

PARAMS = {"rcept_no": "20240701000001"}
SECRET = "synthetic-secret-sentinel"


def archive(name: str = "filing.xml", content: bytes = b"synthetic filing") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as zip_file:
        zip_file.writestr(name, content)
    return output.getvalue()


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("donghak_stock_vision.backtest.dart_transport.time.sleep", lambda _: None)


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("800", "service_maintenance"),
        ("010", "unregistered_key"),
        ("011", "disabled_key"),
        ("012", "ip_denied"),
        ("901", "expired_key"),
        ("020", "request_limit"),
        ("100", "invalid_field"),
        ("014", "file_missing"),
        ("777", "unknown_status"),
    ],
)
def test_xml_error_is_classified_without_retry_or_success_cache(
    tmp_path: Path, code: str, category: str
) -> None:
    calls = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/xml; charset=UTF-8"},
            content=f"<result><status>{code}</status><message>synthetic</message></result>".encode(),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError, match="DART_OFFICIAL_ERROR") as caught:
            transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert f"status={code}" in str(caught.value)
    assert f"category={category}" in str(caught.value)
    assert "message_present=true" in str(caught.value)
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_missing_message_does_not_invent_server_text(tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"<result><status>800</status></result>")
        )
    ) as http:
        with pytest.raises(ValueError, match="status=800.*message_present=false"):
            transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "params",
    [{}, {"rcept_no": "short"}, {"rcept_no": "A" * 14}, {**PARAMS, "corp_code": "00100001"}],
)
def test_document_identity_requires_only_exact_receipt(params: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="invalid_document_request"):
        transport.request_identity("document.xml", params)


def test_cache_reuse_rejects_mismatched_request_metadata(tmp_path: Path) -> None:
    raw = archive()
    identity = transport.request_identity("document.xml", PARAMS)
    stem = digest(identity)
    metadata = {
        **identity,
        "params": {"rcept_no": "20240702000002"},
        "http_status": 200,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    }
    receipt = tmp_path / f"{stem}.json"
    body = receipt.with_suffix(".gz")
    receipt.write_text(json.dumps(metadata))
    body.write_bytes(gzip.compress(raw))
    before = (receipt.read_bytes(), body.read_bytes())

    def no_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("cache mismatch must not dispatch a request")

    with httpx.Client(transport=httpx.MockTransport(no_request)) as http:
        with pytest.raises(ValueError, match="cache_request_identity_mismatch"):
            transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert before == (receipt.read_bytes(), body.read_bytes())


def test_success_receipt_records_safe_content_type_and_retrieval_time(tmp_path: Path) -> None:
    raw = archive()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"Content-Type": "application/zip; charset=binary"}, content=raw
            )
        )
    ) as http:
        result, meta = transport.DartEvidenceClient(SECRET, tmp_path, http).fetch(
            "document.xml", PARAMS
        )
    assert result == raw
    assert meta["content_type"] == "application/zip"
    assert datetime.fromisoformat(meta["retrieved_at"]).utcoffset() is not None
    assert meta["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert SECRET not in json.dumps(meta)


def test_mime_does_not_replace_zip_byte_validation(tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"Content-Type": "application/zip"}, content=b"<html>failure</html>"
            )
        )
    ) as http:
        with pytest.raises(ValueError, match="DART_OFFICIAL_ERROR"):
            transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert list(tmp_path.iterdir()) == []


def test_valid_zip_with_xml_mime_is_not_rejected_by_undocumented_mime_rule(tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Type": "text/xml"}, content=archive())
        )
    ) as http:
        _, meta = transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert meta["content_type"] == "text/xml"


@pytest.mark.parametrize("name", ["../filing.xml", "/filing.xml", "C:/filing.xml", "dir/"])
def test_unsafe_or_directory_only_archives_fail_closed(name: str) -> None:
    assert not transport.zip_response_valid("document.xml", archive(name))


def test_bad_member_crc_is_rejected() -> None:
    raw = archive(content=b"unique synthetic contents")
    corrupted = raw.replace(b"unique synthetic contents", b"unique synthetic contentX", 1)
    assert not transport.zip_response_valid("document.xml", corrupted)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_single_attempt_budget_does_not_retry_transport_failure(
    tmp_path: Path, error_type: type[httpx.TransportError]
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise error_type(SECRET, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError, match="DART_TRANSPORT_BLOCKED") as caught:
            transport.DartEvidenceClient(SECRET, tmp_path, http, max_attempts=1).fetch(
                "document.xml", PARAMS
            )
    assert len(calls) == 1
    assert SECRET not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_secret_in_xml_message_is_never_logged_or_preserved(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(_: httpx.Request) -> httpx.Response:
        logging.getLogger("httpx").error(SECRET)
        return httpx.Response(
            200,
            content=f"<result><status>800</status><message>{SECRET}</message></result>".encode(),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError) as caught:
            transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert SECRET not in str(caught.value)
    assert SECRET not in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "raw",
    [
        b"<result><message>synthetic</message></result>",
        b"<result><status>800</status><status>010</status></result>",
        b"<result><status>800",
        b'<!DOCTYPE result [<!ENTITY x "800">]><result><status>&x;</status></result>',
    ],
)
def test_ambiguous_xml_does_not_invent_status(tmp_path: Path, raw: bytes) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw))
    ) as http:
        with pytest.raises(ValueError, match="DART_OFFICIAL_ERROR") as caught:
            transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert "status=800" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_cached_xml_error_is_classified_without_network_or_mutation(tmp_path: Path) -> None:
    raw = b"<result><status>800</status><message>synthetic</message></result>"
    identity = transport.request_identity("document.xml", PARAMS)
    stem = digest(identity)
    receipt = tmp_path / f"{stem}.json"
    body = receipt.with_suffix(".gz")
    receipt.write_text(
        json.dumps({**identity, "http_status": 200, "raw_sha256": hashlib.sha256(raw).hexdigest()})
    )
    body.write_bytes(gzip.compress(raw))
    before = (receipt.read_bytes(), body.read_bytes())

    def no_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("cached errors must not trigger recollection")

    with httpx.Client(transport=httpx.MockTransport(no_request)) as http:
        with pytest.raises(ValueError, match="DART_CACHED_RESPONSE_BLOCKED: status=800"):
            transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert before == (receipt.read_bytes(), body.read_bytes())


def test_unknown_content_type_cannot_leak_secret_into_receipt(tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"Content-Type": f"application/{SECRET}"}, content=archive()
            )
        )
    ) as http:
        _, meta = transport.DartEvidenceClient(SECRET, tmp_path, http).fetch("document.xml", PARAMS)
    assert meta["content_type"] == "unrecognized"
    assert SECRET not in json.dumps(meta)
    assert all(SECRET not in p.read_text() for p in tmp_path.glob("*.json"))


@pytest.mark.parametrize("raw", [b"PKbroken", b"", b"not a zip"])
def test_broken_archive_structure_is_rejected(raw: bytes) -> None:
    assert not transport.zip_response_valid("document.xml", raw)


def test_duplicate_and_encrypted_zip_members_are_rejected() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr("filing.xml", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            zipped.writestr("filing.xml", "second")
    assert not transport.zip_response_valid("document.xml", output.getvalue())
    raw = bytearray(archive())
    # Set encryption bit in the local and central directory flags (synthetic fixture).
    raw[6] |= 1
    central = raw.index(b"PK\x01\x02")
    raw[central + 8] |= 1
    assert not transport.zip_response_valid("document.xml", bytes(raw))


@pytest.mark.parametrize("budget", [0, 4, True])
def test_invalid_attempt_budget_is_rejected(tmp_path: Path, budget: int) -> None:
    with httpx.Client() as http:
        with pytest.raises(ValueError, match="invalid_dart_attempt_budget"):
            transport.DartEvidenceClient(SECRET, tmp_path, http, max_attempts=budget)


def test_existing_default_transport_retry_budget_is_preserved(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError(SECRET, request=request)
        return httpx.Response(200, content=archive())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        _, metadata = transport.DartEvidenceClient(SECRET, tmp_path, http).fetch(
            "document.xml", PARAMS
        )
    assert len(calls) == 3
    assert [row["http_status"] for row in metadata["attempts"]] == [0, 0, 200]


def test_collector_cli_passes_single_attempt_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from donghak_stock_vision.backtest import collect_corporate_action_documents as collector

    seen = []

    def collect(
        root: Path, output: Path, *, offset: int, limit: int, max_attempts: int
    ) -> dict[str, int]:
        seen.append((offset, limit, max_attempts))
        return {"selected_documents": 1}

    monkeypatch.setattr(collector, "collect", collect)
    monkeypatch.setattr(
        "sys.argv",
        [
            "collector",
            "--root",
            str(tmp_path),
            "--output",
            str(tmp_path / "new"),
            "--limit",
            "1",
            "--max-attempts",
            "1",
        ],
    )
    collector.main()
    assert seen == [(0, 1, 1)]
    assert '"selected_documents": 1' in capsys.readouterr().out


def test_collector_cli_reports_only_safe_api_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest import collect_corporate_action_documents as collector

    def collect(
        root: Path, output: Path, *, offset: int, limit: int, max_attempts: int
    ) -> dict[str, int]:
        raise transport.DartAPIError("800", True)

    monkeypatch.setattr(collector, "collect", collect)
    monkeypatch.setattr(
        "sys.argv", ["collector", "--root", str(tmp_path), "--output", str(tmp_path)]
    )
    with pytest.raises(SystemExit, match="status=800 category=service_maintenance"):
        collector.main()
