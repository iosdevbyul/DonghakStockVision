"""Immutable disclosure evidence; current issuers are not historical securities."""

import hashlib
import io
import re
import zipfile
from datetime import date
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require

REQUIRED_START = "2024-06-14"
REQUIRED_END = "2025-07-07"
OFFICIAL_HOSTS = {"opendart.fss.or.kr", "dart.fss.or.kr", "kind.krx.co.kr", "www.krx.co.kr"}
EVENT_CLASSES = (
    "split",
    "reverse_split",
    "capital_reduction",
    "bonus_issue",
    "stock_dividend",
    "paid_in_capital",
    "rights",
    "merger",
    "division",
    "share_exchange_transfer",
    "name_change",
    "security_replacement",
    "listing_restructuring",
    "other_discontinuity",
)
# Title tokens identify documents for review, never certify an economic event.
TITLE_TOKENS = {
    "split": "\uc8fc\uc2dd\ubd84\ud560",
    "reverse_split": "\uc8fc\uc2dd\ubcd1\ud569",
    "capital_reduction": "\uac10\uc790",
    "bonus_issue": "\ubb34\uc0c1\uc99d\uc790",
    "stock_dividend": "\uc8fc\uc2dd\ubc30\ub2f9",
    "paid_in_capital": "\uc720\uc0c1\uc99d\uc790",
    "rights": "\uad8c\ub9ac\ub77d",
    "merger": "\ud569\ubcd1",
    "division": "\ubd84\ud560",
    "share_exchange_transfer": "\uc8fc\uc2dd\uad50\ud658",
    "name_change": "\uc0c1\ud638\ubcc0\uacbd",
    "listing_restructuring": "\uc7ac\uc0c1\uc7a5",
}
LIFECYCLE_TOKENS = (
    "\uc0c1\uc7a5",
    "\ub9e4\ub9e4\uac70\ub798\uc815\uc9c0",
    "\uac70\ub798\uc815\uc9c0",
    "\uc815\ub9ac\ub9e4\ub9e4",
)


def source_valid(source: dict[str, Any]) -> bool:
    return (
        urlparse(str(source.get("url", ""))).hostname in OFFICIAL_HOSTS
        and re.fullmatch(r"[0-9a-f]{64}", str(source.get("raw_sha256", ""))) is not None
        and bool(source.get("document_reference"))
    )


def corporation_mapping(raw: bytes, universe: tuple[str, ...]) -> FrozenJSON:
    """Exact official stock_code matches, without preferred-share/name inference."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.namelist()) == 1, "corporation_archive_shape")
        xml = archive.read(archive.namelist()[0])
    require(b"<!DOCTYPE" not in xml.upper(), "unsupported_xml_doctype")
    root = ElementTree.fromstring(xml)
    require(root.tag == "result", "corporation_xml_shape")
    index: dict[str, list[dict[str, str]]] = {}
    for node in root.findall("list"):
        row = {child.tag: (child.text or "").strip() for child in node}
        ticker = row.get("stock_code", "")
        if ticker in universe:
            index.setdefault(ticker, []).append(row)
    rows = []
    for ticker in sorted(universe):
        matches = index.get(ticker, [])
        valid = (
            len(matches) == 1
            and re.fullmatch(r"\d{8}", matches[0].get("corp_code", "")) is not None
            and bool(matches[0].get("corp_name"))
        )
        rows.append(
            dict(
                ticker=ticker,
                matches=matches,
                current_status="CURRENT_IDENTITY_VERIFIED"
                if valid
                else "BLOCKED_ON_IDENTITY_MAPPING",
                historical_status="BLOCKED_ON_HISTORICAL_IDENTITY",
                effective_start=None,
                effective_end=None,
            )
        )
    return FrozenJSON.freeze(
        dict(
            version="dart_corporation_mapping_v1",
            source_sha256=hashlib.sha256(raw).hexdigest(),
            securities=rows,
        )
    )


def filing_pages(pages: tuple[dict[str, Any], ...], params: dict[str, str]) -> FrozenJSON:
    """Full pagination verifies a filing-date query, not event/no-event coverage."""
    reasons: list[str] = []
    rows: list[dict[str, Any]] = []
    if not pages:
        reasons.append("missing_pages")
    elif pages[0].get("status") == "013":
        if len(pages) != 1 or pages[0].get("list"):
            reasons.append("inconsistent_empty_query")
    else:
        first = pages[0]
        try:
            total, count = int(first["total_page"]), int(first["total_count"])
            if total < 1 or count < 1 or len(pages) != total:
                reasons.append("partial_pagination")
            for i, page in enumerate(pages, 1):
                if (
                    page.get("status") != "000"
                    or int(page["page_no"]) != i
                    or int(page["total_page"]) != total
                    or int(page["total_count"]) != count
                ):
                    reasons.append("inconsistent_pagination")
                rows.extend(page.get("list", []))
            if len(rows) != count:
                reasons.append("count_mismatch")
        except (KeyError, TypeError, ValueError):
            reasons.append("response_error")
    refs = []
    for row in rows:
        ref = row.get("rcept_no", "")
        if not re.fullmatch(r"\d{14}", ref):
            reasons.append("invalid_filing_reference")
        refs.append(ref)
        if params.get("corp_code") and row.get("corp_code") != params["corp_code"]:
            reasons.append("corp_identity_mismatch")
        if not params["bgn_de"] <= row.get("rcept_dt", "") <= params["end_de"]:
            reasons.append("filing_outside_query")
    if len(set(refs)) != len(refs):
        reasons.append("duplicate_filing")
    return FrozenJSON.freeze(
        dict(
            params=params,
            status="BLOCKED" if reasons else "COMPLETE_FILING_QUERY",
            reasons=sorted(set(reasons)),
            rows=rows,
            empty_means="no_matching_filings_only",
            event_coverage_verified=False,
        )
    )


def document_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    name = str(row.get("report_nm", ""))
    classes = sorted(k for k, token in TITLE_TOKENS.items() if token in name)
    lifecycle = any(token in name for token in LIFECYCLE_TOKENS)
    if not classes and not lifecycle:
        return None
    return dict(
        filing=row,
        review_categories=classes,
        lifecycle_review=lifecycle,
        filing_date=row["rcept_dt"],
        effective_date=None,
        status="BLOCKED_ON_EFFECTIVE_DATE",
        prices_adjusted=False,
        classification_basis="title_review_queue_not_verified_economic_event",
    )


def corporate_status(
    *,
    historical_identity: bool,
    query_complete: bool,
    covered_classes: tuple[str, ...],
    start: str,
    end: str,
    authoritative_complete_semantics: bool,
    events: tuple[dict[str, Any], ...],
) -> str:
    if not historical_identity:
        return "BLOCKED_ON_IDENTITY_MAPPING"
    if (
        not query_complete
        or set(covered_classes) != set(EVENT_CLASSES)
        or start > REQUIRED_START
        or end < REQUIRED_END
        or not authoritative_complete_semantics
    ):
        return "BLOCKED_ON_SOURCE_COVERAGE"
    if any(
        not event.get("effective_date") or not source_valid(event.get("source", {}))
        for event in events
    ):
        return "BLOCKED_ON_EFFECTIVE_DATE"
    return "VERIFIED_EVENT_PRESENT" if events else "VERIFIED_NO_RELEVANT_EVENT"


def reconcile_missing(
    ticker: str, sessions: tuple[str, ...], intervals: tuple[dict[str, Any], ...]
) -> FrozenJSON:
    """Only reviewed exact-security official intervals can explain absence.

    Listing/delisting boundaries must already be normalized by a documented human
    review. This function never derives dates from bars, titles or issuer names.
    """
    allowed = {
        "NOT_YET_LISTED",
        "DELISTED",
        "TRADING_SUSPENDED",
        "SECURITY_IDENTITY_TRANSITION",
        "CORPORATE_RESTRUCTURING",
        "VERIFIED_NON_TRADING_SECURITY_STATE",
    }
    accepted = []
    for item in intervals:
        if (
            item.get("ticker") != ticker
            or item.get("reason") not in allowed
            or not item.get("historical_security_identity_verified")
            or not source_valid(item.get("source", {}))
            or not item.get("review_reference")
            or not item.get("source_excerpt")
        ):
            continue
        try:
            start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
        except (KeyError, ValueError, TypeError):
            continue
        if start <= end:
            accepted.append(item)
    explained, unexplained, conflicts = {}, [], []
    for session in sessions:
        matches = [i for i in accepted if i["start"] <= session <= i["end"]]
        if len(matches) == 1:
            explained[session] = matches[0]
        elif matches:
            conflicts.append(session)
        else:
            unexplained.append(session)
    return FrozenJSON.freeze(
        dict(
            ticker=ticker,
            explained=explained,
            unexplained=unexplained,
            conflicting=conflicts,
            status="BLOCKED" if unexplained or conflicts else "VERIFIED",
        )
    )


def cross_reference(kind: dict[str, Any], dart: dict[str, Any]) -> str:
    """A reviewed DART link is required; similar 14-digit source IDs are insufficient."""
    if kind.get("dart_rcept_no") != dart.get("rcept_no") or not kind.get("dart_rcept_no"):
        return "UNRESOLVED_REFERENCE"
    if not source_valid(kind.get("link_source", {})):
        return "UNVERIFIED_REFERENCE_LINK"
    if kind.get("corp_code") != dart.get("corp_code"):
        return "IDENTITY_CONFLICT"
    if kind.get("ticker") != dart.get("stock_code"):
        return "SECURITY_CONFLICT"
    return "MATCHED_FILING_REFERENCE_ONLY"
