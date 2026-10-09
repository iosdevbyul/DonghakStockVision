"""Offline review indexing for acquired DART corporate-action documents."""

from __future__ import annotations

import gzip
import hashlib
import html
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, cast

from donghak_stock_vision.backtest.corporate_action_acquisition import (
    inspect_document_sources,
)
from donghak_stock_vision.backtest.dart_evidence import EVENT_CLASSES, TITLE_TOKENS
from donghak_stock_vision.backtest.dart_transport import request_identity
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest

TEXT_EXTENSIONS = {".xml", ".html", ".htm", ".txt"}
EXTRA_TOKENS: dict[str, tuple[str, ...]] = {
    "security_replacement": ("변경상장", "신주상장", "주권교부"),
    "other_discontinuity": ("기준일", "효력발생일"),
}
DATE_PATTERNS = (
    re.compile(
        r"(?<![0-9])(20[0-9]{2})[.\-/](0?[1-9]|1[0-2])[.\-/]"
        r"(0?[1-9]|[12][0-9]|3[01])(?![0-9])"
    ),
    re.compile(
        r"(?<![0-9])(20[0-9]{2})년\s*(0?[1-9]|1[0-2])월\s*"
        r"(0?[1-9]|[12][0-9]|3[01])일"
    ),
    re.compile(
        r"(?<![0-9])(20[0-9]{2})(0[1-9]|1[0-2])"
        r"(0[1-9]|[12][0-9]|3[01])(?![0-9])"
    ),
)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _plain_text(raw: bytes) -> str:
    value = _decode(raw)
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _tokens() -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {
        event_class: (token,) for event_class, token in TITLE_TOKENS.items()
    }
    for event_class in EVENT_CLASSES:
        result.setdefault(event_class, ())
    for event_class, extra in EXTRA_TOKENS.items():
        result[event_class] = tuple(sorted(set(result[event_class] + extra)))
    return result


def _dates(text: str) -> list[str]:
    result: set[str] = set()
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            year, month, day = (int(value) for value in match.groups())
            normalized = f"{year:04d}-{month:02d}-{day:02d}"
            try:
                date.fromisoformat(normalized)
            except ValueError:
                continue
            result.add(normalized)
    return sorted(result)


def _snippets(text: str, token: str) -> list[str]:
    result = []
    start = 0
    while len(result) < 3:
        index = text.find(token, start)
        if index < 0:
            break
        left = max(0, index - 100)
        right = min(len(text), index + len(token) + 100)
        result.append(text[left:right])
        start = index + len(token)
    return result


def review_document(
    raw_archive: bytes,
    *,
    rcept_no: str,
    raw_sha256: str,
    plan_row: dict[str, Any],
) -> dict[str, Any]:
    """Surface review candidates without approving events or effective dates."""

    require(
        hashlib.sha256(raw_archive).hexdigest() == raw_sha256,
        "document_review_hash_mismatch",
    )
    require(plan_row.get("rcept_no") == rcept_no, "document_review_plan_mismatch")

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_archive))
    except zipfile.BadZipFile:
        return {
            "rcept_no": rcept_no,
            "raw_sha256": raw_sha256,
            "parse_status": "DOCUMENT_ARCHIVE_UNREADABLE",
            "entries": [],
            "candidate_event_classes": [],
            "date_candidates": [],
            "event_approval_performed": False,
            "effective_date_approved": False,
        }

    entries: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for name in sorted(archive.namelist()):
        if name.endswith("/") or Path(name).suffix.lower() not in TEXT_EXTENSIONS:
            continue
        raw = archive.read(name)
        text = _plain_text(raw)
        text_parts.append(text)
        entries.append(
            {
                "name": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "text_characters": len(text),
            }
        )
    archive.close()

    text = " ".join(text_parts)
    evidence: dict[str, dict[str, Any]] = {}
    token_map = _tokens()
    for event_class in EVENT_CLASSES:
        hits = [token for token in token_map[event_class] if token in text]
        if not hits:
            continue
        snippets: list[str] = []
        for token in hits:
            snippets.extend(_snippets(text, token))
        evidence[event_class] = {"tokens": hits, "snippets": snippets[:6]}

    return {
        "rcept_no": rcept_no,
        "raw_sha256": raw_sha256,
        "repository_tickers": list(plan_row["repository_tickers"]),
        "plan_review_categories": list(plan_row["review_categories"]),
        "lifecycle_review": bool(plan_row["lifecycle_review"]),
        "parse_status": "REVIEW_INDEXED" if entries else "NO_TEXT_ENTRY",
        "entries": entries,
        "candidate_event_classes": sorted(evidence),
        "event_term_evidence": evidence,
        "date_candidates": _dates(text),
        "event_approval_performed": False,
        "effective_date_approved": False,
    }


def build_document_review_index(plan: FrozenJSON, source_dir: Path) -> FrozenJSON:
    """Build a review-only index after complete acquisition is verified."""

    inventory = inspect_document_sources(plan, source_dir).to_dict()
    require(inventory["complete"] is True, "document_sources_incomplete")

    plan_value = plan.to_dict()
    plan_rows = cast(list[dict[str, Any]], plan_value["documents"])
    plan_by_receipt = {row["rcept_no"]: row for row in plan_rows}

    receipts: dict[str, tuple[Path, dict[str, Any]]] = {}
    for receipt_path in sorted(source_dir.glob("*.json")):
        meta_object = json.loads(receipt_path.read_text())
        require(type(meta_object) is dict, "document_receipt_object_required")
        meta = cast(dict[str, Any], meta_object)
        params_object = meta.get("params")
        require(type(params_object) is dict, "document_receipt_params_required")
        params = cast(dict[str, str], params_object)
        identity = request_identity("document.xml", params)
        require(digest(identity) == receipt_path.stem, "document_request_identity_mismatch")
        rcept_no_object = params.get("rcept_no")
        require(isinstance(rcept_no_object, str), "document_receipt_number_required")
        rcept_no = cast(str, rcept_no_object)
        require(rcept_no in plan_by_receipt, "unexpected_document_receipt")
        require(rcept_no not in receipts, "duplicate_document_receipt")
        receipts[rcept_no] = (receipt_path.with_suffix(".gz"), meta)

    reviews: list[dict[str, Any]] = []
    for rcept_no in sorted(plan_by_receipt):
        body_path, meta = receipts[rcept_no]
        reviews.append(
            review_document(
                gzip.decompress(body_path.read_bytes()),
                rcept_no=rcept_no,
                raw_sha256=str(meta["raw_sha256"]),
                plan_row=plan_by_receipt[rcept_no],
            )
        )

    return FrozenJSON.freeze(
        {
            "version": "corporate_action_document_review_index_v1",
            "document_plan_fingerprint": plan.identifier,
            "document_sources_fingerprint": digest(inventory),
            "document_count": len(reviews),
            "indexed_document_count": sum(
                row["parse_status"] == "REVIEW_INDEXED" for row in reviews
            ),
            "unreadable_document_count": sum(
                row["parse_status"] == "DOCUMENT_ARCHIVE_UNREADABLE" for row in reviews
            ),
            "no_text_document_count": sum(
                row["parse_status"] == "NO_TEXT_ENTRY" for row in reviews
            ),
            "documents_with_event_terms": sum(
                bool(row["candidate_event_classes"]) for row in reviews
            ),
            "documents_with_date_candidates": sum(bool(row["date_candidates"]) for row in reviews),
            "documents": reviews,
            "event_approval_performed": False,
            "effective_date_approval_performed": False,
            "corporate_action_gate_verified": False,
        }
    )
