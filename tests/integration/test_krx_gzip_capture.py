"""Synthetic KRX-shaped gzip and sealed legacy recovery; never provider requests."""

import gzip
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_krx_operator_policy import package
from tests.integration.test_krx_pilot_readiness import capture, market_body

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.providers.base import ProviderError
from donghak_stock_vision.providers.forward_capture import CaptureHTTP
from donghak_stock_vision.providers.http import RequestGate
from donghak_stock_vision.providers.krx_response import decode_response
from donghak_stock_vision.storage.forward_capture import DB, readback, verify_capture
from donghak_stock_vision.storage.krx_capture_recovery import recover_capture, verify_recovery


class LegacyCaptureHTTP(CaptureHTTP):
    """Produce original pre-fix metadata to test sealed-v1 failure replay."""

    def __init__(
        self,
        client: httpx.Client,
        gate: RequestGate,
        plan: ForwardCapturePlan,
        record: Callable[[dict[str, Any], bytes], None],
        **kwargs: Any,
    ) -> None:
        def legacy_record(metadata: dict[str, Any], body: bytes) -> None:
            record({k: v for k, v in metadata.items() if k != "body_representation"}, body)

        super().__init__(client, gate, plan, legacy_record, **kwargs)


def collect(
    tmp_path: Path,
    transform: Callable[[bytes], bytes] = lambda b: b,
    *,
    encoding: str = "",
    consumed: bool = False,
    legacy: bool = False,
    payload_change: Callable[[dict[str, Any]], None] = lambda _: None,
    rows: int = 3,
    interrupted: bool = False,
) -> tuple[Path, ForwardCapturePlan, FrozenJSON, FrozenJSON, bytes]:
    plan, ref, policy = package(tmp_path)
    if rows == 940:
        plan = replace(
            plan,
            max_response_bytes=2 * 1024 * 1024,
            authorization_reference=None,
            terms_reference=None,
        )
        policy = FrozenJSON.freeze(
            policy.to_dict()
            | {
                "captures": [{"plan": plan.document.to_dict(), "attempt": "a"}],
            }
        )
        (Path(ref.to_dict()["root"]) / "operator-policy.json").write_text(policy.payload_json)
        ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": policy.identifier})
        plan = replace(
            plan,
            authorization_reference=policy.identifier,
            terms_reference=policy.to_dict()["terms"]["sha256"],
        )
    payload = market_body(plan)
    if rows == 940:
        base = payload["OutBlock_1"][1]
        payload["OutBlock_1"] = [base | {"ISU_CD": f"{i:06d}"} for i in range(1, 940)] + [base]
    payload_change(payload)
    raw = transform(json.dumps(payload).encode())
    root = tmp_path / "capture"
    calls = []

    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield raw[: len(raw) // 2]
            raise httpx.ReadTimeout("synthetic interruption")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert dict(request.url.params) == {"basDd": plan.day.strftime("%Y%m%d")}
        headers = {"content-encoding": encoding} if encoding else {}
        return (
            httpx.Response(200, headers=headers, content=raw)
            if consumed
            else httpx.Response(
                200,
                headers=headers,
                stream=InterruptedStream() if interrupted else httpx.ByteStream(raw),
            )
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        patch("socket.socket.connect", side_effect=AssertionError("external network forbidden")),
        patch(
            "donghak_stock_vision.storage.forward_capture.CaptureHTTP",
            LegacyCaptureHTTP if legacy else CaptureHTTP,
        ),
    ):
        owner = capture(root, plan, ref, client)
    assert len(calls) == 1
    return root, plan, ref, owner, raw


def zipped(body: bytes) -> bytes:
    return gzip.compress(body, mtime=0)


@pytest.mark.parametrize("encoding,consumed", [("", False), ("gzip", False), ("gzip", True)])
def test_plain_gzip_and_already_decoded_httpx(
    tmp_path: Path, encoding: str, consumed: bool
) -> None:
    root, plan, _, owner, raw = collect(
        tmp_path, zipped if encoding else lambda b: b, encoding=encoding, consumed=consumed
    )
    d = owner.to_dict()
    assert d["parser"] == "KRXProvider-daily-unadjusted-v2"
    r = d["responses"][0]
    retained = gzip.decompress(raw) if consumed else raw
    assert readback(root, plan, "a", owner.identifier, 0) == retained
    assert r["receipt"]["rawSha256"] == hashlib.sha256(retained).hexdigest()
    assert r["metadata"]["body_representation"] == ("httpx_decoded" if consumed else "wire")
    assert d["ingestion"] == [["success", 1, None]]
    assert verify_capture(root, plan, "a", owner.identifier) == owner
    with closing(sqlite3.connect(root / DB)) as con:
        assert con.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1


def test_synthetic_940_row_market_original_preserved(tmp_path: Path) -> None:
    root, plan, ref, owner, raw = collect(tmp_path, zipped, encoding="gzip", rows=940)
    assert readback(root, plan, "a", owner.identifier, 0) == raw
    artifact = recover_capture(root, plan, "a", owner.identifier, ref)
    assert artifact.to_dict()["market_row_count"] == 940
    assert len(artifact.to_dict()["observations"]) == 1
    assert artifact.to_dict()["observations"][0]["ticker"] == "005930"


@pytest.mark.parametrize(
    "damage", ["garbage", "truncated", "crc", "bomb", "json", "encoding", "headerless"]
)
def test_invalid_compression_and_content_never_ingested(tmp_path: Path, damage: str) -> None:
    def transform(body: bytes) -> bytes:
        if damage == "garbage":
            return b"not gzip"
        if damage == "truncated":
            return zipped(body)[:-8]
        if damage == "crc":
            raw = zipped(body)
            return raw[:-8] + bytes([raw[-8] ^ 1]) + raw[-7:]
        if damage == "bomb":
            return zipped(b"a" * (2 * 1024 * 1024 + 1))
        if damage == "json":
            return zipped(b"invalid JSON")
        return zipped(body)

    encoding = "br" if damage == "encoding" else "" if damage == "headerless" else "gzip"
    root, plan, ref, owner, raw = collect(tmp_path, transform, encoding=encoding)
    assert owner.to_dict()["ingestion"] == []
    assert owner.to_dict()["responses"][0]["parse_error"] == "invalid_or_missing_scoped_bar"
    assert readback(root, plan, "a", owner.identifier, 0) == raw
    with pytest.raises((ValueError, ProviderError)):
        recover_capture(root, plan, "a", owner.identifier, ref)


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate", "date", "market", "field", "outblock", "code"]
)
def test_scoped_schema_fail_closed(tmp_path: Path, damage: str) -> None:
    def change(payload: dict[str, Any]) -> None:
        rows = payload["OutBlock_1"]
        if damage == "missing":
            payload["OutBlock_1"] = [r for r in rows if r["ISU_CD"] != "005930"]
        elif damage == "duplicate":
            rows.append(rows[1].copy())
        elif damage == "date":
            rows[1]["BAS_DD"] = "20250702"
        elif damage == "market":
            rows[1]["MKT_NM"] = "KOSDAQ"
        elif damage == "field":
            del rows[1]["TDD_CLSPRC"]
        elif damage == "code":
            rows[1]["ISU_CD"] = 5930
        else:
            payload["OutBlock_1"] = {}

    root, plan, ref, owner, _ = collect(tmp_path, zipped, encoding="gzip", payload_change=change)
    assert owner.to_dict()["ingestion"] == []
    with pytest.raises((ValueError, ProviderError)):
        recover_capture(root, plan, "a", owner.identifier, ref)


def test_encoded_decoded_budgets_and_no_sniffing() -> None:
    data = b"a" * 10000
    raw = zipped(data)
    with pytest.raises(ProviderError, match="encoded_body_budget"):
        decode_response(raw, "gzip", "wire", len(raw) - 1)
    with pytest.raises(ProviderError, match="decoded_body_budget"):
        decode_response(raw, "gzip", "wire", 1000)
    assert decode_response(raw, "gzip", "wire", len(data)) == data
    assert decode_response(raw, "", "wire", 1000) == raw
    with pytest.raises(ProviderError, match="unknown_krx_body_representation"):
        decode_response(raw, "gzip", "guess", 1000)


def test_sealed_legacy_failure_stays_failed_recovery_is_separate(tmp_path: Path) -> None:
    root, plan, ref, owner, raw = collect(tmp_path, zipped, encoding="gzip", legacy=True)
    assert owner.to_dict()["parser"] == "KRXProvider-daily-unadjusted-v1"
    assert owner.to_dict()["ingestion"] == []
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
        artifact = recover_capture(root, plan, "a", owner.identifier, ref)
        assert recover_capture(root, plan, "a", owner.identifier, ref) == artifact
        verify_recovery(artifact, artifact.identifier, root, plan, "a", owner.identifier, ref)
        assert verify_capture(root, plan, "a", owner.identifier) == owner
        assert readback(root, plan, "a", owner.identifier, 0) == raw
    assert artifact.to_dict()["original_ingestion"] == []
    assert artifact.to_dict()["original_parse_error"] == "invalid_or_missing_scoped_bar"
    assert artifact.to_dict()["recovery_status"] == "derived_observation_only"
    assert not any(
        artifact.to_dict()[k]
        for k in ("forward_signal_eligible", "paper_execution_eligible", "pit_qualified")
    )
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    tampered = artifact.to_dict()
    tampered["observations"][0]["close"] += 1
    forged = FrozenJSON.freeze(tampered)
    with pytest.raises(ValueError, match="recovery_semantic_mismatch"):
        verify_recovery(forged, forged.identifier, root, plan, "a", owner.identifier, ref)
    with pytest.raises(ValueError, match="capture_pin_mismatch"):
        recover_capture(root, plan, "a", "0" * 64, ref)
    bad = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": "0" * 64})
    with pytest.raises(ValueError, match="operator_policy_pin_mismatch"):
        recover_capture(root, plan, "a", owner.identifier, bad)


def test_raw_receipt_tampering_blocks_recovery(tmp_path: Path) -> None:
    root, plan, ref, owner, _ = collect(tmp_path, zipped, encoding="gzip", legacy=True)
    with closing(sqlite3.connect(root / DB)) as con:
        con.execute("UPDATE raw_pages SET body=?", (b"tampered original",))
        con.commit()
    with pytest.raises((ValueError, ProviderError)):
        recover_capture(root, plan, "a", owner.identifier, ref)


def test_sealed_legacy_plain_success_remains_compatible(tmp_path: Path) -> None:
    root, plan, _, owner, raw = collect(tmp_path, legacy=True)
    before = (root / DB).read_bytes()
    assert owner.to_dict()["parser"] == "KRXProvider-daily-unadjusted-v1"
    assert owner.to_dict()["ingestion"] == [["success", 1, None]]
    assert verify_capture(root, plan, "a", owner.identifier) == owner
    assert readback(root, plan, "a", owner.identifier, 0) == raw
    assert (root / DB).read_bytes() == before


def test_interrupted_wire_response_cannot_be_recovered_as_complete(tmp_path: Path) -> None:
    root, plan, ref, owner, raw = collect(tmp_path, zipped, encoding="gzip", interrupted=True)
    r = owner.to_dict()["responses"][0]
    assert not r["metadata"]["complete"]
    assert r["metadata"]["error"] == "network_transport_error"
    assert readback(root, plan, "a", owner.identifier, 0) == raw[: len(raw) // 2]
    with pytest.raises(ValueError, match="complete_successful_http_response_required"):
        recover_capture(root, plan, "a", owner.identifier, ref)


def test_cli_recovery_private_exclusive_no_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, plan, ref, owner, _ = collect(tmp_path, zipped, encoding="gzip", legacy=True)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.document.payload_json)
    output = tmp_path / "recovery.json"
    argv = [
        "forward-capture",
        "recover",
        "--root",
        str(root),
        "--plan",
        str(plan_file),
        "--plan-pin",
        plan.document.identifier,
        "--pin",
        owner.identifier,
        "--attempt",
        "a",
        "--authorization-policy",
        "operator_read_only_pilot_v1",
        "--authorization-root",
        ref.to_dict()["root"],
        "--authorization-pin",
        ref.to_dict()["grant_pin"],
        "--output",
        str(output),
    ]
    before = (root / DB).read_bytes()
    with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
        assert main(argv) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["network_requests"] == 0 and result["derived_observations"] == 1
        assert output.stat().st_mode & 0o077 == 0
        assert main(argv) != 0  # Existing derived output must not be overwritten.
        assert main(argv + ["--allow-network"]) != 0
    assert (root / DB).read_bytes() == before
