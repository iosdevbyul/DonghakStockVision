"""Normalize official evidence only; never infer prices, mappings or no-event status."""

import calendar
import hashlib
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha

# Exact source labels, escaped to keep authored source text English/ASCII.
HOLIDAY_LABELS = {
    "\ud604\ucda9\uc77c": "memorial_day",
    "\uad11\ubcf5\uc808": "liberation_day",
    "\ucd94\uc11d\uc804\ub0a0": "chuseok_eve",
    "\ucd94\uc11d": "chuseok",
    "\ucd94\uc11d\uc775\uc77c": "chuseok_following_day",
    "\ub300\uccb4\uacf5\ud734\uc77c": "source_label_substitute_holiday",
    "\uac1c\ucc9c\uc808": "national_foundation_day",
    "\ud55c\uae00\ub0a0": "hangul_day",
    "\uc131\ud0c4\uc808": "christmas",
    "\uc5f0\ub9d0\ud734\uc7a5\uc77c": "exchange_year_end_closure",
    "\uc2e0\uc815": "new_year",
    "\uc124\ub0a0\uc804\ub0a0": "lunar_new_year_eve",
    "\uc124\ub0a0": "lunar_new_year",
    "\uc124\ub0a0\uc775\uc77c": "lunar_new_year_following_day",
    "\uadfc\ub85c\uc790\uc758\ub0a0": "labor_day",
    "\uc5b4\ub9b0\uc774\ub0a0": "childrens_day",
    "\ub300\ud1b5\ub839\uc120\uac70\uc77c": "presidential_election_day",
}
KIND_URL = "https://kind.krx.co.kr/common/stockschedule.do"


def semantic_source(source: dict[str, Any]) -> dict[str, Any]:
    """Separate transport paths/timestamps from source content and query identity."""
    keys = (
        "url",
        "method",
        "params",
        "status",
        "raw_sha256",
        "publisher",
        "document_name",
        "requested_period",
        "response_url",
        "result",
    )
    return {k: source[k] for k in keys if k in source}


class Cells(HTMLParser):
    """Capture source table cells and rows without executing page scripts."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.cells: list[tuple[str, str]] = []
        self.row: list[str] = []
        self.text: list[str] | None = None
        self.kind = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        if tag == "td":
            self.text = []
            self.kind = dict(attrs).get("class") or ""

    def handle_data(self, data: str) -> None:
        if self.text is not None and data.strip():
            self.text.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.text is not None:
            text = " ".join(self.text)
            self.cells.append((self.kind, text))
            self.row.append(text)
            self.text = None
        if tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = []


def normalize_month(raw: bytes, source: dict[str, Any]) -> FrozenJSON:
    """Accept only the publicly documented KIND trading-calendar response shape."""
    require(source.get("url") == KIND_URL, "official_calendar_source_required")
    require(source.get("status") == 200, "calendar_http_failure")
    require(hashlib.sha256(raw).hexdigest() == source.get("raw_sha256"), "raw_hash_mismatch")
    p = source["params"]
    require(p.get("method") == "searchTraidingCalendar", "calendar_method_mismatch")
    require(p.get("ldMktTpCd") == "1", "cash_equity_scope_required")
    year, month = int(p["selYear"]), int(p["selMonth"])
    html = raw.decode("utf-8")
    require('id="calBig"' in html, "calendar_shape_missing")
    parsed = Cells()
    parsed.feed(html)
    days: dict[str, str] = {}
    annotations: dict[str, str] = {}
    for css, text in parsed.cells:
        if not text:
            continue
        parts = text.split(maxsplit=1)
        require(parts[0].isdigit(), "unexpected_calendar_cell")
        day = date(year, month, int(parts[0]))
        key = day.isoformat()
        require(key not in days, "duplicate_calendar_day")
        weekend = day.weekday() >= 5
        require(weekend == (css in {"sun", "sat"}), "calendar_weekday_mismatch")
        if len(parts) == 2:
            require(parts[1] in HOLIDAY_LABELS, "unreviewed_calendar_annotation")
            annotations[key] = HOLIDAY_LABELS[parts[1]]
        days[key] = "weekend" if weekend else "holiday" if len(parts) == 2 else "session"
    require(len(days) == calendar.monthrange(year, month)[1], "incomplete_calendar_month")
    return FrozenJSON.freeze(
        dict(days=days, annotations=annotations, source=semantic_source(source))
    )


def merge_months(months: tuple[FrozenJSON, ...], rule: FrozenJSON) -> FrozenJSON:
    require(bool(months), "calendar_months_required")
    months = tuple(sorted(months, key=lambda m: min(m.to_dict()["days"])))
    days: dict[str, str] = {}
    annotations: dict[str, str] = {}
    sources = []
    for month in months:
        d = month.to_dict()
        require(not days.keys() & d["days"].keys(), "overlapping_calendar_month")
        days.update(d["days"])
        annotations.update(d["annotations"])
        sources.append(d["source"])
    ordered = sorted(date.fromisoformat(d) for d in days)
    require(len(ordered) == (ordered[-1] - ordered[0]).days + 1, "calendar_coverage_gap")
    return FrozenJSON.freeze(
        dict(
            version="kind_cash_calendar_v1",
            days=days,
            annotations=annotations,
            source_urls=[KIND_URL, rule.to_dict()["url"]],
            source_content_hashes=sorted(
                {s["raw_sha256"] for s in sources} | {rule.to_dict()["raw_sha256"]}
            ),
            sources=sources,
            normalized_month_fingerprints=[m.identifier for m in months],
            rule_source=rule.to_dict(),
            review_reference="KIND_trading_calendar_plus_KRX_cash_equity_holiday_rules",
            rule="kospi_cash_equity_T_plus_2_settlement_sessions",
            effective_start=ordered[0].isoformat(),
            effective_end=ordered[-1].isoformat(),
            research_exclusions_are_holidays=False,
        )
    )


def extract_action_rows(raw: bytes, source: dict[str, Any]) -> FrozenJSON:
    """Keep official issuer/disclosure IDs without guessing six-character securities."""
    require(source.get("url") == KIND_URL, "official_action_source_required")
    require(hashlib.sha256(raw).hexdigest() == source.get("raw_sha256"), "raw_hash_mismatch")
    html = raw.decode("utf-8")
    records = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.S):
        issuer = re.search(r"companysummary_open\('([^']+)'\)", row)
        disclosure = re.search(r"openDisclsViewer\('([0-9]+)'", row)
        if issuer and disclosure:
            cells = Cells()
            cells.feed("<tr>" + row + "</tr>")
            records.append(
                dict(
                    issuer_code=issuer[1],
                    disclosure_id=disclosure[1],
                    kospi_marker="icn_t_yu.gif" in row,
                    cells=[text for _, text in cells.cells],
                    repository_ticker=None,
                    effective_date_verified=False,
                    status="BLOCKED_ON_IDENTITY_MAPPING",
                )
            )
    return FrozenJSON.freeze(
        dict(
            source=semantic_source(source),
            rows=records,
            complete_result_semantics_verified=False,
            source_shape_valid='<article class="scrarea' in html,
            no_event_certified=False,
            prices_adjusted=False,
        )
    )


def mapping_status(entries: tuple[FrozenJSON, ...], start: date, end: date) -> str:
    if not entries:
        return "BLOCKED_ON_IDENTITY_MAPPING"
    values = [e.to_dict() for e in entries]
    try:
        ordered = sorted(values, key=lambda e: e["effective_start"])
        for e in ordered:
            sha(e["source_sha256"])
            require(
                date.fromisoformat(e["effective_start"]) <= date.fromisoformat(e["effective_end"]),
                "invalid_mapping_period",
            )
            require(
                urlparse(e["source_url"]).hostname
                in {"kind.krx.co.kr", "opendart.fss.or.kr", "data.krx.co.kr"},
                "unofficial_mapping",
            )
            require(bool(e["security_id"]) and e["identity_reviewed"] is True, "mapping_unverified")
        require(len({e["ticker"] for e in ordered}) == 1, "mixed_ticker")
        require(ordered[0]["effective_start"] <= start.isoformat(), "mapping_start_missing")
        require(ordered[-1]["effective_end"] >= end.isoformat(), "mapping_end_missing")
        for a, b in zip(ordered, ordered[1:], strict=False):
            require(
                (
                    date.fromisoformat(b["effective_start"])
                    - date.fromisoformat(a["effective_end"])
                ).days
                == 1,
                "ambiguous_mapping",
            )
        return "VERIFIED"
    except (KeyError, ValueError, TypeError):
        return "BLOCKED_ON_IDENTITY_MAPPING"


def action_status(
    *,
    mapped: bool,
    complete: bool,
    credential_required: bool,
    credential_present: bool,
    events: tuple[str, ...],
) -> str:
    if not mapped:
        return "BLOCKED_ON_IDENTITY_MAPPING"
    if credential_required and not credential_present:
        return "BLOCKED_ON_CREDENTIAL"
    if not complete:
        return "BLOCKED_ON_SOURCE_COVERAGE"
    return "VERIFIED_EVENT_PRESENT" if events else "VERIFIED_NO_RELEVANT_EVENT"
