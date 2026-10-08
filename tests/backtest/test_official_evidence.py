"""Synthetic parser checks and offline replay of preserved official evidence."""

import ast
import calendar as civil
import gzip
import hashlib
import inspect
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest import official_evidence, verify_official
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.evidence_package import AnchorRange, derive_boundaries
from donghak_stock_vision.backtest.official_evidence import (
    KIND_URL,
    action_status,
    extract_action_rows,
    mapping_status,
    merge_months,
    normalize_month,
)
from donghak_stock_vision.backtest.run_evidence import calendar_check, settlement_check
from donghak_stock_vision.backtest.validation import ContractError

H = "a" * 64


def month(year: int = 2025, number: int = 6) -> tuple[bytes, dict[str, Any]]:
    rows = []
    for day in range(1, civil.monthrange(year, number)[1] + 1):
        wd = date(year, number, day).weekday()
        css = "sun" if wd == 6 else "sat" if wd == 5 else ""
        # Use an actual recognized source label only as a synthetic parser fixture.
        label = "\u00a0\ud604\ucda9\uc77c" if (number, day) == (6, 6) else ""
        rows.append(f'<td class="{css}">{day}{label}</td>')
    raw = ('<div id="calBig"><table><tr>' + "".join(rows) + "</tr></table></div>").encode()
    return raw, dict(
        url=KIND_URL,
        status=200,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        params=dict(
            method="searchTraidingCalendar",
            ldMktTpCd="1",
            selYear=str(year),
            selMonth=f"{number:02d}",
        ),
    )


def rule() -> FrozenJSON:
    return FrozenJSON.freeze(dict(url="https://global.krx.co.kr/official-fixture", raw_sha256=H))


def test_official_month_and_holiday() -> None:
    raw, source = month()
    result = normalize_month(raw, source).to_dict()
    assert len(result["days"]) == 30
    assert result["days"]["2025-06-06"] == "holiday"
    assert result["days"]["2025-06-07"] == "weekend"
    assert result["days"]["2025-06-09"] == "session"


@pytest.mark.parametrize(
    "defect", ["url", "scope", "hash", "http", "method", "coverage", "annotation"]
)
def test_calendar_fail_closed(defect: str) -> None:
    raw, source = month()
    if defect == "url":
        source["url"] = "https://example.test/calendar"
    if defect == "scope":
        source["params"]["ldMktTpCd"] = "2"
    if defect == "hash":
        source["raw_sha256"] = H
    if defect == "http":
        source["status"] = 500
    if defect == "method":
        source["params"]["method"] = "other"
    if defect == "coverage":
        raw = raw.replace(b'<td class="">30</td>', b"")
    if defect == "annotation":
        raw = raw.replace(b">30</td>", b">30 unknown</td>")
    if defect in {"coverage", "annotation"}:
        source["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ContractError):
        normalize_month(raw, source)


def test_month_gap_and_overlap() -> None:
    june = normalize_month(*month())
    with pytest.raises(ContractError, match="overlapping"):
        merge_months((june, june), rule())
    august = normalize_month(*month(2025, 8))
    with pytest.raises(ContractError, match="coverage_gap"):
        merge_months((june, august), rule())


def test_preserved_calendar_exact_boundaries_and_exclusion() -> None:
    root = Path(__file__).resolve().parents[2] / "docs/evidence/baseline-official/raw"
    records = json.loads((root / "retrieval.json").read_text())["requests"]
    months = tuple(
        normalize_month(gzip.decompress((root / (s["file"] + ".gz")).read_bytes()), s)
        for s in records
        if s["file"].startswith("calendar-")
    )
    doc = merge_months(months, rule())
    days = doc.to_dict()["days"]
    for d in ("2024-10-01", "2024-12-31", "2025-01-27", "2025-06-03"):
        assert days[d] == "holiday"
    assert days["2025-03-21"] == "session"
    bounds = derive_boundaries(
        AnchorRange(date(2024, 7, 1), date(2025, 6, 30)), (date(2025, 3, 21),), doc, doc.identifier
    ).to_dict()
    assert bounds["warmup_sessions"][0] == "2024-06-14"
    assert bounds["warmup_sessions"][-1] == "2024-06-28"
    assert len(bounds["warmup_sessions"]) == 11
    assert bounds["execution_tail"] == [
        "2025-07-01",
        "2025-07-02",
        "2025-07-03",
        "2025-07-04",
        "2025-07-07",
    ]
    assert bounds["settlement_tail"] == ["2025-07-08", "2025-07-09"]
    assert bounds["tail_generates_signals"] is False
    assert bounds["warmup_generates_signals"] is False
    mapping = bounds["settlement_mapping"]
    assert mapping["2025-06-05"] == "2025-06-10"
    assert mapping["2025-07-07"] == "2025-07-09"
    missing = calendar_check(
        {"005930": []}, date(2025, 6, 2), date(2025, 6, 5), (), doc, doc.identifier
    )
    assert missing.reasons
    unexpected = calendar_check(
        {"005930": ["2025-06-03"]}, date(2025, 6, 2), date(2025, 6, 5), (), doc, doc.identifier
    )
    assert "observed_non_session" in unexpected.reasons
    assert (
        derive_boundaries(
            AnchorRange(date(2024, 7, 1), date(2025, 6, 30)), (), None, None
        ).to_dict()["status"]
        != "VERIFIED"
    )


def entry(**changes: Any) -> FrozenJSON:
    e = dict(
        ticker="005930",
        security_id="official-fixture-id",
        effective_start="2024-01-01",
        effective_end="2025-12-31",
        source_url=KIND_URL,
        source_sha256=H,
        identity_reviewed=True,
    )
    e.update(changes)
    return FrozenJSON.freeze(e)


def test_unique_mapping_and_explicit_identity_change() -> None:
    start, end = date(2024, 7, 1), date(2025, 6, 30)
    assert mapping_status((entry(),), start, end) == "VERIFIED"
    changed = (
        entry(effective_end="2024-12-31"),
        entry(security_id="replacement", effective_start="2025-01-01"),
    )
    assert mapping_status(changed, start, end) == "VERIFIED"
    assert changed[0].identifier != changed[1].identifier


@pytest.mark.parametrize(
    "entries",
    [
        (),
        (entry(), entry()),
        (entry(identity_reviewed=False),),
        (entry(effective_start="2025-01-01"),),
        (entry(source_url="https://example.test"),),
    ],
)
def test_missing_ambiguous_mapping_blocks(entries: tuple[FrozenJSON, ...]) -> None:
    assert (
        mapping_status(entries, date(2024, 7, 1), date(2025, 6, 30))
        == "BLOCKED_ON_IDENTITY_MAPPING"
    )


@pytest.mark.parametrize(
    "mapped,complete,needed,present,events,expected",
    [
        (True, True, False, False, (), "VERIFIED_NO_RELEVANT_EVENT"),
        (True, True, False, False, ("event",), "VERIFIED_EVENT_PRESENT"),
        (True, False, False, False, (), "BLOCKED_ON_SOURCE_COVERAGE"),
        (True, True, True, False, (), "BLOCKED_ON_CREDENTIAL"),
        (False, True, False, False, (), "BLOCKED_ON_IDENTITY_MAPPING"),
    ],
)
def test_explicit_coverage_states(
    mapped: bool,
    complete: bool,
    needed: bool,
    present: bool,
    events: tuple[str, ...],
    expected: str,
) -> None:
    assert (
        action_status(
            mapped=mapped,
            complete=complete,
            credential_required=needed,
            credential_present=present,
            events=events,
        )
        == expected
    )


def test_official_event_does_not_adjust_or_guess_security() -> None:
    raw = b"""<article class="scrarea"><tr><td>2025-06-01</td><td><a
    onclick="companysummary_open('12345')">Name</a><img src="icn_t_yu.gif"></td><td>
    <a onclick="openDisclsViewer('20250601000001','')"></a></td></tr></article>"""
    source = dict(url=KIND_URL, raw_sha256=hashlib.sha256(raw).hexdigest())
    result = extract_action_rows(raw, source)
    row = result.to_dict()["rows"][0]
    assert row["issuer_code"] == "12345" and row["repository_ticker"] is None
    assert result.to_dict()["prices_adjusted"] is False
    assert result.to_dict()["no_event_certified"] is False
    assert result == extract_action_rows(raw, source)


def test_unsupported_settlement_cost_date() -> None:
    doc = merge_months((normalize_month(*month(2030, 6)),), rule())
    assert settlement_check((date(2030, 6, 3),), doc, doc.identifier).reasons


def test_no_prediction_or_backtest_calls() -> None:
    for module in (official_evidence, verify_official):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"fit", "predict", "predict_proba", "load", "run"}


def test_transport_metadata_excluded_and_semantics_hashed() -> None:
    raw, source = month()
    first = normalize_month(raw, source)
    second = normalize_month(
        raw,
        {
            **source,
            "file": "renamed.html",
            "local_path": "/different/root",
            "retrieved_at": "later",
        },
    )
    assert first.identifier == second.identifier
    assert normalize_month(raw, {**source, "publisher": "changed"}).identifier != first.identifier
    july = normalize_month(*month(2025, 7))
    assert merge_months((first, july), rule()) == merge_months((july, first), rule())


def test_empty_query_is_not_no_event_certificate() -> None:
    raw = b'<article class="scrarea"><table><tr><td>No results</td></tr></table></article>'
    source = dict(url=KIND_URL, raw_sha256=hashlib.sha256(raw).hexdigest())
    result = extract_action_rows(raw, source).to_dict()
    assert result["rows"] == []
    assert result["no_event_certified"] is False
    assert result["complete_result_semantics_verified"] is False
