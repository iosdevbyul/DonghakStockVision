from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from donghak_stock_vision.providers.base import ProviderError
from donghak_stock_vision.providers.http import RequestGate, RetryingHTTP


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2024, 1, 2, tzinfo=UTC).timestamp()
        self.waits: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.now += delay
        self.waits.append(delay)


def test_gate_persists_across_instances_and_resets_seoul_day(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "quota.db"
    for _ in range(2):
        RequestGate(path, daily_limit=2, clock=clock.time, sleep=clock.sleep).acquire()
    assert clock.waits == [1.0]
    gate = RequestGate(path, daily_limit=2, clock=clock.time, sleep=clock.sleep)
    with pytest.raises(ProviderError, match="budget"):
        gate.acquire()
    clock.now = datetime(2024, 1, 2, 15, tzinfo=UTC).timestamp()  # midnight Seoul
    gate.acquire()


@pytest.mark.parametrize("mode", ["network", "429", "503"])
def test_retry_then_success(tmp_path: Path, mode: str) -> None:
    attempts = 0
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if mode == "network":
                raise httpx.ConnectError("secret must never be logged", request=request)
            return httpx.Response(int(mode), headers={"Retry-After": "3"})
        return httpx.Response(200, content=b"ok")

    gate = RequestGate(tmp_path / "quota.db", clock=clock.time, sleep=clock.sleep)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = RetryingHTTP(client, gate, sleep=clock.sleep).get(
            "https://example.test", headers={}, params={}
        )
    assert result == b"ok"
    assert attempts == 2
    assert max(clock.waits) >= (1 if mode == "network" else 3)


@pytest.mark.parametrize("status,expected", [(401, 1), (403, 1), (400, 1), (500, 3), (429, 3)])
def test_bounded_failure(tmp_path: Path, status: int, expected: int) -> None:
    count = 0
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        gate = RequestGate(tmp_path / "quota.db", clock=clock.time, sleep=clock.sleep)
        with pytest.raises(ProviderError):
            RetryingHTTP(client, gate, sleep=clock.sleep).get(
                "https://example.test", headers={}, params={}
            )
    assert count == expected


def test_long_retry_after_does_not_retry_early(tmp_path: Path) -> None:
    clock = Clock()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "120"})
        )
    ) as client:
        with pytest.raises(ProviderError, match="longer pause"):
            RetryingHTTP(client, RequestGate(tmp_path / "quota.db"), sleep=clock.sleep).get(
                "https://example.test", headers={}, params={}
            )
    assert not clock.waits
