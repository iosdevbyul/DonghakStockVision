"""Final corporate-action coverage contract tests; no network or credentials."""

from datetime import date

import pytest

from donghak_stock_vision.backtest.corporate_action_coverage import (
    CorporateActionEventEvidence,
    CorporateActionSecurityCoverage,
    summarize_corporate_action_coverage,
)
from donghak_stock_vision.backtest.dart_evidence import EVENT_CLASSES
from donghak_stock_vision.backtest.data import FrozenJSON


def source(name: str) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "url": "https://kind.krx.co.kr/example",
            "raw_sha256": (name[0] if name else "a") * 64,
            "document_reference": name,
        }
    )


def coverage(
    ticker: str = "005930",
    *,
    historical_identity_verified: bool = True,
    covered_classes: tuple[str, ...] = tuple(sorted(EVENT_CLASSES)),
    authoritative_complete_semantics: bool = True,
    events: tuple[CorporateActionEventEvidence, ...] = (),
) -> CorporateActionSecurityCoverage:
    return CorporateActionSecurityCoverage(
        ticker=ticker,
        security_identity="KR7005930003",
        identity_source=source("identity"),
        identity_effective_start=date(2024, 6, 14),
        identity_effective_end=date(2025, 7, 7),
        historical_identity_verified=historical_identity_verified,
        query_start=date(2024, 6, 14),
        query_end=date(2025, 7, 7),
        covered_classes=covered_classes,
        authoritative_complete_semantics=authoritative_complete_semantics,
        query_sources=(source("query"),),
        events=events,
        review_reference="review-v1",
    )


def test_no_event_security_requires_complete_semantics() -> None:
    assert coverage().status == "VERIFIED_NO_RELEVANT_EVENT"
    assert (
        coverage(authoritative_complete_semantics=False).status
        == "BLOCKED_ON_SOURCE_COVERAGE"
    )
    assert coverage(covered_classes=()).status == "BLOCKED_ON_SOURCE_COVERAGE"
    assert (
        coverage(historical_identity_verified=False).status
        == "BLOCKED_ON_IDENTITY_MAPPING"
    )


def test_event_security_requires_reviewed_effective_event() -> None:
    event = CorporateActionEventEvidence(
        ticker="005930",
        event_class="split",
        effective_date=date(2024, 8, 1),
        source=source("event"),
        review_reference="event-review-v1",
        document_reference="20240701000001",
    )
    record = coverage(events=(event,))
    assert record.status == "VERIFIED_EVENT_PRESENT"
    assert record.to_dict()["events"][0]["effective_date"] == "2024-08-01"


def test_identity_claim_must_cover_full_required_period() -> None:
    with pytest.raises(ValueError, match="historical_identity_period_incomplete"):
        CorporateActionSecurityCoverage(
            ticker="005930",
            security_identity="KR7005930003",
            identity_source=source("identity"),
            identity_effective_start=date(2024, 7, 1),
            identity_effective_end=date(2025, 7, 7),
            historical_identity_verified=True,
            query_start=date(2024, 6, 14),
            query_end=date(2025, 7, 7),
            covered_classes=tuple(sorted(EVENT_CLASSES)),
            authoritative_complete_semantics=True,
            query_sources=(source("query"),),
            events=(),
            review_reference="review-v1",
        )


def test_event_cannot_cross_security_or_query_period() -> None:
    wrong_ticker = CorporateActionEventEvidence(
        ticker="000660",
        event_class="merger",
        effective_date=date(2024, 8, 1),
        source=source("event"),
        review_reference="event-review-v1",
        document_reference="20240701000001",
    )
    with pytest.raises(ValueError, match="event_security_mismatch"):
        coverage(events=(wrong_ticker,))

    outside = CorporateActionEventEvidence(
        ticker="005930",
        event_class="merger",
        effective_date=date(2025, 7, 8),
        source=source("event"),
        review_reference="event-review-v1",
        document_reference="20250708000001",
    )
    with pytest.raises(ValueError, match="event_outside_query_period"):
        coverage(events=(outside,))


def test_manifest_remains_blocked_until_every_security_is_verified() -> None:
    record = coverage()
    manifest = summarize_corporate_action_coverage(("000660", "005930"), (record,)).to_dict()

    assert manifest["complete_corporate_coverage_count"] == 1
    assert manifest["historical_identity_verified_count"] == 1
    assert manifest["missing_security_count"] == 1
    assert manifest["blocked_security_count"] == 1
    assert manifest["missing_securities"] == ["000660"]
    assert manifest["blockers"] == [
        "BLOCKED_ON_HISTORICAL_IDENTITY",
        "BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
    ]
    assert manifest["ready_for_runner_tail_integration"] is False


def test_manifest_closes_only_when_full_universe_is_verified() -> None:
    first = coverage("000660")
    second = coverage("005930")
    manifest = summarize_corporate_action_coverage(
        ("000660", "005930"),
        (second, first),
    ).to_dict()

    assert manifest["complete_corporate_coverage_count"] == 2
    assert manifest["historical_identity_verified_count"] == 2
    assert manifest["missing_security_count"] == 0
    assert manifest["blocked_security_count"] == 0
    assert manifest["blockers"] == []
    assert manifest["ready_for_runner_tail_integration"] is True
    assert manifest["prices_adjusted"] is False
    assert manifest["prediction_or_backtest_invoked"] is False


def test_duplicate_security_coverage_is_rejected() -> None:
    record = coverage()
    with pytest.raises(ValueError, match="duplicate_security_coverage"):
        summarize_corporate_action_coverage(("005930",), (record, record))
