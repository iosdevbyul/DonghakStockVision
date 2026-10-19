"""Bounded read-only comparisons using original synthetic execution/publication."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.historical_portfolio_models import PortfolioComparison
from donghak_stock_vision.api.historical_portfolios import (
    INDEX,
    LocalFiles,
    PortfolioPublicationReader,
    encoded,
    revoke,
    sha,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.portfolio import FiveTickerRequest
from tests.api.portfolio_demo import prepare
from tests.api.test_historical_portfolios import client, register
from tests.api.test_portfolio_workflow import fingerprint
from tests.backtest.portfolio_helpers import mechanics_run, prepared

BASE = "/api/v1/research/historical-portfolios"
URL = BASE + "/compare"
Publication = tuple[Path, tuple[str, ...], str, dict[str, str]]


@pytest.fixture(scope="module")
def publications(tmp_path_factory: pytest.TempPathFactory) -> Publication:
    work = tmp_path_factory.mktemp("comparison")
    root = work / "publications"
    ids = []
    pin = None
    for family in ("mechanics", "linear", "hgb"):
        demo = work / family
        prepare(demo, family)
        source = FrozenJSON((demo / "publication" / "result.json").read_text())
        report_id, pin = register(root, source, pin)
        ids.append(report_id)
    fixture = work / "unavailable-sources"
    request, _ = prepared(fixture, controlled=True, omit_last=True)
    for scenario in ("stale", "pending"):
        d = request.document.to_dict()
        d["initial_cash"] = "1500"
        d["config"]["decision_dates"] = d["config"]["decision_dates"][:1]
        if scenario == "pending":
            d["end"] = d["config"]["decision_dates"][0]
        else:
            d["config"]["valuation_policy"]["max_age_seconds"] = 1
            d["config"]["admission_policy"]["ordered_tickers"] = list(reversed(d["tickers"]))
        source = mechanics_run(
            fixture, FiveTickerRequest(FrozenJSON.freeze(d)), output_name=scenario + ".db"
        )
        report_id, pin = register(root, source, pin)
        ids.append(report_id)
    assert len(set(ids)) == 5 and pin is not None
    return root, tuple(ids), pin, fingerprint(work)


@pytest.fixture
def isolated(tmp_path: Path, publications: Publication) -> Publication:
    root, ids, pin, _ = publications
    destination = tmp_path / "publications"
    shutil.copytree(root, destination)
    return destination, ids, pin, fingerprint(destination)


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_comparison_exact_summaries_and_request_order(
    publications: Publication, count: int
) -> None:
    root, ids, pin, _ = publications
    http = client(PortfolioPublicationReader(root, pin))
    selected = list(reversed(ids[:count]))
    response = http.post(URL, json={"reportIds": selected})
    assert response.status_code == 200, response.text
    result = response.json()
    PortfolioComparison.model_validate(result)
    assert [item["reportId"] for item in result["items"]] == selected
    assert response.content == http.post(URL, json={"reportIds": selected}).content
    for item in result["items"]:
        original = http.get(BASE + "/" + item["reportId"]).json()
        assert {k: item[k] for k in original} == original
        assert item["tickerCount"] == len(original["tickers"]) == 5
        assert item["archiveVerification"] == "integrity_verified"
        assert item["reportVerification"] == "offline_authoritative_replay_passed"
    assert result["researchOnly"] and not result["executable"]
    assert result["comparability"]["valuationPolicyComparison"] == "unavailable"
    assert "synthetic_results_not_real_market_performance" in result["comparability"]["notices"]


def test_actual_mechanics_inference_zero_orders_and_unavailable(publications: Publication) -> None:
    root, ids, pin, _ = publications
    result = client(PortfolioPublicationReader(root, pin)).post(URL, json={"reportIds": ids}).json()
    mechanics, linear, hgb, stale, pending = result["items"]
    assert mechanics["acceptedOrderCount"] == mechanics["fillCount"] == 1
    assert mechanics["signalEvidenceScopes"] == ["controlled_mechanics_test_double"]
    for item, family in ((linear, "linear"), (hgb, "hgb")):
        assert item["modelFamily"] == family
        assert item["signalEvidenceScopes"] == ["public_model_inference"]
        assert item["acceptedOrderCount"] == item["fillCount"] == 0
        assert item["dataOrigin"] == "synthetic"
    assert stale["finalPortfolioEquity"] is None and not stale["portfolioValuationAvailable"]
    assert stale["unavailable"]["finalPortfolioEquity"] == "valuation_evidence_unavailable"
    assert stale["finalValuation"]["positions"][0]["reason"] == "stale_mark"
    assert pending["fillCount"] == 0 and pending["pendingOrderCount"] == 1
    differences = result["comparability"]
    assert differences["periodsDiffer"] is True
    assert differences["valuationTimestampsDiffer"] is True
    assert differences["observedMarkFieldsDiffer"] is None
    assert "analysis_periods_differ" in differences["notices"]
    assert "valuation_observation_times_differ" in differences["notices"]
    assert "valuation_evidence_unavailable" in differences["notices"]


@pytest.mark.parametrize(
    "selection",
    [
        [],
        ["a" * 64],
        [str(i) * 64 for i in range(6)],
        ["a" * 64] * 2,
        ["", "a" * 64],
        ["/tmp/input.json", "a" * 64],
        ["../input.json", "a" * 64],
        ["A" * 64, "b" * 64],
        [123, "a" * 64],
        [None, "a" * 64],
        "a" * 64,
    ],
)
def test_invalid_selection(publications: Publication, selection: Any) -> None:
    root, _, pin, _ = publications
    response = client(PortfolioPublicationReader(root, pin)).post(
        URL, json={"reportIds": selection}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_comparison_request"}}


@pytest.mark.parametrize("body", [b"{", b"null", b"[]", b"{}", b'{"reportIds":[],"root":"/tmp"}'])
def test_invalid_body(publications: Publication, body: bytes) -> None:
    root, _, pin, _ = publications
    response = client(PortfolioPublicationReader(root, pin)).post(URL, content=body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_comparison_request"}}


def test_unknown_unregistered_report(isolated: Publication) -> None:
    root, ids, pin, _ = isolated
    manifest = json.loads((root / f"{ids[0]}.manifest.json").read_text())
    raw = (root / f"{ids[0]}.archive.json").read_bytes() + b"\n"
    (root / "unregistered.json").write_bytes(raw)
    binding = {
        k: manifest[k] for k in ("archiveHash", "projectionHash", "resultHash", "reportHash")
    }
    binding["archiveHash"] = sha(raw)
    unknown = sha(encoded(binding))
    http = client(PortfolioPublicationReader(root, pin))
    for report_id in (unknown, "f" * 64):
        response = http.post(URL, json={"reportIds": [ids[0], report_id]})
        assert response.status_code == 404
        assert response.json() == {"detail": {"code": "report_not_found"}}
        assert "items" not in response.json()


def test_revocation_and_stale_pin(isolated: Publication) -> None:
    root, ids, pin, _ = isolated
    old = client(PortfolioPublicationReader(root, pin))
    selection = {"reportIds": ids[:2]}
    assert old.post(URL, json=selection).status_code == 200
    updated = revoke(root, ids[1], expected_index_hash=pin)
    assert old.post(URL, json=selection).json() == {
        "detail": {"code": "publication_manifest_mismatch"}
    }
    response = client(PortfolioPublicationReader(root, updated)).post(URL, json=selection)
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "report_unpublished"}}


@pytest.mark.parametrize(
    "damage,code",
    [("archive", "archive_integrity_mismatch"), ("projection", "archive_unavailable")],
)
def test_corrupt_or_missing_selected_evidence(
    isolated: Publication, damage: str, code: str
) -> None:
    root, ids, pin, _ = isolated
    http = client(PortfolioPublicationReader(root, pin))
    assert http.post(URL, json={"reportIds": ids[:2]}).status_code == 200
    path = root / f"{ids[1]}.{damage}.json"
    if damage == "archive":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        path.unlink()
    response = http.post(URL, json={"reportIds": ids[:2]})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": code}}
    assert str(root) not in response.text


def test_no_replay_inference_or_writes(
    publications: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ids, pin, before = publications

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("comparison must not replay, load or infer")

    monkeypatch.setattr(
        "donghak_stock_vision.backtest.receipt_validation.validate_result", forbidden
    )
    monkeypatch.setattr(
        "donghak_stock_vision.backtest.receipt_validation.replay_five_ticker", forbidden
    )
    monkeypatch.setattr("donghak_stock_vision.backtest.runner.BacktestRunner.run", forbidden)
    monkeypatch.setattr("donghak_stock_vision.models.service.load_model", forbidden)
    monkeypatch.setattr("donghak_stock_vision.signals.service.SignalService.research", forbidden)
    http = client(PortfolioPublicationReader(root, pin))
    for _ in range(2):
        assert http.post(URL, json={"reportIds": ids}).status_code == 200
    assert fingerprint(root.parent) == before


@pytest.mark.parametrize("guard", ["client", "host", "origin", "forwarded"])
def test_local_guards(publications: Publication, guard: str) -> None:
    root, ids, pin, _ = publications
    app = create_app(portfolio_reader=PortfolioPublicationReader(root, pin))
    headers = (
        {"Host": "evil.example"}
        if guard == "host"
        else {"Origin": "https://evil.example"}
        if guard == "origin"
        else {"X-Forwarded-For": "127.0.0.1"}
        if guard == "forwarded"
        else {}
    )
    remote = guard in {"client", "forwarded"}
    http = TestClient(
        app, base_url="http://127.0.0.1", client=("192.0.2.1" if remote else "127.0.0.1", 123)
    )
    assert http.post(URL, json={"reportIds": ids[:2]}, headers=headers).status_code == 403


def test_resource_bounds_and_disabled(
    publications: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ids, pin, _ = publications
    http = client(PortfolioPublicationReader(root, pin))
    assert client().post(URL, json={"reportIds": ids[:2]}).status_code == 503
    response = http.post(URL, content=iter([b" " * 2048, b" " * 2049]))
    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "oversized_request"}}
    monkeypatch.setattr("donghak_stock_vision.api.historical_portfolios.RESPONSE_BYTES", 100)
    assert http.post(URL, json={"reportIds": ids[:2]}).status_code == 413


def test_index_update_during_comparison_fails_closed(
    isolated: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ids, pin, _ = isolated
    reader = PortfolioPublicationReader(root, pin)
    load = reader._load

    def changed(report_id: str, index: dict[str, Any]) -> dict[str, Any]:
        value = load(report_id, index)
        if report_id == ids[0]:
            revoke(root, ids[1], expected_index_hash=pin)
        return value

    monkeypatch.setattr(reader, "_load", changed)
    response = client(reader).post(URL, json={"reportIds": ids[:2]})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "publication_manifest_mismatch"}}


def test_openapi_existing_get_and_new_post(publications: Publication) -> None:
    root, _, pin, _ = publications
    schema = client(PortfolioPublicationReader(root, pin)).get("/openapi.json").json()
    assert "get" in schema["paths"][BASE + "/{report_id}"]
    post = schema["paths"][URL]["post"]
    body = post["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["reportIds"]["minItems"] == 2
    assert body["properties"]["reportIds"]["maxItems"] == 5


@pytest.mark.parametrize("field", ["root", "archivePath"])
def test_storage_selectors_forbidden(tmp_path: Path, field: str) -> None:
    root = tmp_path
    raw = encoded({"version": INDEX, "entries": []})
    LocalFiles(root).write("index.json", raw)
    pin, ids = sha(raw), ["a" * 64, "b" * 64]
    response = client(PortfolioPublicationReader(root, pin)).post(
        URL, json={"reportIds": ids[:2], field: "/tmp/other"}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_comparison_request"}}


def test_projection_basis_differences_and_unknowns() -> None:
    # Projection-only shape coverage. These edited views are never published evidence.
    import copy

    from donghak_stock_vision.api.historical_portfolio_comparison import compare_views

    first: dict[str, Any] = {
        "summary": {
            "finalValuation": {
                "timestamp": "2020-01-02T15:00:00+00:00",
                "stage": "account",
                "positions": [{"mark": {"field": "open"}}],
            },
            "unavailable": {},
            "tickers": ["000075"],
            "period": {"start": "2020-01-01", "end": "2020-01-02"},
            "portfolioValuationAvailable": True,
            "dataOrigin": "synthetic",
        },
        "trades": [{"evidence": {"signal_evidence_scope": "controlled_mechanics_test_double"}}],
    }
    other = copy.deepcopy(first)
    other["summary"]["finalValuation"]["positions"][0]["mark"]["field"] = "close"
    result = compare_views([first, other])
    assert result["comparability"]["observedMarkFieldsDiffer"] is True
    assert "observed_valuation_mark_fields_differ" in result["comparability"]["notices"]
    assert result["comparability"]["periodsDiffer"] is False
    assert result["comparability"]["valuationTimestampsDiffer"] is False
    other["summary"]["finalValuation"].pop("timestamp")
    other["summary"]["finalValuation"].pop("stage")
    other["trades"] = []
    unknown = compare_views([first, other])
    assert unknown["comparability"]["valuationTimestampsDiffer"] is None
    assert unknown["items"][1]["valuationBasis"]["timestamp"] is None
    assert unknown["items"][1]["valuationBasis"]["stage"] is None
    assert (
        unknown["items"][1]["valuationBasis"]["unavailable"]["timestamp"]
        == "valuation_source_field_unavailable"
    )
    assert unknown["items"][1]["signalEvidenceScopes"] == []
    assert (
        unknown["items"][1]["unavailable"]["signalEvidenceScopes"]
        == "signal_evidence_scope_not_recorded"
    )


def test_existing_cors_post_preflight_remains_blocked() -> None:
    response = client().options(
        URL,
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 400
    assert response.headers["access-control-allow-methods"] == "GET"
