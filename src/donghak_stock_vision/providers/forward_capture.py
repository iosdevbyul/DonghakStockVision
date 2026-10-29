"""Opt-in bounded HTTP recorder for the original KRX provider, never auto-started."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.providers.base import ProviderError
from donghak_stock_vision.providers.http import RequestGate, RetryingHTTP


def capture_client(*, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Shared capture client defaults; origin/TLS/retry checks remain in run_capture."""
    return httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=20)


class CaptureHTTP(RetryingHTTP):
    """Sequential streaming transport; bounded retries, no redirects or proxy discovery.

    Callback commits each outcome before a retry. Deadline/cancellation are cooperative;
    the per-request timeout bounds stalled transport, not arbitrary injected Python code.
    """

    def __init__(
        self,
        client: httpx.Client,
        gate: RequestGate,
        plan: ForwardCapturePlan,
        record: Callable[[dict[str, Any], bytes], None],
        *,
        cancelled: Callable[[], bool] = lambda: False,
        authorization_check: Callable[[], None] = lambda: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(client, gate, attempts=plan.max_requests, sleep=sleep)
        self.plan, self.record, self.cancelled = plan, record, cancelled
        self.authorization_check = authorization_check
        self.clock, self.monotonic = clock, monotonic
        self.deadline = monotonic() + plan.max_seconds
        self.completed_at: datetime | None = None

    def guard(self) -> None:
        self.authorization_check()
        if self.cancelled():
            raise ProviderError("capture_cancelled")
        if self.monotonic() >= self.deadline:
            raise ProviderError("capture_deadline_exhausted")

    def now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ProviderError("naive_capture_clock")
        return value.astimezone(UTC)

    def get(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> bytes:
        for index in range(self.attempts):
            self.guard()
            # Original durable daily/spacing gate is shared across pilot invocations.
            self.gate.acquire()
            self.guard()
            started = self.now()
            body = bytearray()
            metadata: dict[str, Any] = {
                "request_index": index,
                "endpoint": url,
                "parameters": params,
                "request_started_at": started.isoformat(),
                "http_status": None,
                "headers": {},
                "complete": False,
                "error": None,
                "body_representation": "wire",
            }
            try:
                timeout = max(0.01, min(20.0, self.deadline - self.monotonic()))
                with self.client.stream(
                    "GET",
                    url,
                    headers=headers,
                    params=params,
                    timeout=timeout,
                    follow_redirects=False,
                    auth=None,
                ) as response:
                    metadata["http_status"] = response.status_code
                    metadata["headers"] = {
                        name: response.headers[name]
                        for name in (
                            "date",
                            "content-type",
                            "content-encoding",
                            "etag",
                            "last-modified",
                            "retry-after",
                        )
                        if name in response.headers
                    }
                    if sum(len(v.encode()) for v in metadata["headers"].values()) > 8192:
                        metadata["headers"] = {}
                        raise ProviderError("response_header_budget")
                    if response.is_stream_consumed:
                        metadata["body_representation"] = "httpx_decoded"
                    chunks = (
                        (response.content,) if response.is_stream_consumed else response.iter_raw()
                    )
                    for chunk in chunks:
                        self.guard()
                        remaining = self.plan.max_response_bytes - len(body)
                        body.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            raise ProviderError("response_byte_budget")
                    self.guard()
                    metadata["complete"] = True
            except httpx.TransportError:
                metadata["error"] = "network_transport_error"
            except ProviderError as error:
                metadata["error"] = str(error)
            completed = self.now()
            if completed < started:
                metadata["error"] = "capture_clock_regression"
                metadata["complete"] = False
            metadata["response_completed_at"] = completed.isoformat()
            self.record(metadata, bytes(body))
            self.completed_at = completed
            if metadata["complete"] and metadata["http_status"] == 200:
                return bytes(body)
            status = metadata["http_status"]
            reason = metadata["error"] or f"HTTP_{status}"
            retryable = reason == "network_transport_error" or (
                metadata["complete"] and status in {408, 429, 500, 502, 503, 504}
            )
            if not retryable or index + 1 == self.attempts:
                raise ProviderError(reason)
            delay = max(float(self.plan.interval_seconds), float(2**index))
            retry_after = metadata["headers"].get("retry-after")
            if retry_after is not None:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    raise ProviderError("unsupported_retry_after") from None
            if not 0 <= delay <= 60 or self.monotonic() + delay >= self.deadline:
                raise ProviderError("retry_budget_exhausted")
            self.sleep(delay)
        raise AssertionError("unreachable")
