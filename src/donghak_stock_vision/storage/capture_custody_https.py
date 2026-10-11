"""Opt-in HTTPS adapter; its responses do not establish trusted custody."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from ipaddress import ip_address
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 65536


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _request(request: Request, timeout: float) -> tuple[int, bytes]:
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
            return response.status, data
    except HTTPError as exc:
        with exc:
            return exc.code, exc.read(MAX_RESPONSE_BYTES + 1)


@dataclass(frozen=True)
class HTTPSCustodyTransport:
    """Provider-defined HTTPS endpoint. No authentication or acceptance implied."""

    endpoint: str
    timeout: float = 10.0
    bearer_token: str | None = field(default=None, repr=False)
    execute: Callable[[Request, float], tuple[int, bytes]] = _request

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or self.endpoint.endswith("/")
        ):
            raise ValueError("endpoint must be a fixed HTTPS URL without credentials")
        if not 0 < self.timeout <= 60:
            raise ValueError("invalid HTTPS timeout")
        if self.bearer_token is not None and re.fullmatch(
            r"[A-Za-z0-9._~+/-]+={0,2}", self.bearer_token
        ) is None:
            raise ValueError("invalid custody bearer token")

    def _call(
        self, method: str, *, raw_id: int, idempotency_key: str, body: bytes | None
    ) -> bytes | None:
        if (
            raw_id <= 0
            or len(idempotency_key) != 64
            or any(char not in "0123456789abcdef" for char in idempotency_key)
        ):
            raise ValueError("invalid custody submission identity")
        url = f"{self.endpoint}/{raw_id}"
        headers = {
            "Idempotency-Key": idempotency_key,
            "Accept": "application/octet-stream",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.bearer_token is not None:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = Request(url, data=body, headers=headers, method=method)
        status, response = self.execute(request, self.timeout)
        if len(response) > MAX_RESPONSE_BYTES:
            raise ValueError("custody response exceeds size limit")
        if method == "GET" and status == 404:
            return None
        if not 200 <= status < 300 or not response:
            raise ValueError("custody endpoint did not provide a valid response")
        return response

    def submit(self, receipt_bytes: bytes, *, raw_id: int, idempotency_key: str) -> bytes:
        result = self._call(
            "PUT", raw_id=raw_id, idempotency_key=idempotency_key, body=receipt_bytes
        )
        if result is None:
            raise ValueError("custody submit returned no response")
        return result

    def lookup(self, *, raw_id: int, idempotency_key: str) -> bytes | None:
        return self._call("GET", raw_id=raw_id, idempotency_key=idempotency_key, body=None)


def create_approved_https_custody_transport(
    endpoint: str,
    *,
    approved_hostname: str,
    timeout: float = 10.0,
    bearer_token: str | None = None,
    execute: Callable[[Request, float], tuple[int, bytes]] = _request,
) -> HTTPSCustodyTransport:
    """Require an independently configured exact hostname before contacting a custodian.

    An approval string must be supplied by the operator, not derived from the URL.
    This prevents accidental destination substitution, not DNS or TLS compromise.
    """
    transport = HTTPSCustodyTransport(
        endpoint, timeout=timeout, bearer_token=bearer_token, execute=execute
    )
    parsed = urlsplit(endpoint)
    hostname = parsed.hostname
    if (
        not approved_hostname
        or approved_hostname != approved_hostname.lower()
        or hostname != approved_hostname
        or parsed.port not in (None, 443)
        or hostname == "localhost"
        or hostname.endswith(".localhost")
        or "." not in hostname
    ):
        raise ValueError("custody endpoint does not match approved public hostname")
    try:
        ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("custody endpoint IP literals are not approved")
    return transport
