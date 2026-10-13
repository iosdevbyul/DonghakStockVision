"""Synthetic streaming and archive budgets; never calls an official endpoint."""

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from donghak_stock_vision.backtest import dart_transport as transport


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def zipped(contents: tuple[bytes, ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, content in enumerate(contents):
            archive.writestr(f"filing-{index}.xml", content)
    return output.getvalue()


def limits(**changes: int) -> transport.DartTransferLimits:
    return transport.DartTransferLimits(
        **{
            "response_bytes": 1024,
            "zip_members": 2,
            "member_bytes": 128,
            "uncompressed_bytes": 200,
            **changes,
        }
    )


def test_stream_limit_stops_reading_and_never_retries(tmp_path: Path) -> None:
    stream = Chunks([b"a" * 70000, b"b" * 70000, b"never read"])
    calls = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = transport.DartEvidenceClient(
            "synthetic-secret", tmp_path, http, limits=limits(response_bytes=65536)
        )
        with pytest.raises(ValueError, match="DART_RESPONSE_LIMIT_EXCEEDED"):
            client.fetch("document.xml", {"rcept_no": "20240603800041"})
    assert stream.read_count < 3
    assert stream.closed
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_content_length_limit_stops_before_body_iteration(tmp_path: Path) -> None:
    stream = Chunks([b"must not read"])
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Length": "1025"}, stream=stream)
        )
    ) as http:
        with pytest.raises(ValueError, match="DART_RESPONSE_LIMIT_EXCEEDED"):
            transport.DartEvidenceClient("synthetic", tmp_path, http, limits=limits()).fetch(
                "document.xml", {"rcept_no": "20240603800041"}
            )
    assert stream.read_count == 0
    assert stream.closed


@pytest.mark.parametrize(
    ("contents", "changes"),
    [
        ((b"a", b"b", b"c"), {}),
        ((b"a" * 129,), {}),
        ((b"a" * 120, b"b" * 120), {}),
    ],
)
def test_archive_limits_reject_before_unbounded_decompression(
    contents: tuple[bytes, ...], changes: dict[str, int]
) -> None:
    raw = zipped(contents)
    assert not transport.zip_response_valid("document.xml", raw, limits=limits(**changes))
    assert not transport.safe_body(raw, "synthetic-secret", limits=limits(**changes))


def test_archive_limits_allow_exact_boundaries() -> None:
    raw = zipped((b"a" * 128, b"b" * 72))
    assert transport.zip_response_valid("document.xml", raw, limits=limits())
    assert transport.safe_body(raw, "synthetic-secret", limits=limits())


def test_secret_crossing_read_chunk_boundary_is_blocked() -> None:
    raw = zipped((b"a" * 65530 + b"synthetic-secret" + b"b" * 65536,))
    budget = limits(response_bytes=200000, member_bytes=200000, uncompressed_bytes=200000)
    assert not transport.safe_body(raw, "synthetic-secret", limits=budget)


def test_http_content_encoding_is_blocked_before_expansion(tmp_path: Path) -> None:
    stream = Chunks([b"must not decode"])
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream)
        )
    ) as http:
        with pytest.raises(ValueError, match="DART_CONTENT_ENCODING_BLOCKED"):
            transport.DartEvidenceClient("synthetic", tmp_path, http, limits=limits()).fetch(
                "document.xml", {"rcept_no": "20240603800041"}
            )
    assert stream.read_count == 0
    assert stream.closed


@pytest.mark.parametrize("value", [0, -1, True])
def test_invalid_limit_is_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="invalid_dart_transfer_limit"):
        limits(member_bytes=value)


@pytest.mark.parametrize(
    ("mime", "label"),
    [
        ("application/zip", "application/zip"),
        ("application/octet-stream", "application/octet-stream"),
        ("application/unrecognized", "unrecognized"),
        (None, "not_recorded"),
        ("", "not_recorded"),
        ("text/xml", "text/xml"),
        ("text/html", "text/html"),
    ],
)
def test_bounded_document_accepts_valid_zip_independently_of_mime(
    tmp_path: Path, mime: str | None, label: str
) -> None:
    raw = zipped((b"ok",))
    headers = {} if mime is None else {"Content-Type": mime}
    calls = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, headers=headers, content=raw)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result, meta = transport.DartEvidenceClient(
            "synthetic", tmp_path, http, max_attempts=1, limits=limits()
        ).fetch("document.xml", {"rcept_no": "20240603800041"})
    assert result == raw
    assert meta["content_type"] == label
    assert len(calls) == 1
    assert len(list(tmp_path.glob("*.gz"))) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.parametrize("mime", ["application/zip", "application/unrecognized", None])
@pytest.mark.parametrize(
    "kind", ["xml_800", "html", "json", "nonzip", "broken", "crc", "path", "size"]
)
def test_bounded_document_mime_never_bypasses_content_checks(
    tmp_path: Path, mime: str | None, kind: str
) -> None:
    if kind == "crc":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("filing.xml", b"unique content")
        raw = output.getvalue().replace(b"unique content", b"unique contenX", 1)
    elif kind == "path":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../filing.xml", b"ok")
        raw = output.getvalue()
    else:
        raw = {
            "xml_800": b"<result><status>800</status></result>",
            "html": b"<html>failure</html>",
            "json": b'{"status":"800"}',
            "nonzip": b"not a zip",
            "broken": b"PKbroken",
            "size": zipped((b"a" * 120, b"b" * 120)),
        }[kind]
    headers = {} if mime is None else {"Content-Type": mime}
    calls = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, headers=headers, content=raw)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError) as caught:
            transport.DartEvidenceClient(
                "synthetic", tmp_path, http, max_attempts=1, limits=limits()
            ).fetch("document.xml", {"rcept_no": "20240603800041"})
    if kind == "xml_800":
        assert isinstance(caught.value, transport.DartAPIError)
        assert caught.value.status == "800"
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_bounded_document_keeps_secret_mime_out_of_audit_and_receipt(tmp_path: Path) -> None:
    secret = "synthetic-secret-sentinel"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"Content-Type": f"application/{secret}"}, content=zipped((b"ok",))
            )
        )
    ) as http:
        client = transport.DartEvidenceClient(secret, tmp_path, http, limits=limits())
        _, meta = client.fetch("document.xml", {"rcept_no": "20240603800041"})
    assert meta["content_type"] == client.last_response["content_type"] == "unrecognized"
    assert secret not in json.dumps(client.last_response)
    assert secret not in json.dumps(meta)
    assert all(secret not in p.read_text() for p in tmp_path.glob("*.json"))


def test_cached_body_expansion_is_bounded(tmp_path: Path) -> None:
    import gzip

    body = tmp_path / "body.gz"
    body.write_bytes(gzip.compress(b"a" * 1025))
    before = body.read_bytes()
    with pytest.raises(ValueError, match="DART_RESPONSE_LIMIT_EXCEEDED"):
        transport.read_cached_body(body, limits=limits())
    assert body.read_bytes() == before


def test_normalized_filename_collision_is_rejected() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("filing.xml", "first")
        archive.writestr("./filing.xml", "second")
    assert not transport.zip_response_valid("document.xml", output.getvalue(), limits=limits())


def test_other_bounded_binary_endpoint_keeps_existing_mime_contract(tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"Content-Type": "application/unrecognized"}, content=zipped((b"ok",))
            )
        )
    ) as http:
        with pytest.raises(ValueError, match="DART_DOCUMENT_MIME_BLOCKED"):
            transport.DartEvidenceClient("synthetic", tmp_path, http, limits=limits()).fetch(
                "corpCode.xml", {}
            )
    assert list(tmp_path.iterdir()) == []
