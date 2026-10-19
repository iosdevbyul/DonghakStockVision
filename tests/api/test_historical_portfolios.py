"""Offline synthetic publication, trust boundary and read-only HTTP regressions."""

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.historical_portfolio_models import PortfolioSummary
from donghak_stock_vision.api.historical_portfolio_projection import projection
from donghak_stock_vision.api.historical_portfolios import (
    MAX_LIMIT,
    LocalFiles,
    PortfolioPublicationReader,
    PublicationError,
    encoded,
    publish,
    revoke,
    sha,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.portfolio import FiveTickerRequest
from donghak_stock_vision.backtest.receipt_archive import encode_result
from tests.backtest.portfolio_helpers import TICKERS, execute_fixture, mechanics_run, prepared

BASE = "/api/v1/research/historical-portfolios"


def client(reader: PortfolioPublicationReader | None = None) -> TestClient:
    return TestClient(
        create_app(portfolio_reader=reader),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def register(root: Path, source: FrozenJSON, previous: str | None = None) -> tuple[str, str]:
    root.mkdir(exist_ok=True)
    (root / "input.json").write_text(encode_result(source).document.payload_json)
    return publish(
        root,
        "input.json",
        expected_index_hash=previous,
        expected_result_hash=source.identifier,
        expected_report_hash=source.to_dict()["detailed_report_hash"],
    )


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str, FrozenJSON]:
    work = tmp_path_factory.mktemp("portfolio-publication")
    fixture = work / "sources"
    request, _ = prepared(fixture, controlled=True)
    d = request.document.to_dict()
    # One account can afford one entry, a complete exit, then one reentry.
    d["initial_cash"] = "1500"
    d["config"]["decision_dates"] = d["config"]["decision_dates"][:3]
    source = mechanics_run(fixture, FiveTickerRequest(FrozenJSON.freeze(d)))
    root = work / "publications"
    report_id, pin = register(root, source)
    return root, report_id, pin, source


@pytest.fixture
def isolated(tmp_path: Path, published: tuple[Path, str, str, FrozenJSON]) -> tuple[Path, str, str]:
    root, report_id, pin, _ = published
    destination = tmp_path / "publications"
    shutil.copytree(root, destination)
    return destination, report_id, pin


def test_publication_restart_summary_and_exact_economics(
    published: tuple[Path, str, str, FrozenJSON],
) -> None:
    root, report_id, pin, source = published
    d = source.to_dict()
    reader = PortfolioPublicationReader(root, pin)
    summary = reader.summary(report_id)
    PortfolioSummary.model_validate(summary)
    perf = d["performance"]
    assert summary["fillCount"] == perf["fill_count"] == 3
    assert summary["completedPositionCount"] == perf["completed_trades"] == 1
    for key, original in (
        ("realizedNetPnl", "ledger_realized_pnl"),
        ("unrealizedPnl", "unrealized_pnl"),
        ("portfolioReturn", "total_return"),
        ("finalPortfolioEquity", "final_equity"),
    ):
        assert summary[key] == perf[original]
    assert summary["resultHash"] == source.identifier
    assert summary["reportHash"] == d["detailed_report_hash"]
    assert summary["tickers"] == list(TICKERS)
    assert summary["researchOnly"] is True and summary["executable"] is False
    assert summary["pitVerified"] is summary["oosVerified"] is False
    assert summary["publishedAt"] not in (d["run"]["start_at"], d["run"]["end_at"])
    assert reader.summary(report_id) == PortfolioPublicationReader(root, pin).summary(report_id)
    assert client(reader).get(f"{BASE}/{report_id}").status_code == 200


def test_duplicate_publication_idempotent(published: tuple[Path, str, str, FrozenJSON]) -> None:
    root, report_id, pin, source = published
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert publish(
        root,
        "input.json",
        expected_index_hash=pin,
        expected_result_hash=source.identifier,
        expected_report_hash=source.to_dict()["detailed_report_hash"],
    ) == (report_id, pin)
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_ticker_timeline_positions_reentry_and_source_linkage(
    published: tuple[Path, str, str, FrozenJSON],
) -> None:
    root, report_id, pin, source = published
    reader = PortfolioPublicationReader(root, pin)
    details = reader.ticker(report_id, TICKERS[0])
    assert [x["action"] for x in details["timeline"]] == ["BUY", "SELL", "BUY"]
    assert [x["status"] for x in details["positions"]] == ["confirmed_full_exit", "still_open"]
    original = source.to_dict()["detailed_report"]
    assert details["realizedNetPnl"] == original["realized_pnl_by_ticker"][TICKERS[0]]
    assert (
        details["positions"][1]["previous_completed_position_id"]
        == details["positions"][0]["position_id"]
    )
    assert details["positions"][0]["holding_duration"]["calendar_days"] is not None
    assert details["timeline"][0]["source"] == original["timeline"][0]["source"]
    fill = details["timeline"][0]["executions"][0]
    assert fill["exchange_execution_at"] is None
    assert fill["fill"]["fee_krw"] is not None
    assert client(reader).get(f"{BASE}/{report_id}/tickers/00088K").json()["ticker"] == "00088K"


def test_trade_order_and_stable_pagination(published: tuple[Path, str, str, FrozenJSON]) -> None:
    root, report_id, pin, source = published
    reader = PortfolioPublicationReader(root, pin)
    first, second = (
        reader.trades(report_id, offset=0, limit=2),
        reader.trades(report_id, offset=2, limit=2),
    )
    original = source.to_dict()["detailed_report"]["timeline"]
    assert [x["evidence"]["opportunity_id"] for x in first["items"] + second["items"]] == [
        x["opportunity_id"] for x in original[:4]
    ]
    assert first["items"][0]["classification"] == "filled"
    assert first["items"][1]["classification"] == "rejected"
    assert len({x["eventId"] for x in reader.trades(report_id, limit=20)["items"]}) == 15
    assert reader.trades(report_id, TICKERS[0])["total"] == 3
    assert first == reader.trades(report_id, offset=0, limit=2)


@pytest.mark.parametrize("route", ["", "/{id}", "/{id}/trades", "/{id}/tickers/000075"])
def test_get_never_replays_or_writes(
    published: tuple[Path, str, str, FrozenJSON],
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    root, report_id, pin, _ = published
    before = {p.name: sha(p.read_bytes()) for p in root.iterdir()}
    source_before = {
        p.name: sha(p.read_bytes()) for p in (root.parent / "sources").iterdir() if p.is_file()
    }
    import donghak_stock_vision.backtest.receipt_validation as authority

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("GET must not replay or infer")

    monkeypatch.setattr(authority, "validate_result", forbidden)
    monkeypatch.setattr(authority, "replay_five_ticker", forbidden)
    monkeypatch.setattr("donghak_stock_vision.models.service.load_model", forbidden)
    http = client(PortfolioPublicationReader(root, pin))
    url = BASE + route.format(id=report_id)
    assert http.get(url).status_code == http.get(url).status_code == 200
    assert before == {p.name: sha(p.read_bytes()) for p in root.iterdir()}
    assert source_before == {
        p.name: sha(p.read_bytes()) for p in (root.parent / "sources").iterdir() if p.is_file()
    }


@pytest.mark.parametrize(
    "damage,code",
    [
        ("archive_changed", "archive_integrity_mismatch"),
        ("archive_missing", "archive_unavailable"),
        ("manifest_changed", "publication_manifest_mismatch"),
        ("projection_changed", "publication_manifest_mismatch"),
        ("index_changed", "publication_manifest_mismatch"),
        ("symlink_archive", "archive_unavailable"),
    ],
)
def test_storage_changes_invalidate_warm_reader(
    isolated: tuple[Path, str, str],
    damage: str,
    code: str,
) -> None:
    root, report_id, pin = isolated
    http = client(PortfolioPublicationReader(root, pin))
    assert http.get(f"{BASE}/{report_id}").status_code == 200
    suffix = {
        "archive_changed": "archive.json",
        "archive_missing": "archive.json",
        "manifest_changed": "manifest.json",
        "projection_changed": "projection.json",
        "symlink_archive": "archive.json",
    }.get(damage)
    path = root / (f"{report_id}.{suffix}" if suffix else "index.json")
    if damage == "symlink_archive":
        outside = root.parent / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif damage == "archive_missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b" ")
    response = http.get(f"{BASE}/{report_id}")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": code}}
    assert str(root) not in response.text


def test_unregistered_and_revoked(isolated: tuple[Path, str, str]) -> None:
    root, report_id, pin = isolated
    http = client(PortfolioPublicationReader(root, pin))
    assert http.get(f"{BASE}/{'a' * 64}").json()["detail"]["code"] == "report_not_found"
    assert http.get(BASE).json()["total"] == 1  # input.json itself is never listed
    new_pin = revoke(root, report_id, expected_index_hash=pin)
    assert http.get(BASE).status_code == 503  # old pin fails immediately
    new = client(PortfolioPublicationReader(root, new_pin))
    assert new.get(BASE).json()["items"] == []
    assert new.get(f"{BASE}/{report_id}").json() == {"detail": {"code": "report_unpublished"}}


@pytest.mark.parametrize(
    "offset,limit",
    [("-1", "1"), ("0", "21"), ("0", "0"), ("100001", "1"), ("no", "10"), ("0", "10000000000000")],
)
def test_bad_pagination(
    published: tuple[Path, str, str, FrozenJSON], offset: str, limit: str
) -> None:
    root, _, pin, _ = published
    response = client(PortfolioPublicationReader(root, pin)).get(
        BASE, params={"offset": offset, "limit": limit}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_pagination"}}


def test_exact_unknown_tickers_and_no_mutations(
    published: tuple[Path, str, str, FrozenJSON],
) -> None:
    root, report_id, pin, _ = published
    http = client(PortfolioPublicationReader(root, pin))
    assert http.get(f"{BASE}/{report_id}/tickers/00088k").status_code == 422
    assert http.get(f"{BASE}/{report_id}/tickers/999999").status_code == 404
    assert http.get(f"{BASE}/{report_id}/trades?ticker=999999").status_code == 404
    for method in ("post", "put", "patch", "delete"):
        assert getattr(http, method)(f"{BASE}/{report_id}").status_code == 405


@pytest.mark.parametrize("locator", ["../input.json", "/etc/passwd", "sub/input.json", "..", ""])
def test_locator_traversal_rejected(isolated: tuple[Path, str, str], locator: str) -> None:
    root, _, _ = isolated
    with pytest.raises(PublicationError, match="invalid_storage_locator"):
        LocalFiles(root).read(locator, 100)


def test_root_and_input_symlinks_rejected(isolated: tuple[Path, str, str]) -> None:
    root, _, pin = isolated
    link = root.parent / "linked-root"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(PublicationError, match="archive_unavailable"):
        PortfolioPublicationReader(link, pin)
    (root / "link.json").symlink_to(root / "input.json")
    with pytest.raises(PublicationError, match="archive_unavailable"):
        LocalFiles(root).read("link.json", 100000000)


def test_response_limit_and_pagination_guard(
    isolated: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, report_id, pin = isolated
    import donghak_stock_vision.api.historical_portfolios as module

    monkeypatch.setattr(module, "RESPONSE_BYTES", 100)
    response = client(PortfolioPublicationReader(root, pin)).get(f"{BASE}/{report_id}")
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "oversized_result"
    with pytest.raises(PublicationError, match="invalid_pagination"):
        PortfolioPublicationReader.pagination(0, MAX_LIMIT + 1)


def test_local_boundary_and_disabled(published: tuple[Path, str, str, FrozenJSON]) -> None:
    assert client().get(BASE).status_code == 503
    root, _, pin, _ = published
    app = create_app(portfolio_reader=PortfolioPublicationReader(root, pin))
    remote = TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.1", 50000))
    assert remote.get(BASE).status_code == 403
    http = client(PortfolioPublicationReader(root, pin))
    assert http.get(BASE, headers={"Host": "example.com"}).status_code == 403
    assert http.get(BASE, headers={"Origin": "https://evil.example"}).status_code == 403
    assert http.get(BASE, headers={"Origin": "http://["}).status_code == 403
    large = http.get(BASE, params={"unused": "x" * 4096})
    assert large.status_code == 413 and large.json()["detail"]["code"] == "oversized_request"
    assert remote.get(BASE, headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


@pytest.mark.parametrize("damage", ["result_hash", "report_hash", "provenance", "graph", "version"])
def test_invalid_offline_publication_fails_closed(
    isolated: tuple[Path, str, str],
    published: tuple[Path, str, str, FrozenJSON],
    damage: str,
) -> None:
    root, _, pin = isolated
    source = published[3]
    original = (root / "index.json").read_bytes()
    expected = source.identifier
    report_hash = source.to_dict()["detailed_report_hash"]
    if damage == "result_hash":
        expected = "a" * 64
    elif damage == "report_hash":
        report_hash = "c" * 64
    elif damage == "provenance":
        altered = source.to_dict()
        altered["frozen_input"]["request"]["model_id"] = "b" * 64
        tampered = FrozenJSON.freeze(altered)
        expected = (
            tampered.identifier
        )  # A re-signed transport must still fail authoritative replay.
        (root / "input.json").write_text(encode_result(tampered).document.payload_json)
    else:
        archive = json.loads((root / "input.json").read_text())
        if damage == "graph":
            archive["artifacts"].pop(0)
        else:
            archive["version"] = "unsupported_v99"
        (root / "input.json").write_text(json.dumps(archive))
    with pytest.raises(
        PublicationError, match="result_validation_required|unsupported_archive_version"
    ):
        publish(
            root,
            "input.json",
            expected_index_hash=pin,
            expected_result_hash=expected,
            expected_report_hash=report_hash,
        )
    assert (root / "index.json").read_bytes() == original


def test_missing_pin_never_overwrites_index(isolated: tuple[Path, str, str]) -> None:
    root, _, _ = isolated
    with pytest.raises(PublicationError, match="publication_manifest_mismatch"):
        from donghak_stock_vision.api.historical_portfolios import read_index

        read_index(LocalFiles(root), None)


def test_projection_no_economic_recalculation_missing_valuation(
    published: tuple[Path, str, str, FrozenJSON],
) -> None:
    # Projection-only shape check. Not accepted as new authoritative evidence.
    source = copy.deepcopy(published[3].to_dict())
    source["performance"].update(final_equity=None, unrealized_pnl=None, total_return=None)
    source["performance"]["final_positions"][0].update(status="unvalued", reason="stale_mark")
    source["detailed_report"]["final_valuation"]["complete"] = False
    view = projection(source, "a" * 64)
    assert view["summary"]["finalPortfolioEquity"] is None
    assert view["summary"]["unavailable"]["unrealizedPnl"] == "valuation_evidence_unavailable"
    assert view["tickers"][TICKERS[0]]["valuation"]["reason"] == "stale_mark"


@pytest.mark.parametrize("family", ["linear", "hgb"])
def test_genuine_model_provenance_and_multiple_publications(tmp_path: Path, family: str) -> None:
    root = tmp_path / "publication"
    fixture = tmp_path / "source"
    request, provider = prepared(fixture, family)
    bounded = request.document.to_dict()
    bounded["config"]["decision_dates"] = bounded["config"]["decision_dates"][:1]
    request = FiveTickerRequest(FrozenJSON.freeze(bounded))
    source = execute_fixture(fixture, request, provider)
    report_id, pin = register(root, source)
    summary = PortfolioPublicationReader(root, pin).summary(report_id)
    assert summary["modelFamily"] == family
    assert summary["modelReferences"] == request.document.to_dict()["model_references"]
    assert summary["modelId"] == request.document.to_dict()["model_id"]
    rows = PortfolioPublicationReader(root, pin).ticker(report_id, TICKERS[0])["timeline"]
    if family == "hgb":
        assert all(
            row["feature_contributions"] is None and row["model_created_at"] is None for row in rows
        )
        assert all(row["hgb_pair"] is not None for row in rows)
    else:
        assert all(row["hgb_pair"] is None for row in rows)
    # A different frozen request is a different publication. No fabricated new run.
    d = request.document.to_dict()
    d["initial_cash"] = "999999"
    second = execute_fixture(
        fixture, FiveTickerRequest(FrozenJSON.freeze(d)), provider, output_name="second.db"
    )
    other_id, next_pin = register(root, second, pin)
    reader = PortfolioPublicationReader(root, next_pin)
    assert other_id != report_id
    assert [x["reportId"] for x in reader.collection()["items"]] == sorted([report_id, other_id])
    assert reader.collection(limit=1)["total"] == 2
    assert reader.collection(offset=2)["items"] == []


@pytest.mark.parametrize("scenario", ["pending", "stale"])
def test_real_validated_unavailable_and_pending(
    tmp_path: Path,
    scenario: str,
) -> None:
    fixture = tmp_path / "sources"
    request, _ = prepared(fixture, controlled=True, omit_last=scenario == "stale")
    d = request.document.to_dict()
    d["initial_cash"] = "1500"
    d["config"]["decision_dates"] = d["config"]["decision_dates"][:1]
    if scenario == "pending":
        d["end"] = d["config"]["decision_dates"][0]
    else:
        d["config"]["valuation_policy"]["max_age_seconds"] = 1
        d["config"]["admission_policy"]["ordered_tickers"] = list(reversed(TICKERS))
    source = mechanics_run(fixture, FiveTickerRequest(FrozenJSON.freeze(d)))
    root = tmp_path / "publication"
    report_id, pin = register(root, source)
    http = client(PortfolioPublicationReader(root, pin))
    summary = http.get(f"{BASE}/{report_id}").json()
    trades = http.get(f"{BASE}/{report_id}/trades").json()["items"]
    if scenario == "pending":
        assert summary["fillCount"] == 0 and summary["pendingOrderCount"] == 1
        pending = [x for x in trades if x["classification"] == "pending"]
        assert len(pending) == 1
        assert pending[0]["evidence"]["reservation"]["status"] == "accepted"
        assert pending[0]["evidence"]["executions"] == []
    else:
        assert summary["finalPortfolioEquity"] is None
        assert summary["unrealizedPnl"] is None and summary["portfolioReturn"] is None
        assert summary["finalValuation"]["positions"][0]["reason"] == "stale_mark"
        assert summary["unavailable"]["unrealizedPnl"] == "valuation_evidence_unavailable"
        from donghak_stock_vision.portfolio_publication_cli import diagnose

        diagnostic = diagnose(root, pin, report_id)
        assert diagnostic["portfolioValuationAvailable"] is False
        assert {"valuation_evidence_unavailable", "stale_mark"} <= set(diagnostic["diagnostics"])


@pytest.mark.parametrize("key", ["archiveLocator", "archiveVersion", "resultHash", "validation"])
def test_repinned_index_cannot_break_manifest_bindings(
    isolated: tuple[Path, str, str], key: str
) -> None:
    root, report_id, pin = isolated
    manifest_path = root / f"{report_id}.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[key] = {
        "archiveLocator": "../outside.json",
        "archiveVersion": "wrong",
        "resultHash": "f" * 64,
        "validation": "transport_only",
    }[key]
    raw = encoded(manifest)
    manifest_path.write_bytes(raw)
    index = json.loads((root / "index.json").read_text())
    index["entries"][0]["manifestHash"] = sha(raw)
    index_raw = encoded(index)
    (root / "index.json").write_bytes(index_raw)
    response = client(PortfolioPublicationReader(root, sha(index_raw))).get(f"{BASE}/{report_id}")
    assert response.status_code == 503
    assert (
        response.json()["detail"]["code"]
        == {
            "archiveLocator": "invalid_storage_locator",
            "archiveVersion": "unsupported_archive_version",
            "resultHash": "publication_manifest_mismatch",
            "validation": "result_validation_required",
        }[key]
    )


def test_size_limits_and_fifo(isolated: tuple[Path, str, str]) -> None:
    root, _, _ = isolated
    with pytest.raises(PublicationError, match="oversized_result"):
        LocalFiles(root).read("input.json", 1)
    with pytest.raises(PublicationError, match="oversized_result"):
        LocalFiles(root).digest("input.json", 1)
    import os

    os.mkfifo(root / "pipe")
    with pytest.raises(PublicationError, match="archive_unavailable"):
        LocalFiles(root).read("pipe", 1)


def test_safe_projection_rejects_private_paths(
    published: tuple[Path, str, str, FrozenJSON],
) -> None:
    from donghak_stock_vision.api.historical_portfolios import safe_projection

    view = projection(published[3].to_dict(), "a" * 64)
    view["summary"]["modelReferences"]["model_id"] = "/private/checkpoint.pkl"
    with pytest.raises(PublicationError, match="report_projection_incomplete"):
        safe_projection(view)


def test_cli_parser_and_loopback_guard(tmp_path: Path) -> None:
    from donghak_stock_vision.api_cli import main as serve
    from donghak_stock_vision.portfolio_publication_cli import parser

    args = parser().parse_args(
        [
            "--root",
            str(tmp_path),
            "publish",
            "--archive",
            "input.json",
            "--result-hash",
            "a" * 64,
            "--report-hash",
            "b" * 64,
        ]
    )
    assert args.archive == "input.json"
    with pytest.raises(SystemExit):
        serve(
            [
                "--snapshot",
                str(tmp_path / "missing.json"),
                "--portfolio-publications",
                str(tmp_path),
                "--portfolio-index-sha256",
                "a" * 64,
                "--host",
                "0.0.0.0",
            ]
        )
