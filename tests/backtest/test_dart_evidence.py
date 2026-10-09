"""Offline evidence fixtures; no credentials, real models or network calls."""

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from donghak_stock_vision.backtest.dart_evidence import (
    EVENT_CLASSES,
    corporate_status,
    corporation_mapping,
    cross_reference,
    document_candidate,
    filing_pages,
    reconcile_missing,
)
from donghak_stock_vision.backtest.dart_transport import (
    DartEvidenceClient,
    request_identity,
    safe_body,
    zip_response_valid,
)

SOURCE = dict(
    url="https://kind.krx.co.kr/external/fixture.htm",
    raw_sha256="a" * 64,
    document_reference="fixture",
)
PARAMS = dict(corp_code="00100001", bgn_de="20240614", end_de="20250707")
ROW = dict(
    corp_code="00100001",
    stock_code="005930",
    rcept_no="20240701000001",
    rcept_dt="20240701",
    report_nm="fixture",
)


def archive(rows: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("CORPCODE.xml", "<result>" + rows + "</result>")
    return output.getvalue()


def test_exact_mapping_is_not_historical() -> None:
    raw = archive(
        "<list><stock_code>005930</stock_code><corp_code>00100001</corp_code>"
        "<corp_name>Fixture</corp_name><modify_date>20261008</modify_date></list>"
    )
    rows = corporation_mapping(raw, ("005930", "000001")).to_dict()["securities"]
    assert rows[0]["current_status"] == "BLOCKED_ON_IDENTITY_MAPPING"
    assert rows[1]["current_status"] == "CURRENT_IDENTITY_VERIFIED"
    assert rows[1]["historical_status"] == "BLOCKED_ON_HISTORICAL_IDENTITY"
    assert rows[1]["effective_start"] is None


def test_duplicate_mapping_fails_closed() -> None:
    row = (
        "<list><stock_code>005930</stock_code><corp_code>00100001</corp_code>"
        "<corp_name>X</corp_name></list>"
    )
    result = corporation_mapping(archive(row + row), ("005930",)).to_dict()
    assert result["securities"][0]["current_status"] == "BLOCKED_ON_IDENTITY_MAPPING"


def page(
    n: int = 1, total: int = 1, count: int = 1, rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return dict(
        status="000",
        page_no=n,
        total_page=total,
        total_count=count,
        list=[ROW] if rows is None else rows,
    )


def test_complete_pages() -> None:
    other = {**ROW, "rcept_no": "20240701000002"}
    result = filing_pages((page(1, 2, 2), page(2, 2, 2, [other])), PARAMS).to_dict()
    assert result["status"] == "COMPLETE_FILING_QUERY"
    assert not result["event_coverage_verified"]


@pytest.mark.parametrize(
    "pages",
    [
        (),
        (page(1, 2, 2),),
        ({"status": "020"},),
        (page(2),),
        (page(count=2),),
        (page(1, 2, 2), page(2, 2, 2)),
    ],
)
def test_bad_pagination_blocked(pages: tuple[dict[str, Any], ...]) -> None:
    assert filing_pages(pages, PARAMS).to_dict()["status"] == "BLOCKED"


def test_empty_is_only_empty_filing_query() -> None:
    result = filing_pages(({"status": "013"},), PARAMS).to_dict()
    assert result["status"] == "COMPLETE_FILING_QUERY"
    assert result["empty_means"] == "no_matching_filings_only"
    assert not result["event_coverage_verified"]


@pytest.mark.parametrize("change", [{"corp_code": "00999999"}, {"rcept_dt": "20250708"}])
def test_wrong_identity_or_period(change: dict[str, str]) -> None:
    assert filing_pages((page(rows=[{**ROW, **change}]),), PARAMS).to_dict()["status"] == "BLOCKED"


def test_title_never_proves_effective_date() -> None:
    event = document_candidate({**ROW, "report_nm": "\uc8fc\uc2dd\ubd84\ud560"})
    assert event is not None
    assert event["filing_date"] == "20240701"
    assert event["effective_date"] is None
    assert not event["prices_adjusted"]


def coverage(**changes: Any) -> str:
    args: dict[str, Any] = dict(
        historical_identity=True,
        query_complete=True,
        covered_classes=EVENT_CLASSES,
        start="2024-06-14",
        end="2025-07-07",
        authoritative_complete_semantics=True,
        events=(),
    )
    return corporate_status(**(args | changes))


def test_no_event_requires_all_dimensions() -> None:
    assert coverage() == "VERIFIED_NO_RELEVANT_EVENT"
    assert coverage(historical_identity=False) == "BLOCKED_ON_IDENTITY_MAPPING"
    cases: list[dict[str, Any]] = [
        dict(query_complete=False),
        dict(covered_classes=()),
        dict(start="2024-07-01"),
        dict(end="2025-06-30"),
        dict(authoritative_complete_semantics=False),
    ]
    for change in cases:
        assert coverage(**change) == "BLOCKED_ON_SOURCE_COVERAGE"
    assert coverage(events=({"effective_date": None},)) == "BLOCKED_ON_EFFECTIVE_DATE"
    assert (
        coverage(events=({"effective_date": "2024-07-01", "source": SOURCE},))
        == "VERIFIED_EVENT_PRESENT"
    )


@pytest.mark.parametrize(
    "reason", ["NOT_YET_LISTED", "DELISTED", "TRADING_SUSPENDED", "SECURITY_IDENTITY_TRANSITION"]
)
def test_reviewed_lifecycle_explains_only_exact_interval(reason: str) -> None:
    interval = dict(
        ticker="005930",
        start="2024-06-14",
        end="2024-06-14",
        reason=reason,
        historical_security_identity_verified=True,
        source=SOURCE,
        source_excerpt="Fixture interval",
        review_reference="review-v1",
    )
    value = reconcile_missing("005930", ("2024-06-14", "2024-06-17"), (interval,)).to_dict()
    assert len(value["explained"]) == 1
    assert value["unexplained"] == ["2024-06-17"]
    assert value["status"] == "BLOCKED"
    interval["source"] = {}
    assert not reconcile_missing("005930", ("2024-06-14",), (interval,)).to_dict()["explained"]


def test_absence_itself_is_not_suspension() -> None:
    assert reconcile_missing("005930", ("2024-06-14",), ()).to_dict()["unexplained"] == [
        "2024-06-14"
    ]


def test_cross_reference_requires_source_link_and_identity() -> None:
    kind = dict(
        dart_rcept_no=ROW["rcept_no"],
        corp_code=ROW["corp_code"],
        ticker="005930",
        link_source=SOURCE,
    )
    assert cross_reference(kind, ROW) == "MATCHED_FILING_REFERENCE_ONLY"
    assert cross_reference({**kind, "corp_code": "other"}, ROW) == "IDENTITY_CONFLICT"
    assert cross_reference({**kind, "ticker": "other"}, ROW) == "SECURITY_CONFLICT"
    assert cross_reference({**kind, "link_source": {}}, ROW) == "UNVERIFIED_REFERENCE_LINK"
    assert cross_reference({"disclosure_id": ROW["rcept_no"]}, ROW) == "UNRESOLVED_REFERENCE"


def test_fingerprint_determinism() -> None:
    a = filing_pages((page(),), PARAMS)
    assert a.identifier == filing_pages((page(),), dict(reversed(list(PARAMS.items())))).identifier
    assert a.identifier != filing_pages((page(),), {**PARAMS, "end_de": "20250708"}).identifier


def test_secret_and_path_cannot_enter_request_identity() -> None:
    with pytest.raises(ValueError):
        request_identity("list.json", {"crtfc_key": "sensitive"})
    with pytest.raises(ValueError):
        request_identity("list.json", {"local_path": "/tmp/example"})
    assert not safe_body(b"echo-sensitive", "sensitive")
    assert not safe_body(archive("sensitive"), "sensitive")


def test_zip_response_validation_rejects_http_200_xml_error() -> None:
    assert zip_response_valid("document.xml", archive("<list/>"))
    assert not zip_response_valid(
        "document.xml",
        b'<?xml version="1.0" encoding="UTF-8"?><result><status>800</status></result>',
    )
    assert zip_response_valid("list.json", b'{"status":"000"}')


def test_document_transport_rejects_http_200_xml_error_without_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("donghak_stock_vision.backtest.dart_transport.time.sleep", lambda _: None)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'<?xml version="1.0" encoding="UTF-8"?><result><status>800</status></result>',
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = DartEvidenceClient("test-only-sensitive-sentinel", tmp_path, http)
        with pytest.raises(ValueError, match="DART_OFFICIAL_ERROR"):
            client.fetch("document.xml", {"rcept_no": "20240701000001"})

    assert not tuple(tmp_path.glob("*.json"))
    assert not tuple(tmp_path.glob("*.gz"))


def test_missing_credential(tmp_path: Path) -> None:
    with httpx.Client() as client, pytest.raises(ValueError, match="BLOCKED_ON_DART_CREDENTIAL"):
        DartEvidenceClient(None, tmp_path, client)


def test_transport_consumes_pages_and_never_preserves_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("donghak_stock_vision.backtest.dart_transport.time.sleep", lambda _: None)
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params["page_no"])
        seen.append(n)
        return httpx.Response(200, json=page(n, 2, 2, [{**ROW, "rcept_no": f"2024070100000{n}"}]))

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = DartEvidenceClient("test-only-sensitive-sentinel", tmp_path, http)
        assert len(client.filings(PARAMS)) == 2
        assert len(client.filings(PARAMS)) == 2
    assert seen == [1, 2]
    for path in tmp_path.glob("*.json"):
        assert "test-only-sensitive-sentinel" not in path.read_text()
        assert "crtfc_key" not in path.read_text()


def test_sensitive_transport_exception_is_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("donghak_stock_vision.backtest.dart_transport.time.sleep", lambda _: None)

    def handle(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("sensitive-transport-text")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = DartEvidenceClient("test-only-sensitive-sentinel", tmp_path, http)
        with pytest.raises(ValueError, match="DART_TRANSPORT_BLOCKED") as err:
            client.fetch("list.json", PARAMS)
        assert "sensitive-transport-text" not in str(err.value)
    assert all("sensitive-transport-text" not in p.read_text() for p in tmp_path.glob("*.json"))


def test_semantic_metadata_excludes_clock_and_paths() -> None:
    from donghak_stock_vision.backtest.verify_dart import semantic_metadata
    from donghak_stock_vision.data.learning import digest

    a = dict(raw_sha256="a" * 64, path="/one", retrieved_at="yesterday")
    b = dict(raw_sha256="a" * 64, path="/two", retrieved_at="today")
    assert digest(semantic_metadata(a)) == digest(semantic_metadata(b))
    b["raw_sha256"] = "b" * 64
    assert digest(semantic_metadata(a)) != digest(semantic_metadata(b))
    with pytest.raises(ValueError, match="secret_field"):
        semantic_metadata({"nested": {"crtfc_key": "sentinel"}})


def test_lifecycle_conflict_remains_blocked() -> None:
    item = dict(
        ticker="005930",
        start="2024-06-14",
        end="2024-06-14",
        reason="DELISTED",
        historical_security_identity_verified=True,
        source=SOURCE,
        source_excerpt="fixture",
        review_reference="fixture",
    )
    result = reconcile_missing("005930", ("2024-06-14",), (item, item)).to_dict()
    assert result["status"] == "BLOCKED"
    assert not result["explained"]
    assert result["conflicting"] == ["2024-06-14"]


def test_bad_response_and_quota_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("donghak_stock_vision.backtest.dart_transport.time.sleep", lambda _: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"status": "020"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = DartEvidenceClient("test-only-sensitive-sentinel", tmp_path, http)
        with pytest.raises(ValueError, match="DART_OFFICIAL_ERROR"):
            client.filings(PARAMS)
    assert len(calls) == 1


def test_transport_does_not_log_nested_library_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setattr("donghak_stock_vision.backtest.dart_transport.time.sleep", lambda _: None)
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpcore.http11").error("test-only-sensitive-sentinel")
        return httpx.Response(200, json={"status": "013"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        DartEvidenceClient("test-only-sensitive-sentinel", tmp_path, http).filings(PARAMS)
    assert "test-only-sensitive-sentinel" not in caplog.text


def test_evidence_modules_do_not_invoke_execution() -> None:
    import ast
    import inspect

    from donghak_stock_vision.backtest import dart_evidence, verify_dart

    banned = {"fit", "predict", "predict_proba", "decide", "run_backtest", "research"}
    for module in (dart_evidence, verify_dart):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in banned


def test_source_inventory_independent_of_location(tmp_path: Path) -> None:
    from donghak_stock_vision.backtest.verify_dart import source_inventory
    from donghak_stock_vision.data.learning import digest

    for name in ("one", "two"):
        (tmp_path / name / "raw").mkdir(parents=True)
        (tmp_path / name / "support").mkdir()
        (tmp_path / name / "raw" / (name + ".json")).write_text(
            json.dumps(dict(content="same", path=name))
        )
    assert digest(source_inventory(tmp_path / "one")) == digest(source_inventory(tmp_path / "two"))
