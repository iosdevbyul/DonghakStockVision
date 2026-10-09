"""Secret-safe official disclosure acquisition, separate from offline verification."""

import gzip
import hashlib
import io
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from donghak_stock_vision.backtest.validation import require
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


def request_identity(api: str, params: dict[str, str]) -> dict[str, Any]:
    require(api in ALLOWED, "unsupported_evidence_endpoint")
    require(set(params) <= PARAMETERS, "unsupported_request_parameter")
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
    """Require documented ZIP payloads for binary DART endpoints."""

    if api not in ZIP_BINARY_ENDPOINTS:
        return True
    if not raw.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return bool(archive.namelist())
    except (ValueError, zipfile.BadZipFile):
        return False


def safe_body(raw: bytes, secret: str) -> bool:
    if secret.encode() in raw:
        return False
    if raw.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                return all(secret.encode() not in z.read(n) for n in z.namelist())
        except (ValueError, zipfile.BadZipFile):
            return False
    return True


class DartEvidenceClient:
    """No URL/error logging; sanitized receipts and immutable response files only.

    Sequential requests have a 0.35-second minimum delay. Transient transport/HTTP
    failures retry at deterministic 1/2-second delays; official quota/auth errors
    stop immediately. Resumption reuses verified cached bytes, never overwrites.
    """

    def __init__(self, key: str | None, output: Path, client: httpx.Client) -> None:
        require(bool(key), "BLOCKED_ON_DART_CREDENTIAL")
        self._key = str(key)
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
            raw = gzip.decompress(body.read_bytes())
            require(hashlib.sha256(raw).hexdigest() == saved["raw_sha256"], "cache_hash_mismatch")
            require(saved["http_status"] == 200, "DART_TRANSPORT_BLOCKED")
            require(zip_response_valid(api, raw), "DART_CACHED_RESPONSE_BLOCKED")
            return raw, saved
        require(not body.exists(), "incomplete_cache_receipt")
        attempts: list[dict[str, Any]] = []
        raw = b""
        for attempt in range(3):
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
            except Exception:
                raw = b""
            finally:
                logging.disable(prior_logging_threshold)
            require(safe_body(raw, self._key), "unsafe_response_not_preserved")
            attempts.append(dict(attempt=attempt + 1, http_status=status))
            if status == 200 or (status and status < 500 and status != 429):
                break
        meta.update(
            http_status=status,
            attempts=attempts,
            bytes=len(raw),
            raw_sha256=hashlib.sha256(raw).hexdigest(),
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
