"""Track C final verification over real local HTTP, never a provider or broker."""

import hashlib
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from donghak_stock_vision.api import create_app
from donghak_stock_vision.api.ohlcv import PinnedOperationalReader
from tests.api.test_krx_scheduler_diagnostics import reports
from tests.api.test_operational_market_read import make_snapshot


_ORIGINAL_CONNECT = socket.socket.connect


@contextmanager
def local_api(snapshot: Path, sha256: str) -> Iterator[str]:
    app = create_app(
        operational_reader=PinnedOperationalReader(snapshot, expected_sha256=sha256),
        krx_diagnostics_reports=reports,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="critical")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started or not server.servers or not server.servers[0].sockets:
            raise AssertionError("local HTTP server did not start")
        address = server.servers[0].sockets[0].getsockname()
        if not isinstance(address, tuple):
            raise AssertionError("unexpected local HTTP socket address")
        yield f"http://127.0.0.1:{address[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise AssertionError("local HTTP server did not stop")


def test_operations_http_finalization_keeps_evidence_unreviewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "track-c.snapshot.sqlite3"
    sha = make_snapshot(snapshot)
    before = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    with local_api(snapshot, sha) as base:
        # Permit only this ephemeral loopback server; keep external TCP forbidden.
        port = int(base.rsplit(":", 1)[1])

        def local_only_connect(sock: socket.socket, address: object) -> None:
            if (
                not isinstance(address, tuple)
                or len(address) != 2
                or address != ("127.0.0.1", port)
            ):
                raise AssertionError("network is forbidden in offline tests")
            _ORIGINAL_CONNECT(sock, address)

        monkeypatch.setattr(socket.socket, "connect", local_only_connect)
        with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:
            summary = client.get("/api/v1/operational/summary")
            catalog = client.get("/api/v1/operational/stocks?offset=0&limit=25")
            bars = client.get("/api/v1/operational/stocks/005930/ohlcv")
            diagnostics = client.get("/api/v1/operations/krx-scheduler/diagnostics")
            forbidden = client.post("/api/v1/operations/krx-scheduler/diagnostics")
            missing = client.get("/api/v1/operational/stocks/999999/ohlcv")

            assert summary.status_code == catalog.status_code == bars.status_code == 200
            assert diagnostics.status_code == 200
            assert summary.json()["evidenceStatus"] == "unreviewed"
            assert summary.json()["lastTradingDate"] == "2026-10-29"
            assert catalog.json()["items"][0]["latestDataDate"] == "2026-10-29"
            assert bars.json()["bars"][0]["date"] == "2026-10-29"
            payload = diagnostics.json()
            assert payload["operatorConfirmationStillRequired"] is True
            assert payload["networkRequests"] == 0
            assert payload["schedulerChanged"] is False
            assert payload["paperExecutionEligible"] is False
            assert forbidden.status_code == 405
            assert missing.status_code == 404

    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before
