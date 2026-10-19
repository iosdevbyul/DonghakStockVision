"""Actual CLI → replay/publication → startup → GET, one replay per demo family."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from donghak_stock_vision.api.historical_portfolio_models import (
    PortfolioPage,
    PortfolioSummary,
    PortfolioTicker,
    PortfolioTrades,
)
from donghak_stock_vision.api.historical_portfolios import (
    INDEX,
    LocalFiles,
    PublicationError,
    encoded,
    sha,
)
from donghak_stock_vision.api_cli import main as serve
from donghak_stock_vision.portfolio_publication_cli import diagnose, main
from tests.api.portfolio_demo import prepare
from tests.backtest.portfolio_helpers import TICKERS

BASE = "/api/v1/research/historical-portfolios"


def fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()}


def values(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.strip().splitlines())


@pytest.mark.parametrize("family", ["mechanics", "linear", "hgb"])
def test_operator_workflow_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    family: str,
) -> None:
    root = tmp_path / "demo"
    facts = prepare(root, family)
    publication = root / "publication"
    sources_before = fingerprint(root / "sources")
    result_before = sha((publication / "result.json").read_bytes())
    import donghak_stock_vision.backtest.receipt_validation as authority

    original = authority.validate_result
    replay_calls = []

    def replay(document: Any) -> None:
        replay_calls.append(document.identifier)
        original(document)

    monkeypatch.setattr(authority, "validate_result", replay)
    args = ["--root", str(publication)]
    main(args + ["encode", "--result", "result.json", "--archive", "input.json"])
    compact = json.loads(capsys.readouterr().out)
    assert compact["archiveValidation"] == "transport_verified_only"
    assert compact["publicationAuthorized"] is False
    assert replay_calls == []
    assert compact["resultHash"] == facts["resultHash"]
    archive_before = (publication / "input.json").read_bytes()
    main(
        args
        + [
            "publish",
            "--archive",
            "input.json",
            "--result-hash",
            facts["resultHash"],
            "--report-hash",
            facts["reportHash"],
        ]
    )
    published = values(capsys.readouterr().out)
    identifier, pin = published["reportId"], published["indexSha256"]
    assert replay_calls == [facts["resultHash"]]
    # The pin is captured from authorized command output outside the publication root.
    (root / "operator-publication.txt").write_text(f"reportId={identifier}\nindexSha256={pin}\n")
    assert sha((publication / "index.json").read_bytes()) == pin
    assert (publication / "input.json").read_bytes() == archive_before
    assert sha((publication / "result.json").read_bytes()) == result_before
    assert fingerprint(root / "sources") == sources_before

    def forbidden(*a: Any, **kw: Any) -> None:
        raise AssertionError("read-only startup/GET/diagnose must not replay or infer")

    monkeypatch.setattr(authority, "validate_result", forbidden)
    monkeypatch.setattr(authority, "replay_five_ticker", forbidden)
    monkeypatch.setattr("donghak_stock_vision.backtest.runner.BacktestRunner.run", forbidden)
    monkeypatch.setattr("donghak_stock_vision.models.service.load_model", forbidden)
    monkeypatch.setattr("donghak_stock_vision.signals.service.SignalService.research", forbidden)
    applications: list[FastAPI] = []

    def start(app: FastAPI, **settings: Any) -> None:
        assert settings == {"host": "127.0.0.1", "port": 8877, "proxy_headers": False}
        applications.append(app)

    monkeypatch.setattr("uvicorn.run", start)
    server_args = [
        "--snapshot",
        str(root / "snapshot.json"),
        "--portfolio-publications",
        str(publication),
        "--portfolio-index-sha256",
        pin,
        "--port",
        "8877",
    ]
    serve(server_args)
    app = applications[-1]
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    private_before = fingerprint(publication)
    rows = client.get(BASE, params={"limit": 1}).json()
    PortfolioPage.model_validate(rows)
    assert rows["total"] == 1 and rows["items"][0]["reportId"] == identifier
    summary = client.get(f"{BASE}/{identifier}").json()
    PortfolioSummary.model_validate(summary)
    assert summary["resultHash"] == facts["resultHash"]
    assert summary["reportHash"] == facts["reportHash"]
    assert summary["archiveTransportHash"] == compact["archiveTransportHash"]
    assert summary["tickers"] == list(TICKERS) and summary["initialVirtualCash"] == "1500"
    assert summary["researchOnly"] and not summary["operationalEligible"]
    assert summary["evidenceScope"] == "synthetic_fixture"
    ticker = client.get(f"{BASE}/{identifier}/tickers/00088K", params={"limit": 1}).json()
    PortfolioTicker.model_validate(ticker)
    assert ticker["ticker"] == "00088K" and ticker["modelId"] == summary["modelId"]
    trade_response = client.get(f"{BASE}/{identifier}/trades", params={"limit": 2})
    trades = trade_response.json()
    PortfolioTrades.model_validate(trades)
    assert trades["total"] == 5 and len(trades["items"]) == 2
    second = client.get(f"{BASE}/{identifier}/trades?offset=2&limit=2").json()
    assert {x["eventId"] for x in second["items"]}.isdisjoint(x["eventId"] for x in trades["items"])
    assert trades == client.get(f"{BASE}/{identifier}/trades?limit=2").json()
    assert all(x["evidence"]["source"]["run_hash"] == summary["runHash"] for x in trades["items"])
    assert all(
        x["evidence"]["action"] in {"BUY", "HOLD", "SELL", "WAIT", None} for x in trades["items"]
    )
    if family == "mechanics":
        assert summary["acceptedOrderCount"] == summary["fillCount"] == 1
        assert trades["items"][0]["classification"] == "filled"
        assert (
            trades["items"][0]["evidence"]["signal_evidence_scope"]
            == "controlled_mechanics_test_double"
        )
    else:
        assert summary["modelFamily"] == family
        assert all(
            x["evidence"]["signal_evidence_scope"] == "public_model_inference"
            for x in trades["items"]
        )
        if family == "hgb":
            assert trades["items"][0]["evidence"]["feature_contributions"] is None
            assert trades["items"][0]["evidence"]["model_created_at"] is None
            assert trades["items"][0]["evidence"]["hgb_pair"] is not None
    main(args + ["--index-sha256", pin, "diagnose", "--report-id", identifier])
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["reportId"] == identifier and diagnostic["fillCount"] == summary["fillCount"]
    assert ("no_fills" in diagnostic["diagnostics"]) == (summary["fillCount"] == 0)
    assert ("no_accepted_orders" in diagnostic["diagnostics"]) == (
        summary["acceptedOrderCount"] == 0
    )
    assert fingerprint(publication) == private_before
    assert fingerprint(root / "sources") == sources_before

    remote = TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.1", 50000))
    assert remote.get(BASE).status_code == 403
    # Valid transport at an unregistered filename/byte identity never authorizes access.
    (publication / "unregistered.json").write_bytes(archive_before + b"\n")
    manifest = json.loads((publication / f"{identifier}.manifest.json").read_text())
    binding = {
        k: manifest[k] for k in ("archiveHash", "projectionHash", "resultHash", "reportHash")
    }
    binding["archiveHash"] = sha(archive_before + b"\n")
    unregistered = sha(encoded(binding))
    assert client.get(f"{BASE}/{unregistered}").status_code == 404
    assert client.get(BASE).json()["total"] == 1
    archive = publication / f"{identifier}.archive.json"
    stored = archive.read_bytes()
    archive.write_bytes(stored + b" ")
    assert client.get(f"{BASE}/{identifier}").json() == {
        "detail": {"code": "archive_integrity_mismatch"}
    }
    with pytest.raises(SystemExit, match="archive_integrity_mismatch"):
        main(args + ["--index-sha256", pin, "diagnose", "--report-id", identifier])
    archive.write_bytes(stored)
    projection = publication / f"{identifier}.projection.json"
    projection.rename(publication / "missing-projection.json")
    with pytest.raises(SystemExit, match="archive_unavailable"):
        main(args + ["--index-sha256", pin, "diagnose", "--report-id", identifier])
    (publication / "missing-projection.json").rename(projection)

    main(args + ["--index-sha256", pin, "revoke", "--report-id", identifier])
    updated = values(capsys.readouterr().out)["indexSha256"]
    assert updated != pin
    assert client.get(f"{BASE}/{identifier}").json() == {
        "detail": {"code": "publication_manifest_mismatch"}
    }
    with pytest.raises(SystemExit, match="startup blocked: publication_manifest_mismatch"):
        serve(server_args)
    server_args[server_args.index(pin)] = updated
    serve(server_args)
    refreshed = TestClient(applications[-1], base_url="http://127.0.0.1", client=("127.0.0.1", 1))
    assert refreshed.get(BASE).json()["items"] == []
    assert refreshed.get(f"{BASE}/{identifier}").json() == {
        "detail": {"code": "report_unpublished"}
    }
    assert diagnose(publication, updated)["status"] == "no_published_archive"
    assert fingerprint(root / "sources") == sources_before
    assert (publication / "input.json").read_bytes() == archive_before
    # Compact measured facts only, for a reproducible local validation record.
    (root / "verified-get-results.json").write_text(
        json.dumps(
            {
                "family": family,
                "reportId": identifier,
                "resultHash": summary["resultHash"],
                "reportHash": summary["reportHash"],
                "tickers": summary["tickers"],
                "acceptedOrderCount": summary["acceptedOrderCount"],
                "fillCount": summary["fillCount"],
                "initialVirtualCash": summary["initialVirtualCash"],
                "finalPortfolioEquity": summary["finalPortfolioEquity"],
                "tradePageBytes": len(trade_response.content),
                "sourceBytesUnchanged": True,
                "replayCallsDuringPublication": len(replay_calls),
                "getReplayCalls": 0,
            },
            sort_keys=True,
            indent=2,
        )
    )


@pytest.mark.parametrize("failure", ["missing_pin", "bad_root", "bad_pin"])
def test_operator_diagnostics(tmp_path: Path, failure: str) -> None:
    LocalFiles(tmp_path).write("index.json", encoded({"version": INDEX, "entries": []}))
    pin = sha((tmp_path / "index.json").read_bytes())
    if failure == "missing_pin":
        root, supplied, code = tmp_path, None, "missing_trusted_index_sha"
    elif failure == "bad_root":
        root, supplied, code = tmp_path / "absent", pin, "archive_unavailable"
    else:
        root, supplied, code = tmp_path, "a" * 64, "publication_manifest_mismatch"
    with pytest.raises(PublicationError, match=code):
        diagnose(root, supplied)


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"not JSON", "result_validation_required"),
        (b'{"version":"unsupported"}', "unsupported_archive_version"),
    ],
)
def test_encode_invalid_input_fail_closed(tmp_path: Path, payload: bytes, code: str) -> None:
    (tmp_path / "result.json").write_bytes(payload)
    with pytest.raises(SystemExit, match=code):
        main(
            [
                "--root",
                str(tmp_path),
                "encode",
                "--result",
                "result.json",
                "--archive",
                "input.json",
            ]
        )
    assert not (tmp_path / "input.json").exists()


def test_demo_output_never_clobbers_or_uses_protected_root(tmp_path: Path) -> None:
    (tmp_path / "existing").mkdir()
    with pytest.raises(PublicationError, match="demo_output_exists"):
        prepare(tmp_path / "existing")
    with pytest.raises(PublicationError, match="unsafe_demo_output"):
        prepare(tmp_path / ".data" / "new")


def test_invalid_snapshot_startup_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="research snapshot unavailable or invalid"):
        serve(["--snapshot", str(tmp_path / "absent")])


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"{", "result_validation_required"),
        (b'{"version":"not_supported"}', "unsupported_archive_version"),
        (b'{"version":"shared_trading_receipts_v1","metadata":[]}', "result_validation_required"),
        (b'{"version":"shared_trading_receipts_v1","metadata":null}', "result_validation_required"),
    ],
)
def test_corrupt_compact_publication_diagnostic(tmp_path: Path, payload: bytes, code: str) -> None:
    (tmp_path / "input.json").write_bytes(payload)
    with pytest.raises(SystemExit, match=code):
        main(
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
    assert not (tmp_path / "index.json").exists()


def test_missing_publication_pin_startup_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        serve(["--snapshot", str(tmp_path / "absent"), "--portfolio-publications", str(tmp_path)])
