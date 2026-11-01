"""Read-only Track C operator smoke test against an existing loopback API."""

import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def call(base: str, path: str, method: str = "GET") -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(base + path, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port is None
        or parsed.username or parsed.password
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        parser.error("base-url must point to a loopback HTTP server with an explicit port")
    if len(args.ticker) != 6 or not args.ticker.isdigit():
        parser.error("ticker must have six digits")
    if len(args.sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in args.sha256):
        parser.error("sha256 must be 64 hexadecimal characters")

    original = hashlib.sha256(args.snapshot.read_bytes()).hexdigest()
    if original != args.sha256.lower():
        raise SystemExit("FAIL: snapshot digest does not match approved SHA-256")
    base = args.base_url.rstrip("/")
    cases = (
        ("health", "/api/v1/health", "GET", 200),
        ("summary", "/api/v1/operational/summary", "GET", 200),
        ("catalog", "/api/v1/operational/stocks?offset=0&limit=25", "GET", 200),
        ("ohlcv", f"/api/v1/operational/stocks/{args.ticker}/ohlcv", "GET", 200),
        ("missing", "/api/v1/operational/stocks/999999/ohlcv", "GET", 404),
        ("write_denied", "/api/v1/operational/summary", "POST", 405),
    )
    results: dict[str, dict[str, object]] = {}
    for name, path, method, expected in cases:
        status, payload = call(base, path, method)
        if status != expected:
            raise SystemExit(f"FAIL: {name}: HTTP {status}, expected {expected}")
        results[name] = payload
    if results["summary"].get("evidenceStatus") != "unreviewed":
        raise SystemExit("FAIL: evidence unexpectedly promoted")
    if not results["catalog"].get("items"):
        raise SystemExit("FAIL: empty catalog")
    if results["ohlcv"].get("ticker") != args.ticker or not results["ohlcv"].get("bars"):
        raise SystemExit("FAIL: missing/mismatched OHLCV")
    status, diagnostics = call(base, "/api/v1/operations/krx-scheduler/diagnostics")
    if status == 200:
        if (
            diagnostics.get("operatorConfirmationStillRequired") is not True
            or diagnostics.get("networkRequests") != 0
            or diagnostics.get("schedulerChanged") is not False
            or diagnostics.get("paperExecutionEligible") is not False
        ):
            raise SystemExit("FAIL: unsafe scheduler diagnostics flags")
    elif status != 503:
        raise SystemExit(f"FAIL: diagnostics HTTP {status}; expected 200 or unconfigured 503")
    if hashlib.sha256(args.snapshot.read_bytes()).hexdigest() != original:
        raise SystemExit("FAIL: snapshot mutated")
    print(json.dumps({
        "status": "pass",
        "checks": len(cases) + 1,
        "diagnosticsHTTP": status,
        "snapshotUnchanged": True,
        "evidenceStatus": "unreviewed",
        "mode": "read_only_loopback",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
