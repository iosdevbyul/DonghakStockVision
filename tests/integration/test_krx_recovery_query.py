"""Verified read-only query; all provider records and authorization documents are synthetic."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.integration.test_forward_capture_scope import NOW, scoped_package
from tests.integration.test_krx_gzip_capture import collect, zipped
from tests.integration.test_krx_operator_policy import record

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.krx_capture_recovery import recover_capture


def prepared(tmp: Path, *, legacy: bool = True, v2: bool = False) -> tuple[list[str], FrozenJSON]:
    if v2:
        plan, ref, _ = scoped_package(tmp)
        root = tmp / "capture"
        with patch("tests.integration.test_krx_operator_policy.NOW", NOW):
            owner = record(root, plan, ref)
    else:
        root, plan, ref, owner, _ = collect(tmp, zipped, encoding="gzip", legacy=legacy)
    artifact = recover_capture(root, plan, "a", owner.identifier, ref)
    recovery = tmp / "recovery.json"
    recovery.write_text(artifact.payload_json)
    plan_file = tmp / "plan.json"
    plan_file.write_text(plan.document.payload_json)
    (tmp / "recovery.pin").write_text(artifact.identifier)
    argv = [
        "forward-capture",
        "inspect-recovery",
        "--root",
        str(root),
        "--plan",
        str(plan_file),
        "--plan-pin",
        plan.document.identifier,
        "--attempt",
        "a",
        "--pin",
        owner.identifier,
        "--authorization-policy",
        "operator_read_only_pilot_v1",
        "--authorization-root",
        ref.to_dict()["root"],
        "--authorization-pin",
        ref.to_dict()["grant_pin"],
        "--recovery",
        str(recovery),
        "--recovery-pin",
        artifact.identifier,
    ]
    return argv, artifact


def snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("legacy,v2", [(True, False), (False, False), (False, True)])
def test_verified_query_exact_values_stable_private_readonly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], legacy: bool, v2: bool
) -> None:
    with patch("socket.socket.connect", side_effect=AssertionError("no network")):
        argv, artifact = prepared(tmp_path, legacy=legacy, v2=v2)
        before = snapshot(tmp_path)
        assert main(argv) == 0
        raw = capsys.readouterr().out
        result = json.loads(raw)
        assert main(argv) == 0
        assert capsys.readouterr().out == raw
    assert snapshot(tmp_path) == before
    d = artifact.to_dict()
    assert result["version"] == "krx_recovered_observation_query_v1"
    assert result["observation_count"] == 1
    assert result["observation"] == d["observations"][0]
    assert {k: result["observation"][k] for k in ("open", "high", "low", "close", "volume")} == {
        "open": 100,
        "high": 110,
        "low": 90,
        "close": 105,
        "volume": 10,
    }
    assert result["basDd"] == ("20261026" if v2 else "20250701")
    assert result["original_capture"] == {
        "ingestion": [] if legacy else [["success", 1, None]],
        "parse_error": "invalid_or_missing_scoped_bar" if legacy else None,
        "changed": False,
    }
    assert result["provenance"] == {
        "capture_attempt": "a",
        "receipt_id": d["source_receipt"]["raw_id"],
        "origin": "authorized_provider_mock_fixture",
        "response_at": d["source_response_at"],
        "capture_parser": d["original_parser"],
        "recovery_parser": d["parser"],
        "recovery_version": "krx_capture_recovery_v1",
        "verification": "original_source_semantic_replay",
    }
    assert result["recovery_status"] == "derived_observation_only"
    assert result["network_requests"] == 0
    assert all(
        result[k] is False
        for k in (
            "pit_qualified",
            "forward_signal_eligible",
            "paper_execution_eligible",
            "execution_authorized",
        )
    )
    assert str(tmp_path) not in raw
    assert not re.search(r"[a-f0-9]{64}", raw)
    assert set(result) == {
        "version",
        "basDd",
        "observation_count",
        "observation",
        "provenance",
        "original_capture",
        "recovery_status",
        "network_requests",
        "pit_qualified",
        "forward_signal_eligible",
        "paper_execution_eligible",
        "execution_authorized",
    }


@pytest.mark.parametrize(
    "damage",
    [
        "recovery_pin",
        "artifact",
        "rehashed_artifact",
        "eligibility",
        "original",
        "plan_pin",
        "capture_pin",
        "authorization_pin",
        "plan",
        "missing",
        "json",
        "utf8",
        "duplicate_keys",
        "oversized",
    ],
)
def test_invalid_query_rejected_without_output_or_source_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], damage: str
) -> None:
    argv, artifact = prepared(tmp_path)
    recovery = tmp_path / "recovery.json"
    if damage in ("recovery_pin", "plan_pin", "capture_pin", "authorization_pin"):
        flag = {
            "recovery_pin": "--recovery-pin",
            "plan_pin": "--plan-pin",
            "capture_pin": "--pin",
            "authorization_pin": "--authorization-pin",
        }[damage]
        argv[argv.index(flag) + 1] = "0" * 64
    elif damage in ("artifact", "rehashed_artifact", "eligibility"):
        d = artifact.to_dict()
        if damage == "eligibility":
            d["paper_execution_eligible"] = True
        else:
            d["observations"][0]["close"] += 1
        forged = FrozenJSON.freeze(d)
        recovery.write_text(forged.payload_json)
        if damage != "artifact":
            argv[argv.index("--recovery-pin") + 1] = forged.identifier
    elif damage == "original":
        with closing(sqlite3.connect(tmp_path / "capture" / DB)) as con:
            con.execute("UPDATE raw_pages SET body=?", (b"altered",))
            con.commit()
    elif damage == "plan":
        plan_file = tmp_path / "plan.json"
        d = json.loads(plan_file.read_text()) | {"ticker": "000660"}
        owner = FrozenJSON.freeze(d)
        plan_file.write_text(owner.payload_json)
        argv[argv.index("--plan-pin") + 1] = owner.identifier
    elif damage == "missing":
        recovery.unlink()
    elif damage == "oversized":
        recovery.write_bytes(b" " * 32769)
    elif damage == "utf8":
        recovery.write_bytes(b"\xff")
    elif damage == "duplicate_keys":
        recovery.write_text('{"version":1,"version":2}')
    else:
        recovery.write_text("not JSON")
    before = snapshot(tmp_path)
    with patch("socket.socket.connect", side_effect=AssertionError("no network")):
        assert main(argv) == 2
    assert not capsys.readouterr().out
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "flag", ["--recovery", "--recovery-pin", "--pin", "--authorization-pin", "--plan-pin"]
)
def test_missing_required_reference(tmp_path: Path, flag: str) -> None:
    argv, _ = prepared(tmp_path)
    i = argv.index(flag)
    del argv[i : i + 2]
    before = snapshot(tmp_path)
    assert main(argv) == 2
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "flag", ["--allow-network", "--confirm-operator-authorization", "--output"]
)
def test_inspection_cannot_enable_network_or_output_writes(tmp_path: Path, flag: str) -> None:
    argv, _ = prepared(tmp_path)
    argv += [flag] if flag != "--output" else [flag, str(tmp_path / "not-created.json")]
    before = snapshot(tmp_path)
    with patch("socket.socket.connect", side_effect=AssertionError("no network")):
        assert main(argv) == 2
    assert snapshot(tmp_path) == before
