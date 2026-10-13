"""Synthetic streaming and archive budgets; never calls an official endpoint."""

import io
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


@pytest.mark.parametrize("mime", ["", "text/html", "application/unrecognized"])
def test_bounded_binary_receipt_requires_reviewed_mime_label(tmp_path: Path, mime: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Type": mime}, content=zipped((b"ok",)))
        )
    ) as http:
        with pytest.raises(ValueError, match="DART_DOCUMENT_MIME_BLOCKED"):
            transport.DartEvidenceClient("synthetic", tmp_path, http, limits=limits()).fetch(
                "document.xml", {"rcept_no": "20240603800041"}
            )
    assert list(tmp_path.iterdir()) == []


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
