"""Real preparation/CLI registration → pinned app → comparison → CLI revocation."""

import io
import json
import shutil
import socket
from collections.abc import Callable, Iterator
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from donghak_stock_vision.api.historical_portfolio_models import PortfolioComparison
from donghak_stock_vision.api.historical_portfolios import LocalFiles, encoded, sha
from donghak_stock_vision.api_cli import main as serve
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.portfolio_publication_cli import main as publication_cli
from tests.api.portfolio_demo import main as prepare_cli
from tests.api.test_portfolio_workflow import fingerprint, values

BASE = "/api/v1/research/historical-portfolios"
URL = BASE + "/compare"


def invoke(command: Callable[[list[str] | None], None], args: list[str]) -> str:
    with redirect_stdout(io.StringIO()) as output:
        command(args)
    return output.getvalue()


@dataclass
class Workflow:
    root: Path
    publication: Path
    ids: tuple[str, ...]
    pin: str
    records: dict[str, dict[str, Any]]
    observations: dict[str, Any]
    counters: dict[str, int]
    connect: Callable[[Path, str], TestClient]

    def record_error(self, label: str, response: Any, status: int, code: str) -> None:
        assert response.status_code == status, response.text
        assert response.json() == {"detail": {"code": code}}
        assert str(self.root) not in response.text
        self.observations["diagnostics"][label] = {
            "status": response.status_code,
            **response.json(),
        }


@pytest.fixture(scope="module")
def workflow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Workflow]:
    work = tmp_path_factory.mktemp("comparison-workflow")
    root = work / "publications"
    root.mkdir(mode=0o700)
    counters = {"network": 0, "replay": 0, "backtest": 0, "modelLoading": 0, "inference": 0}
    records: dict[str, dict[str, Any]] = {}
    source_hashes: dict[str, dict[str, str]] = {}
    observations: dict[str, Any] = {
        "scope": "executed_synthetic_comparison_workflow_not_real_market_performance",
        "diagnostics": {},
    }

    def deny(kind: str) -> Callable[..., Any]:
        def forbidden(*args: Any, **kwargs: Any) -> Any:
            counters[kind] += 1
            raise AssertionError(f"workflow attempted forbidden {kind}")

        return forbidden

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", deny("network"))
        patch.setattr(socket, "create_connection", deny("network"))
        # Preparation uses the existing original synthetic runners and tiny model fixtures.
        for label, family, scenario in (
            ("mechanics", "mechanics", "standard"),
            ("linear", "linear", "standard"),
            ("hgb", "hgb", "standard"),
            ("stale", "mechanics", "stale_valuation"),
        ):
            demo = work / label
            facts = json.loads(
                invoke(
                    prepare_cli, ["--output", str(demo), "--family", family, "--scenario", scenario]
                )
            )
            source_hashes[label] = fingerprint(demo)
            source = FrozenJSON((demo / "publication/result.json").read_text())
            d = source.to_dict()
            records[label] = {
                "facts": facts,
                "expected": {
                    "resultHash": source.identifier,
                    "reportHash": d["detailed_report_hash"],
                    "runHash": d["run_hash"],
                    "performanceHash": d["performance_hash"],
                    "modelReferences": d["frozen_input"]["request"]["model_references"],
                    "provenance": d["detailed_report"]["sources"],
                    "period": facts["period"],
                    "fillCount": d["performance"]["fill_count"],
                    "finalPortfolioEquity": d["performance"]["final_equity"],
                    "realizedNetPnl": d["performance"]["ledger_realized_pnl"],
                    "unrealizedPnl": d["performance"]["unrealized_pnl"],
                },
            }
            LocalFiles(root).write(label + "-result.json", source.payload_json.encode())

        import donghak_stock_vision.backtest.receipt_validation as authority

        original = authority.validate_result
        replays: list[str] = []

        def replay(document: FrozenJSON) -> None:
            replays.append(document.identifier)
            original(document)

        patch.setattr(authority, "validate_result", replay)
        pin: str | None = None
        approved = work / "approved-output"
        approved.mkdir(mode=0o700)
        for label, record in records.items():
            args = ["--root", str(root)]
            before = len(replays)
            compact = json.loads(
                invoke(
                    publication_cli,
                    args
                    + [
                        "encode",
                        "--result",
                        label + "-result.json",
                        "--archive",
                        label + "-input.json",
                    ],
                )
            )
            assert len(replays) == before  # encoding is transport-only
            assert compact["publicationAuthorized"] is False
            assert compact["archiveValidation"] == "transport_verified_only"
            assert compact["resultHash"] == record["expected"]["resultHash"]
            current = ["--index-sha256", pin] if pin is not None else []
            output = invoke(
                publication_cli,
                args
                + current
                + [
                    "publish",
                    "--archive",
                    label + "-input.json",
                    "--result-hash",
                    compact["resultHash"],
                    "--report-hash",
                    compact["reportHash"],
                ],
            )
            (approved / (label + ".txt")).write_text(output)
            registered = values(output)
            pin, report_id = registered["indexSha256"], registered["reportId"]
            assert replays[before:] == [record["expected"]["resultHash"]]
            assert sha((root / "index.json").read_bytes()) == pin
            record.update(reportId=report_id, encoding=compact)
            assert (root / (label + "-input.json")).read_bytes() == (
                root / (report_id + ".archive.json")
            ).read_bytes()
        assert pin is not None and len(replays) == 4
        immutable = fingerprint(root)
        # Every ordinary startup/GET/POST/diagnose now forbids execution and loading.
        patch.setattr(authority, "validate_result", deny("replay"))
        patch.setattr(authority, "replay_five_ticker", deny("replay"))
        patch.setattr("donghak_stock_vision.backtest.runner.BacktestRunner.run", deny("backtest"))
        patch.setattr("donghak_stock_vision.models.service.load_model", deny("modelLoading"))
        patch.setattr(
            "donghak_stock_vision.models.inference.LinearInference.__init__", deny("modelLoading")
        )
        patch.setattr(
            "donghak_stock_vision.models.inference.HGBInference.__init__", deny("modelLoading")
        )
        patch.setattr(
            "donghak_stock_vision.models.inference.LinearInference.score", deny("inference")
        )
        patch.setattr(
            "donghak_stock_vision.models.inference.HGBInference.research", deny("inference")
        )
        patch.setattr(
            "donghak_stock_vision.signals.service.SignalService.research", deny("inference")
        )
        apps: list[FastAPI] = []

        def capture(app: FastAPI, **settings: Any) -> None:
            assert settings == {"host": "127.0.0.1", "port": 8877, "proxy_headers": False}
            apps.append(app)

        patch.setattr("uvicorn.run", capture)

        def connect(publication: Path, trusted: str) -> TestClient:
            serve(
                [
                    "--snapshot",
                    str(work / "mechanics/snapshot.json"),
                    "--portfolio-publications",
                    str(publication),
                    "--portfolio-index-sha256",
                    trusted,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8877",
                ]
            )
            return TestClient(apps[-1], base_url="http://127.0.0.1", client=("127.0.0.1", 123))

        ids = tuple(records[label]["reportId"] for label in ("hgb", "mechanics", "linear", "stale"))
        observations.update(
            reports=records,
            publicationIndexSha256=pin,
            authoritativePublicationReplayCount=len(replays),
        )
        yield Workflow(work, root, ids, pin, records, observations, counters, connect)
        assert fingerprint(root) == immutable
        for label, before_hashes in source_hashes.items():
            assert fingerprint(work / label) == before_hashes
        assert counters == dict.fromkeys(counters, 0)
        observations.update(
            apiExecutionCalls=counters,
            integrity={
                "sourceAndArchiveBytesUnchanged": True,
                "sourceFileCount": sum(len(v) for v in source_hashes.values()),
                "sourceDatabaseSha256": {
                    label: {k: v for k, v in hashes.items() if k.endswith(".db")}
                    for label, hashes in source_hashes.items()
                },
                "publicationFileSha256": immutable,
            },
        )
        (work / "workflow-observations.json").write_text(
            json.dumps(observations, sort_keys=True, indent=2) + "\n"
        )


def test_comparison_cli_to_verified_response(workflow: Workflow) -> None:
    http = workflow.connect(workflow.publication, workflow.pin)
    request = {"reportIds": list(workflow.ids)}
    response = http.post(URL, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    PortfolioComparison.model_validate(result)
    assert response.content == http.post(URL, json=request).content
    assert [item["reportId"] for item in result["items"]] == list(workflow.ids)
    expected = {r["reportId"]: r for r in workflow.records.values()}
    for item in result["items"]:
        record = expected[item["reportId"]]
        assert {k: item[k] for k in record["expected"]} == record["expected"]
        assert item["archiveTransportHash"] == record["encoding"]["archiveTransportHash"]
        assert item["archiveVerification"] == "integrity_verified"
        assert item["reportVerification"] == "offline_authoritative_replay_passed"
        assert item["researchOnly"] and not item["operationalEligible"]
        assert item["dataOrigin"] == "synthetic" and item["tickerCount"] == 5
        trades = http.get(BASE + "/" + item["reportId"] + "/trades?limit=20").json()
        assert {x["evidence"]["signal_evidence_scope"] for x in trades["items"]} == set(
            item["signalEvidenceScopes"]
        )
        assert all(x["evidence"]["source"]["run_hash"] == item["runHash"] for x in trades["items"])
        diagnostic = json.loads(
            invoke(
                publication_cli,
                [
                    "--root",
                    str(workflow.publication),
                    "--index-sha256",
                    workflow.pin,
                    "diagnose",
                    "--report-id",
                    item["reportId"],
                ],
            )
        )
        assert diagnostic["fillCount"] == item["fillCount"]
    hgb, mechanics, linear, stale = result["items"]
    assert hgb["modelFamily"] == "hgb" and linear["modelFamily"] == "linear"
    assert (
        hgb["acceptedOrderCount"]
        == hgb["fillCount"]
        == linear["acceptedOrderCount"]
        == linear["fillCount"]
        == 0
    )
    assert mechanics["acceptedOrderCount"] == mechanics["fillCount"] == 1
    assert mechanics["signalEvidenceScopes"] == ["controlled_mechanics_test_double"]
    assert (
        hgb["signalEvidenceScopes"] == linear["signalEvidenceScopes"] == ["public_model_inference"]
    )
    assert stale["finalPortfolioEquity"] is None and stale["unrealizedPnl"] is None
    assert stale["finalValuation"]["positions"][0]["reason"] == "stale_mark"
    assert stale["unavailable"]["finalPortfolioEquity"] == "valuation_evidence_unavailable"
    assert result["comparability"]["periodsDiffer"] is True
    assert result["comparability"]["valuationTimestampsDiffer"] is True
    workflow.observations.update(compareRequest=request, comparisonResponse=result)
    workflow.observations["diagnostics"]["valuation_unavailable"] = {
        "status": response.status_code,
        "reason": stale["unavailable"]["finalPortfolioEquity"],
        "positionReason": "stale_mark",
    }


@pytest.mark.parametrize("count", [2, 3])
def test_request_order(workflow: Workflow, count: int) -> None:
    ids = list(reversed(workflow.ids[:count]))
    response = workflow.connect(workflow.publication, workflow.pin).post(
        URL, json={"reportIds": ids}
    )
    assert response.status_code == 200
    assert [row["reportId"] for row in response.json()["items"]] == ids


@pytest.mark.parametrize(
    "damage,status,code",
    [
        ("unapproved", 404, "report_not_found"),
        ("invalid", 422, "invalid_comparison_request"),
        ("duplicate", 422, "invalid_comparison_request"),
        ("excessive", 422, "invalid_comparison_request"),
        ("path_id", 422, "invalid_comparison_request"),
        ("path_selector", 422, "invalid_comparison_request"),
    ],
)
def test_comparison_diagnostics(workflow: Workflow, damage: str, status: int, code: str) -> None:
    body: dict[str, Any] = {"reportIds": list(workflow.ids[:2])}
    if damage == "unapproved":
        body["reportIds"][1] = "f" * 64
    elif damage == "invalid":
        body["reportIds"][1] = "not-a-report-id"
    elif damage == "duplicate":
        body["reportIds"][1] = body["reportIds"][0]
    elif damage == "excessive":
        body["reportIds"] = list(workflow.ids) + ["a" * 64, "b" * 64]
    elif damage == "path_id":
        body["reportIds"][1] = "../input.json"
    else:
        body["publicationRoot"] = "/tmp/other-publications"
    response = workflow.connect(workflow.publication, workflow.pin).post(URL, json=body)
    workflow.record_error(damage, response, status, code)


@pytest.fixture
def isolated(tmp_path: Path, workflow: Workflow) -> Path:
    root = tmp_path / "publications"
    shutil.copytree(workflow.publication, root)
    return root


def test_valid_unregistered_archive(workflow: Workflow, isolated: Path) -> None:
    report_id = workflow.ids[0]
    manifest = json.loads((isolated / (report_id + ".manifest.json")).read_text())
    raw = (isolated / (report_id + ".archive.json")).read_bytes() + b"\n"
    LocalFiles(isolated).write("unregistered.json", raw)
    binding = {
        k: manifest[k] for k in ("archiveHash", "projectionHash", "resultHash", "reportHash")
    }
    binding["archiveHash"] = sha(raw)
    unknown = sha(encoded(binding))
    response = workflow.connect(isolated, workflow.pin).post(
        URL, json={"reportIds": [workflow.ids[1], unknown]}
    )
    workflow.record_error("unregistered_valid_transport", response, 404, "report_not_found")


@pytest.mark.parametrize(
    "damage,code",
    [("archive", "archive_integrity_mismatch"), ("projection", "archive_unavailable")],
)
def test_damaged_copy(workflow: Workflow, isolated: Path, damage: str, code: str) -> None:
    http = workflow.connect(isolated, workflow.pin)
    body = {"reportIds": workflow.ids[:2]}
    assert http.post(URL, json=body).status_code == 200
    target = isolated / (workflow.ids[1] + "." + damage + ".json")
    if damage == "archive":
        target.write_bytes(target.read_bytes() + b" ")
    else:
        target.unlink()
    workflow.record_error(damage, http.post(URL, json=body), 503, code)


def test_cli_revocation_stale_pin_and_empty_registry(workflow: Workflow, isolated: Path) -> None:
    old = workflow.connect(isolated, workflow.pin)
    body = {"reportIds": workflow.ids[:2]}
    assert old.post(URL, json=body).status_code == 200
    pin = values(
        invoke(
            publication_cli,
            [
                "--root",
                str(isolated),
                "--index-sha256",
                workflow.pin,
                "revoke",
                "--report-id",
                workflow.ids[0],
            ],
        )
    )["indexSha256"]
    workflow.record_error(
        "stale_pin", old.post(URL, json=body), 503, "publication_manifest_mismatch"
    )
    with pytest.raises(SystemExit, match="startup blocked: publication_manifest_mismatch"):
        workflow.connect(isolated, workflow.pin)
    http = workflow.connect(isolated, pin)
    workflow.record_error("revoked", http.post(URL, json=body), 404, "report_unpublished")
    assert http.post(URL, json={"reportIds": workflow.ids[1:]}).status_code == 200
    for report_id in workflow.ids[1:]:
        pin = values(
            invoke(
                publication_cli,
                [
                    "--root",
                    str(isolated),
                    "--index-sha256",
                    pin,
                    "revoke",
                    "--report-id",
                    report_id,
                ],
            )
        )["indexSha256"]
    empty = workflow.connect(isolated, pin)
    collection = empty.get(BASE)
    assert collection.status_code == 200 and collection.json()["items"] == []
    assert collection.json()["total"] == 0
    diagnostic = json.loads(
        invoke(publication_cli, ["--root", str(isolated), "--index-sha256", pin, "diagnose"])
    )
    assert diagnostic["status"] == "no_published_archive"
    workflow.record_error(
        "empty_registry_known_revoked", empty.post(URL, json=body), 404, "report_unpublished"
    )
    workflow.record_error(
        "empty_registry_unknown",
        empty.post(URL, json={"reportIds": ["a" * 64, "b" * 64]}),
        404,
        "report_not_found",
    )
    workflow.observations["diagnostics"]["empty_registry_list"] = {
        "status": collection.status_code,
        "total": collection.json()["total"],
        "diagnose": diagnostic,
    }


@pytest.mark.parametrize("headers", [{"Host": "evil.example"}, {"Origin": "https://evil.example"}])
def test_local_boundary(workflow: Workflow, headers: dict[str, str]) -> None:
    response = workflow.connect(workflow.publication, workflow.pin).post(
        URL, json={"reportIds": workflow.ids[:2]}, headers=headers
    )
    workflow.record_error(
        "local_" + next(iter(headers)), response, 403, "local_research_access_required"
    )


def test_stale_scenario_rejects_genuine_family_before_creation(tmp_path: Path) -> None:
    output = tmp_path / "invalid"
    with pytest.raises(SystemExit, match="unsupported_demo_scenario"):
        invoke(
            prepare_cli,
            ["--output", str(output), "--family", "hgb", "--scenario", "stale_valuation"],
        )
    assert not output.exists()
