"""Short-lived real Uvicorn server; only its reserved IPv4 loopback port is reachable."""

import os
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from tests.api.test_portfolio_comparison_workflow import Workflow


class ReadyServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = threading.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        self.ready.set()


@dataclass
class TcpLease:
    address: tuple[str, int]
    listener: socket.socket
    server: ReadyServer
    thread: threading.Thread
    client: httpx.Client
    closed: bool = False


class LoopbackTCP:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow
        self.ports: set[int] = set()
        self.connections = 0
        self.leases: list[TcpLease] = []

    def allowed(self, address: Any) -> None:
        if not (
            isinstance(address, tuple)
            and len(address) == 2
            and address[0] == "127.0.0.1"
            and address[1] in self.ports
        ):
            self.workflow.counters["network"] += 1
            raise AssertionError("only the owned loopback TCP listener is permitted")

    @contextmanager
    def network_policy(self) -> Iterator[None]:
        def connect(sock: socket.socket, address: Any) -> None:
            self.allowed(address)
            assert sock.family == socket.AF_INET
            self.workflow.socket_connect(sock, address)
            self.connections += 1

        def connect_ex(sock: socket.socket, address: Any) -> int:
            self.allowed(address)
            assert sock.family == socket.AF_INET
            result: int = self.workflow.socket_connect_ex(sock, address)
            return result

        def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
            self.allowed(address)
            result: socket.socket = self.workflow.socket_create_connection(address, *args, **kwargs)
            return result

        # The original fixture still forbids all networking by default. Only this
        # scope permits the exact ephemeral listener currently owned by this harness.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(socket.socket, "connect", connect)
            patch.setattr(socket.socket, "connect_ex", connect_ex)
            patch.setattr(socket, "create_connection", create_connection)
            yield
        assert not self.ports
        assert all(lease.closed for lease in self.leases)

    @contextmanager
    def serve(self, root: Path, pin: str) -> Iterator[TcpLease]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        port: int | None = None
        try:
            listener.bind(("127.0.0.1", 0))
            host, port = listener.getsockname()
            assert host == "127.0.0.1" and port > 0
            self.ports.add(port)
            # Use the actual CLI app/pin/proxy configuration; capture only its
            # convenience uvicorn.run call, then run a real Server on our held FD.
            app = self.workflow.application(root, pin, port)
            server = ReadyServer(
                uvicorn.Config(
                    app,
                    host=host,
                    port=port,
                    proxy_headers=False,
                    workers=1,
                    loop="asyncio",
                    http="h11",
                    lifespan="on",
                    log_config=None,
                    access_log=False,
                    timeout_keep_alive=1,
                    timeout_graceful_shutdown=2,
                )
            )
            errors: list[BaseException] = []

            def run() -> None:
                try:
                    server.run(sockets=[listener])
                except BaseException as error:
                    errors.append(error)
                finally:
                    server.ready.set()

            before_threads = set(threading.enumerate())
            pid = os.getpid()
            thread = threading.Thread(target=run, name=f"dsv-tcp-{port}", daemon=True)
            client = httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=5,
                trust_env=False,
                transport=httpx.HTTPTransport(retries=0, trust_env=False),
            )
            lease = TcpLease((host, port), listener, server, thread, client)
            self.leases.append(lease)
            started = False
            try:
                thread.start()
                started = True
                assert server.ready.wait(10), "bounded Uvicorn startup timed out"
                assert not errors, errors
                assert server.started and thread.is_alive(), "Uvicorn did not start"
                assert server.servers and all(
                    sock.getsockname()[0] == "127.0.0.1"
                    for running in server.servers
                    for sock in running.sockets or []
                )
                yield lease
            finally:
                # This also runs for request/assertion failures and startup errors.
                client.close()
                server.should_exit = True
                if started:
                    thread.join(6)
                    if thread.is_alive():
                        server.force_exit = True
                        thread.join(2)
                listener.close()
                assert not thread.is_alive(), "owned Uvicorn thread leaked"
                assert listener.fileno() == -1
                assert all(not running.is_serving() for running in getattr(server, "servers", []))
                assert os.getpid() == pid  # no server subprocess was created
                # AnyIO stops workers when the server's root task finishes. Wait
                # only for newly created workers; never stop unrelated threads.
                for worker in set(threading.enumerate()) - before_threads:
                    if worker.name == "AnyIO worker thread":
                        worker.join(2)
                        assert not worker.is_alive(), "owned HTTP worker leaked"
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    assert probe.connect_ex((host, port)) != 0, "listener still accepts TCP"
                assert not errors, errors
                lease.closed = True
        finally:
            listener.close()
            if port is not None:
                self.ports.discard(port)
