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
from datetime import UTC, datetime
from pathlib import Path
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


def zip_response_valid(api: str, raw: bytes) -> bool:
    """Require ZIP bytes, readable CRCs and safe member names; never extract files."""

    if api not in ZIP_BINARY_ENDPOINTS:
        return True
    if not raw.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if not any(not item.is_dir() for item in members) or len(set(names)) != len(names):
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
            return archive.testzip() is None
    except (
        ValueError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
        EOFError,
    ):
        return False


def safe_body(raw: bytes, secret: str) -> bool:
    if secret.encode() in raw:
        return False
    if raw.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                return all(secret.encode() not in z.read(n) for n in z.namelist())
        except (
            ValueError,
            zipfile.BadZipFile,
            RuntimeError,
            NotImplementedError,
            zlib.error,
            EOFError,
        ):
            return False
    return True


class DartEvidenceClient:
    """No URL/error logging; sanitized receipts and immutable response files only.

    Sequential requests have a 0.35-second minimum delay. Transient transport/HTTP
    failures retry at deterministic 1/2-second delays; official quota/auth errors
    stop immediately. Resumption reuses verified cached bytes, never overwrites.
    """

    def __init__(
        self, key: str | None, output: Path, client: httpx.Client, *, max_attempts: int = 3
    ) -> None:
        require(bool(key), "BLOCKED_ON_DART_CREDENTIAL")
        require(type(max_attempts) is int and 1 <= max_attempts <= 3, "invalid_dart_attempt_budget")
        self._key = str(key)
        self.max_attempts = max_attempts
        self.output = output
        self.client = client
        output.mkdir(parents=True, exist_ok=True)

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
            raw = gzip.decompress(body.read_bytes())
            require(hashlib.sha256(raw).hexdigest() == saved["raw_sha256"], "cache_hash_mismatch")
            require(saved["http_status"] == 200, "DART_TRANSPORT_BLOCKED")
            require(safe_body(raw, self._key), "unsafe_response_not_preserved")
            if api == "document.xml" and (error := document_api_error(raw, cached=True)):
                raise error
            require(zip_response_valid(api, raw), "DART_CACHED_RESPONSE_BLOCKED")
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
            try:
                response = self.client.get(
                    ENDPOINT + api,
                    params={**params, "crtfc_key": self._key},
                    follow_redirects=False,
                )
                status = response.status_code
                raw = response.content
                content_type = response_content_type(response.headers.get("content-type", ""))
            except Exception:
                raw = b""
            finally:
                logging.disable(prior_logging_threshold)
            retrieved_at = datetime.now(UTC).isoformat()
            require(safe_body(raw, self._key), "unsafe_response_not_preserved")
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
        require(zip_response_valid(api, raw), "DART_OFFICIAL_ERROR")
        body.write_bytes(gzip.compress(raw, mtime=0))
        receipt.write_text(json.dumps(meta, sort_keys=True, ensure_ascii=True) + "\n")
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
