"""Secret-safe official disclosure acquisition, separate from offline verification."""

import gzip
import hashlib
import io
import json
import logging
import re
import time
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

import httpx

from donghak_stock_vision.backtest.validation import ContractError, require
from donghak_stock_vision.data.learning import digest

ENDPOINT = "https://opendart.fss.or.kr/api/"
ALLOWED = {"list.json", "corpCode.xml", "document.xml"}
ZIP_BINARY_ENDPOINTS = {"corpCode.xml", "document.xml"}
PARAMETERS = {
    "corp_code",
    "bgn_de",
    "end_de",
    "last_reprt_at",
    "pblntf_ty",
    "pblntf_detail_ty",
    "corp_cls",
    "sort",
    "sort_mth",
    "page_no",
    "page_count",
    "rcept_no",
}
# Official document API guide (DS001 / 2019003), reviewed 2026-10-13.
# These describe reported API codes, not independent proof of the underlying cause.
API_ERROR_CATEGORIES = {
    "010": "unregistered_key",
    "011": "disabled_key",
    "012": "ip_denied",
    "013": "no_data",
    "014": "file_missing",
    "020": "request_limit",
    "021": "company_limit",
    "100": "invalid_field",
    "101": "invalid_access",
    "800": "service_maintenance",
    "900": "undefined_error",
    "901": "expired_key",
}


@dataclass(frozen=True)
class DartTransferLimits:
    """Local pilot budgets, not official OpenDART service limits."""

    response_bytes: int = 16 * 1024 * 1024
    zip_members: int = 100
    member_bytes: int = 16 * 1024 * 1024
    uncompressed_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        for value in (
            self.response_bytes,
            self.zip_members,
            self.member_bytes,
            self.uncompressed_bytes,
        ):
            require(type(value) is int and value > 0, "invalid_dart_transfer_limit")


class DartTransferLimitError(ContractError):
    """A local resource limit must stop processing without a retry."""


def read_cached_body(path: Path, *, limits: DartTransferLimits | None = None) -> bytes:
    if limits is None:
        return gzip.decompress(path.read_bytes())
    with gzip.open(path, "rb") as source:
        raw = source.read(limits.response_bytes + 1)
    if len(raw) > limits.response_bytes:
        raise DartTransferLimitError("DART_RESPONSE_LIMIT_EXCEEDED")
    return raw


class DartAPIError(ContractError):
    """Code-only diagnostics; never include server text, URLs or credentials."""

    def __init__(self, status: str, message_present: bool, *, cached: bool = False) -> None:
        require(re.fullmatch(r"[0-9]{3}", status) is not None, "invalid_dart_diagnostic_status")
        self.status = status
        self.category = API_ERROR_CATEGORIES.get(status, "unknown_status")
        self.message_present = message_present
        prefix = "DART_CACHED_RESPONSE_BLOCKED" if cached else "DART_OFFICIAL_ERROR"
        super().__init__(
            f"{prefix}: status={status} category={self.category} "
            f"message_present={str(message_present).lower()}"
        )


def document_api_error(raw: bytes, *, cached: bool = False) -> DartAPIError | None:
    """Parse a bounded, unambiguous error envelope without exposing message text."""

    if len(raw) > 65536 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return None
    try:
        root = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, LookupError, ValueError):
        return None
    statuses = root.findall("status")
    if root.tag != "result" or len(statuses) != 1:
        return None
    status = (statuses[0].text or "").strip()
    if re.fullmatch(r"[0-9]{3}", status) is None or status == "000":
        return None
    message_present = any((node.text or "").strip() for node in root.findall("message"))
    return DartAPIError(status, message_present, cached=cached)


def response_content_type(value: str) -> str:
    """Retain known MIME labels only; arbitrary header text may contain secrets."""

    mime = value.partition(";")[0].strip().lower()
    if not mime:
        return "not_recorded"
    known = {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "application/xml",
        "text/xml",
        "text/html",
        "application/json",
    }
    return mime if mime in known else "unrecognized"


def request_identity(api: str, params: dict[str, str]) -> dict[str, Any]:
    require(api in ALLOWED, "unsupported_evidence_endpoint")
    require(set(params) <= PARAMETERS, "unsupported_request_parameter")
    if api == "document.xml":
        require(
            set(params) == {"rcept_no"}
            and isinstance(params["rcept_no"], str)
            and re.fullmatch(r"[0-9]{14}", params["rcept_no"]) is not None,
            "invalid_document_request",
        )
    # Every allowed value is a documented code, date, integer or sort literal.
    for v in params.values():
        allowed = v.isascii() and v.isalnum() and len(v) <= 14
        require(allowed, "invalid_request_value")
    return dict(
        publisher="Financial Supervisory Service / Open DART",
        endpoint=ENDPOINT + api,
        params=dict(sorted(params.items())),
        credential_used=True,
    )


def zip_response_valid(api: str, raw: bytes, *, limits: DartTransferLimits | None = None) -> bool:
    """Require ZIP bytes, readable CRCs and safe member names; never extract files."""

    if api not in ZIP_BINARY_ENDPOINTS:
        return True
    return _inspect_zip(raw, limits=limits)


def _inspect_zip(
    raw: bytes, *, limits: DartTransferLimits | None, secret: bytes | None = None
) -> bool:
    if not raw.startswith(b"PK") or (limits and len(raw) > limits.response_bytes):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            names = [PurePosixPath(item.filename).as_posix().casefold() for item in members]
            if not any(not item.is_dir() for item in members) or len(set(names)) != len(names):
                return False
            if limits and (
                len(members) > limits.zip_members
                or any(item.file_size > limits.member_bytes for item in members)
                or sum(item.file_size for item in members) > limits.uncompressed_bytes
            ):
                return False
            for item in members:
                if (
                    not item.filename
                    or item.filename.startswith("/")
                    or "\\" in item.filename
                    or ".." in item.filename.split("/")
                    or re.match(r"[A-Za-z]:", item.filename)
                    or item.flag_bits & 1
                ):
                    return False
            total_read = 0
            for item in members:
                member_read = 0
                tail = b""
                with archive.open(item) as source:
                    while chunk := source.read(65536):
                        member_read += len(chunk)
                        total_read += len(chunk)
                        if limits and (
                            member_read > limits.member_bytes
                            or total_read > limits.uncompressed_bytes
                        ):
                            return False
                        if secret and secret in tail + chunk:
                            return False
                        if secret:
                            tail = (tail + chunk)[-(len(secret) - 1) :] if len(secret) > 1 else b""
                if member_read != item.file_size:
                    return False
            return True
    except (
        ValueError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
        EOFError,
    ):
        return False


def safe_body(raw: bytes, secret: str, *, limits: DartTransferLimits | None = None) -> bool:
    if secret.encode() in raw:
        return False
    if raw.startswith(b"PK"):
        return _inspect_zip(raw, limits=limits, secret=secret.encode())
    return True


class DartEvidenceClient:
    """No URL/error logging; sanitized receipts and immutable response files only.

    Sequential requests have a 0.35-second minimum delay. Transient transport/HTTP
    failures retry at deterministic 1/2-second delays; official quota/auth errors
    stop immediately. Resumption reuses verified cached bytes, never overwrites.
    """

    def __init__(
        self,
        key: str | None,
        output: Path,
        client: httpx.Client,
        *,
        max_attempts: int = 3,
        limits: DartTransferLimits | None = None,
    ) -> None:
        require(bool(key), "BLOCKED_ON_DART_CREDENTIAL")
        require(type(max_attempts) is int and 1 <= max_attempts <= 3, "invalid_dart_attempt_budget")
        self._key = str(key)
        self.max_attempts = max_attempts
        self.limits = limits
        self.last_response: dict[str, Any] = {}
        self.output = output
        self.client = client
        output.mkdir(parents=True, exist_ok=True)

    def _read_response(self, response: httpx.Response) -> bytes:
        if self.limits:
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise DartTransferLimitError("DART_CONTENT_ENCODING_BLOCKED")
            length = response.headers.get("content-length", "")
            if re.fullmatch(r"[0-9]+", length) and int(length) > self.limits.response_bytes:
                raise DartTransferLimitError("DART_RESPONSE_LIMIT_EXCEEDED")
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            self.last_response["bytes_read"] += len(chunk)
            if self.limits and len(body) + len(chunk) > self.limits.response_bytes:
                raise DartTransferLimitError("DART_RESPONSE_LIMIT_EXCEEDED")
            body.extend(chunk)
        self.last_response["response_complete"] = True
        return bytes(body)

    def fetch(self, api: str, params: dict[str, str]) -> tuple[bytes, dict[str, Any]]:
        meta = request_identity(api, params)
        ident = digest(meta)
        receipt = self.output / (ident + ".json")
        body = self.output / (ident + ".gz")
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            require(
                isinstance(saved, dict) and all(saved.get(k) == v for k, v in meta.items()),
                "cache_request_identity_mismatch",
            )
            raw = read_cached_body(body, limits=self.limits)
            require(hashlib.sha256(raw).hexdigest() == saved["raw_sha256"], "cache_hash_mismatch")
            require(saved["http_status"] == 200, "DART_TRANSPORT_BLOCKED")
            require(safe_body(raw, self._key, limits=self.limits), "unsafe_response_not_preserved")
            if api == "document.xml" and (error := document_api_error(raw, cached=True)):
                raise error
            require(
                zip_response_valid(api, raw, limits=self.limits), "DART_CACHED_RESPONSE_BLOCKED"
            )
            return raw, saved
        require(not body.exists(), "incomplete_cache_receipt")
        attempts: list[dict[str, Any]] = []
        raw = b""
        content_type = "not_recorded"
        retrieved_at = ""
        for attempt in range(self.max_attempts):
            time.sleep(0.35 if attempt == 0 else 2 ** (attempt - 1))
            # httpx INFO logs contain query strings. Suppress both libraries around
            # the sensitive request, and never propagate their exception objects.
            prior_logging_threshold = logging.root.manager.disable
            logging.disable(logging.CRITICAL)
            status = 0
            self.last_response = {
                "attempt_count": attempt + 1,
                "http_status": None,
                "bytes_read": 0,
                "content_type": "not_recorded",
                "response_complete": False,
            }
            try:
                with self.client.stream(
                    "GET",
                    ENDPOINT + api,
                    params={**params, "crtfc_key": self._key},
                    headers={"Accept-Encoding": "identity"} if self.limits else None,
                    follow_redirects=False,
                ) as response:
                    status = response.status_code
                    content_type = response_content_type(response.headers.get("content-type", ""))
                    self.last_response.update(http_status=status, content_type=content_type)
                    raw = self._read_response(response)
                    self.last_response["raw_sha256"] = hashlib.sha256(raw).hexdigest()
            except DartTransferLimitError:
                raise
            except Exception:
                raw = b""
                status = 0
            finally:
                logging.disable(prior_logging_threshold)
                self.last_response["retrieved_at"] = datetime.now(UTC).isoformat()
            retrieved_at = datetime.now(UTC).isoformat()
            require(safe_body(raw, self._key, limits=self.limits), "unsafe_response_not_preserved")
            attempts.append(dict(attempt=attempt + 1, http_status=status))
            if api == "document.xml" and (error := document_api_error(raw)):
                raise error
            if status == 200 or (status and status < 500 and status != 429):
                break
        meta.update(
            http_status=status,
            attempts=attempts,
            bytes=len(raw),
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            content_type=content_type,
            retrieved_at=retrieved_at,
        )
        require(status == 200, "DART_TRANSPORT_BLOCKED")
        require(zip_response_valid(api, raw, limits=self.limits), "DART_OFFICIAL_ERROR")
        if self.limits and api in ZIP_BINARY_ENDPOINTS:
            require(
                content_type
                in {
                    "application/zip",
                    "application/x-zip-compressed",
                    "application/octet-stream",
                    "application/xml",
                    "text/xml",
                },
                "DART_DOCUMENT_MIME_BLOCKED",
            )
        with body.open("xb") as handle:
            handle.write(gzip.compress(raw, mtime=0))
        with receipt.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(meta, sort_keys=True, ensure_ascii=True) + "\n")
        return raw, meta

    def filings(self, params: dict[str, str]) -> list[dict[str, Any]]:
        pages = []
        page = 1
        total = 1
        while page <= total:
            raw, _ = self.fetch("list.json", {**params, "page_no": str(page), "page_count": "100"})
            try:
                response = json.loads(raw)
            except ValueError:
                raise ValueError("DART_RESPONSE_BLOCKED") from None
            pages.append(response)
            require(response.get("status") in {"000", "013"}, "DART_OFFICIAL_ERROR")
            if response["status"] == "013":
                break
            total = int(response["total_page"])
            require(0 < total <= 10000, "invalid_page_total")
            page += 1
        return pages
