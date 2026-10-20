"""Real HTTP/1.1 over owned 127.0.0.1 ephemeral listeners; no TestClient requests."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from donghak_stock_vision.api.historical_portfolio_models import PortfolioComparison
from donghak_stock_vision.api.historical_portfolios import LocalFiles, encoded, sha
from donghak_stock_vision.portfolio_publication_cli import main as publication_cli
from tests.api.loopback_tcp import LoopbackTCP
from tests.api.test_portfolio_comparison_workflow import (
    BASE,
    URL,
    Workflow,
    invoke,
)
from tests.api.test_portfolio_comparison_workflow import (
    workflow as workflow,
)
from tests.api.test_portfolio_workflow import fingerprint, values


@pytest.fixture
def tcp(workflow: Workflow, block_network: None) -> Iterator[LoopbackTCP]:
    # Install after the existing autouse function guard, restore it afterwards.
    # No live marker, global exception or change to tests/conftest.py is needed.
    harness = LoopbackTCP(workflow)
    with harness.network_policy():
        yield harness
    assert harness.connections > 0
    assert all(lease.closed for lease in harness.leases)
    assert workflow.counters == dict.fromkeys(workflow.counters, 0)


@pytest.fixture
def isolated(tmp_path: Path, workflow: Workflow) -> Path:
    root = tmp_path / "publications"
    shutil.copytree(workflow.publication, root)
    return root


@pytest.mark.parametrize("selection", [(1, 2), (2, 0, 1), (3, 1, 0, 2)])
def test_real_tcp_comparison(
    tcp: LoopbackTCP, workflow: Workflow, selection: tuple[int, ...]
) -> None:
    ids = [workflow.ids[index] for index in selection]
    source_before = fingerprint(workflow.publication)
    with tcp.serve(workflow.publication, workflow.pin) as live:
        response = live.client.post(URL, json={"reportIds": ids})
        assert response.status_code == 200, response.text
        assert response.http_version == "HTTP/1.1"
        stream = response.extensions["network_stream"]
        assert stream.get_extra_info("server_addr") == live.address
        result = response.json()
        PortfolioComparison.model_validate(result)
        assert [item["reportId"] for item in result["items"]] == ids
        assert response.content == live.client.post(URL, json={"reportIds": ids}).content
        expected = {record["reportId"]: record for record in workflow.records.values()}
        for item in result["items"]:
            original = expected[item["reportId"]]
            assert {key: item[key] for key in original["expected"]} == original["expected"]
            assert item["archiveTransportHash"] == original["encoding"]["archiveTransportHash"]
            assert item["archiveVerification"] == "integrity_verified"
            assert item["reportVerification"] == "offline_authoritative_replay_passed"
            assert item["researchOnly"] and not item["operationalEligible"]
            assert item["dataOrigin"] == "synthetic"
        if 3 in selection:
            stale = result["items"][selection.index(3)]
            assert stale["finalPortfolioEquity"] is None and stale["unrealizedPnl"] is None
            assert stale["unavailable"]["finalPortfolioEquity"] == "valuation_evidence_unavailable"
            assert stale["finalValuation"]["positions"][0]["reason"] == "stale_mark"
            assert "valuation_evidence_unavailable" in result["comparability"]["notices"]
        assert live.server.config.host == live.address[0] == "127.0.0.1"
        assert live.server.config.proxy_headers is False
        assert live.server.config.workers == 1
    assert live.closed and live.client.is_closed
    assert not live.thread.is_alive() and live.listener.fileno() == -1
    assert fingerprint(workflow.publication) == source_before


@pytest.mark.parametrize(
    "damage,status,code",
    [
        ("one", 422, "invalid_comparison_request"),
        ("six", 422, "invalid_comparison_request"),
        ("duplicate", 422, "invalid_comparison_request"),
        ("unknown", 404, "report_not_found"),
        ("invalid", 422, "invalid_comparison_request"),
    ],
)
def test_real_tcp_invalid_selection(
    tcp: LoopbackTCP, workflow: Workflow, damage: str, status: int, code: str
) -> None:
    ids = list(workflow.ids[:2])
    if damage == "one":
        ids.pop()
    elif damage == "six":
        ids = list(workflow.ids) + ["a" * 64, "b" * 64]
    elif damage == "duplicate":
        ids[1] = ids[0]
    elif damage == "unknown":
        ids[1] = "f" * 64
    else:
        ids[1] = "../unapproved.json"
    with tcp.serve(workflow.publication, workflow.pin) as live:
        workflow.record_error(
            "tcp_" + damage, live.client.post(URL, json={"reportIds": ids}), status, code
        )


@pytest.mark.parametrize("raw", [b"{", b"[]"])
def test_real_tcp_malformed_body(tcp: LoopbackTCP, workflow: Workflow, raw: bytes) -> None:
    with tcp.serve(workflow.publication, workflow.pin) as live:
        response = live.client.post(URL, content=raw, headers={"Content-Type": "application/json"})
        workflow.record_error("tcp_malformed", response, 422, "invalid_comparison_request")


def test_real_tcp_unapproved_archive(tcp: LoopbackTCP, workflow: Workflow, isolated: Path) -> None:
    report_id = workflow.ids[0]
    manifest = json.loads((isolated / (report_id + ".manifest.json")).read_text())
    raw = (isolated / (report_id + ".archive.json")).read_bytes() + b"\n"
    LocalFiles(isolated).write("unapproved.json", raw)
    binding = {
        key: manifest[key] for key in ("archiveHash", "projectionHash", "resultHash", "reportHash")
    }
    binding["archiveHash"] = sha(raw)
    unapproved = sha(encoded(binding))
    with tcp.serve(isolated, workflow.pin) as live:
        response = live.client.post(URL, json={"reportIds": [workflow.ids[1], unapproved]})
        workflow.record_error("tcp_unapproved", response, 404, "report_not_found")
        assert live.client.post(BASE, json={"reportIds": workflow.ids[:2]}).status_code == 405


def test_real_tcp_stale_pin_and_revocation(
    tcp: LoopbackTCP, workflow: Workflow, isolated: Path, tmp_path: Path
) -> None:
    body = {"reportIds": workflow.ids[:2]}
    archives_before = {
        path.name: sha(path.read_bytes()) for path in isolated.glob("*.archive.json")
    }
    with tcp.serve(isolated, workflow.pin) as old:
        assert old.client.post(URL, json=body).status_code == 200
        output = invoke(
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
        (tmp_path / "authorized-revocation.txt").write_text(output)
        updated = values(output)["indexSha256"]
        workflow.record_error(
            "tcp_stale_pin", old.client.post(URL, json=body), 503, "publication_manifest_mismatch"
        )
    with tcp.serve(isolated, updated) as current:
        workflow.record_error(
            "tcp_revoked", current.client.post(URL, json=body), 404, "report_unpublished"
        )
        assert current.client.post(URL, json={"reportIds": workflow.ids[1:3]}).status_code == 200
    assert {
        path.name: sha(path.read_bytes()) for path in isolated.glob("*.archive.json")
    } == archives_before


@pytest.mark.parametrize(
    "damage,code",
    [
        ("missing_projection", "archive_unavailable"),
        ("corrupt_projection", "publication_manifest_mismatch"),
        ("corrupt_archive", "archive_integrity_mismatch"),
    ],
)
def test_real_tcp_damaged_evidence(
    tcp: LoopbackTCP, workflow: Workflow, isolated: Path, damage: str, code: str
) -> None:
    body = {"reportIds": workflow.ids[:2]}
    with tcp.serve(isolated, workflow.pin) as live:
        assert live.client.post(URL, json=body).status_code == 200
        suffix = ".archive.json" if damage == "corrupt_archive" else ".projection.json"
        path = isolated / (workflow.ids[1] + suffix)
        if damage == "missing_projection":
            path.unlink()
        else:
            path.write_bytes(path.read_bytes() + b" ")
        workflow.record_error("tcp_" + damage, live.client.post(URL, json=body), 503, code)


def test_real_tcp_host_policy(tcp: LoopbackTCP, workflow: Workflow) -> None:
    with tcp.serve(workflow.publication, workflow.pin) as live:
        body = {"reportIds": workflow.ids[:2]}
        response = live.client.post(URL, json=body, headers={"Host": "example.com"})
        workflow.record_error("tcp_host", response, 403, "local_research_access_required")
        # Proxy headers cannot change the real accepted loopback peer.
        assert (
            live.client.post(URL, json=body, headers={"X-Forwarded-For": "192.0.2.1"}).status_code
            == 200
        )


def test_real_tcp_failure_also_releases_resources(tcp: LoopbackTCP, workflow: Workflow) -> None:
    with pytest.raises(RuntimeError, match="intentional test-body failure"):
        with tcp.serve(workflow.publication, workflow.pin) as live:
            assert live.client.post(URL, json={"reportIds": workflow.ids[:2]}).status_code == 200
            raise RuntimeError("intentional test-body failure")
    assert live.closed and live.client.is_closed
    assert not live.thread.is_alive() and live.listener.fileno() == -1
    assert tcp.ports == set()
