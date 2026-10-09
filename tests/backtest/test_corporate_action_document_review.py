"""Offline DART document review indexing tests."""

import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx

from donghak_stock_vision.backtest.corporate_action_acquisition import build_document_plan
from donghak_stock_vision.backtest.corporate_action_document_review import (
    build_document_review_index,
    review_document,
)
from donghak_stock_vision.backtest.dart_transport import DartEvidenceClient
from donghak_stock_vision.data.learning import digest


def wrapped(value: dict[str, Any]) -> dict[str, Any]:
    return {"artifact": value, "fingerprint": digest(value)}


def parent() -> dict[str, Any]:
    artifact = {
        "version": "dart_document_review_queue_v1",
        "candidates": [
            {
                "filing": {"rcept_no": "20240701000001"},
                "repository_ticker": "005930",
                "review_categories": ["split"],
                "lifecycle_review": False,
            }
        ],
    }
    return wrapped(artifact)


def zip_body(text: str, *, encoding: str = "utf-8") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("document.xml", text.encode(encoding))
    return output.getvalue()


def test_review_document_surfaces_terms_and_dates_without_approval() -> None:
    raw = zip_body(
        "<html><body>주식분할 기준일은 2024년 8월 9일이며 "
        "변경상장일은 2024-08-20입니다.</body></html>"
    )
    row = {
        "rcept_no": "20240701000001",
        "repository_tickers": ["005930"],
        "review_categories": ["split"],
        "lifecycle_review": False,
    }
    result = review_document(
        raw,
        rcept_no="20240701000001",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        plan_row=row,
    )

    assert result["parse_status"] == "REVIEW_INDEXED"
    assert "split" in result["candidate_event_classes"]
    assert "security_replacement" in result["candidate_event_classes"]
    assert result["date_candidates"] == ["2024-08-09", "2024-08-20"]
    assert result["event_approval_performed"] is False
    assert result["effective_date_approved"] is False


def test_review_document_decodes_cp949_text() -> None:
    raw = zip_body("<html><body>합병기일 2025.01.02</body></html>", encoding="cp949")
    row = {
        "rcept_no": "20240701000001",
        "repository_tickers": ["005930"],
        "review_categories": ["merger"],
        "lifecycle_review": False,
    }
    result = review_document(
        raw,
        rcept_no="20240701000001",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        plan_row=row,
    )

    assert "merger" in result["candidate_event_classes"]
    assert result["date_candidates"] == ["2025-01-02"]


def test_review_index_requires_complete_sources(tmp_path: Path) -> None:
    plan = build_document_plan(parent())

    try:
        build_document_review_index(plan, tmp_path)
    except ValueError as error:
        assert str(error) == "document_sources_incomplete"
    else:
        raise AssertionError("partial acquisition must not create a review index")


def test_review_index_replays_acquired_document(tmp_path: Path) -> None:
    plan = build_document_plan(parent())
    raw = zip_body("<xml>주식분할 2024-08-09</xml>")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        DartEvidenceClient("test-only-secret", tmp_path, http).fetch(
            "document.xml",
            {"rcept_no": "20240701000001"},
        )

    result = build_document_review_index(plan, tmp_path).to_dict()
    assert result["document_count"] == 1
    assert result["indexed_document_count"] == 1
    assert result["unreadable_document_count"] == 0
    assert result["documents_with_event_terms"] == 1
    assert result["event_approval_performed"] is False
    assert result["corporate_action_gate_verified"] is False

    for path in tmp_path.glob("*.json"):
        receipt = json.loads(path.read_text())
        assert "test-only-secret" not in json.dumps(receipt)


def test_unreadable_archive_remains_review_only() -> None:
    raw = b"not-a-zip"
    row = {
        "rcept_no": "20240701000001",
        "repository_tickers": ["005930"],
        "review_categories": ["split"],
        "lifecycle_review": False,
    }
    result = review_document(
        raw,
        rcept_no="20240701000001",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        plan_row=row,
    )
    assert result["parse_status"] == "DOCUMENT_ARCHIVE_UNREADABLE"
    assert result["event_approval_performed"] is False
