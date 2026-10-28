"""Synthetic capture-to-store integration. No private pilot imports or live requests."""

import gzip
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_forward_capture import Clock, execute
from tests.integration.test_krx_forward_authorization import KEY, permission_fixture
from tests.integration.test_market_collection import row

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.forward_capture_cli import offline_plan
from donghak_stock_vision.providers.base import ProviderError
from donghak_stock_vision.storage.forward_capture import DB as CAPTURE_DB
from donghak_stock_vision.storage.operational_capture import ingest_capture
from donghak_stock_vision.storage.operational_market import (
    DB,
    OperationalMarketStore,
    StorageLimits,
)


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def captured(
    tmp: Path,
    *,
    zipped: bool = False,
    count: int = 940,
    changes: dict[str, Any] | None = None,
    status: int = 200,
    raw: bytes | None = None,
) -> tuple[Path, FrozenJSON]:
    plan = replace(offline_plan(), max_response_bytes=2 * 1024**2)
    rows = [row(plan.ticker, plan.day, ISU_NM="Synthetic Samsung")]
    rows += [row(f"{i:06d}", plan.day, ISU_NM=f"Synthetic {i}") for i in range(count - 1)]
    for r in rows:
        r.update(changes or {})
    body = raw if raw is not None else json.dumps({"OutBlock_1": rows}).encode()
    headers = {"Content-Encoding": "gzip"} if zipped else {}
    if zipped:
        body = gzip.compress(body, mtime=0)
    source = tmp / "capture"
    transport = httpx.MockTransport(lambda _: httpx.Response(status, content=body, headers=headers))
    return source, execute(source, plan, transport=transport)


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    root = tmp_path / "operational"
    OperationalMarketStore.initialize(root, StorageLimits(min_free_bytes=0))
    return root


def ingest(root: Path, source: Path, owner: FrozenJSON, **kwargs: Any) -> FrozenJSON:
    from donghak_stock_vision.data.forward_capture import ForwardCapturePlan

    doc = owner.to_dict()
    plan = ForwardCapturePlan.from_dict(doc["plan"])
    return ingest_capture(
        root,
        source,
        plan,
        kwargs.pop("plan_pin", plan.document.identifier),
        doc["attempt"],
        kwargs.pop("capture_pin", owner.identifier),
        allow_synthetic_fixture=kwargs.pop("allow_synthetic_fixture", True),
        **kwargs,
    )


@pytest.mark.parametrize("zipped", [False, True])
def test_full_market_verified_idempotent_and_original_read_only(
    tmp_path: Path, destination: Path, zipped: bool
) -> None:
    source, owner = captured(tmp_path, zipped=zipped)
    before = snapshot(source)
    with patch("httpx.Client.send", side_effect=AssertionError("network forbidden")):
        result = ingest(destination, source, owner)
        assert ingest(destination, source, owner) == result
    assert snapshot(source) == before
    assert result.to_dict()["observation_count"] == 940
    assert result.to_dict()["source_origin"] == "offline_fixture"
    assert result.to_dict()["network_requests"] == 0
    assert result.to_dict()["verification"] == "original_capture_semantic_replay"
    assert not any(result.to_dict()["eligibility"].values())
    with closing(OperationalMarketStore(destination)) as store:
        assert store.validate().to_dict()["valid"]
        assert store.status().to_dict()["observation_count"] == 940
        assert store.status().to_dict()["batch_count"] == 1
        batch = FrozenJSON(
            store.connection.execute("SELECT document FROM ingestion_batches").fetchone()[0]
        )
        assert batch.to_dict()["version"] == "operational_krx_batch_v2"
        link = batch.to_dict()["request"]["source_capture"]
        assert link["capture_pin"] == owner.identifier
        assert (
            link["receipt_pin"]
            == FrozenJSON.freeze(owner.to_dict()["responses"][0]["receipt"]).identifier
        )
        assert link["plan_pin"] == FrozenJSON.freeze(owner.to_dict()["plan"]).identifier
        assert link["authorization_pin"] is None
        normalized, name = store.connection.execute(
            "SELECT payload,reported_name FROM market_observations WHERE ticker='005930'"
        ).fetchone()
        assert name == "Synthetic Samsung"
        bar = json.loads(normalized)
        assert (
            bar["open"],
            bar["high"],
            bar["low"],
            bar["close"],
            bar["volume"],
            bar["trading_value"],
        ) == (100, 110, 90, 105, 10, 1020)
        assert "bars" not in store.status().to_dict()["tables"]


@pytest.mark.parametrize("argument", ["plan_pin", "capture_pin"])
def test_incorrect_pins_leave_destination_unchanged(
    tmp_path: Path, destination: Path, argument: str
) -> None:
    source, owner = captured(tmp_path)
    before = snapshot(destination)
    with pytest.raises(ValueError, match="pin_mismatch"):
        ingest(destination, source, owner, **{argument: "0" * 64})
    assert snapshot(destination) == before


@pytest.mark.parametrize(
    "mutation", ["body", "receipt", "metadata", "manifest", "missing_manifest"]
)
def test_tampered_original_rejected_even_after_completed_import(
    tmp_path: Path, destination: Path, mutation: str
) -> None:
    source, owner = captured(tmp_path)
    ingest(destination, source, owner)
    before = snapshot(destination)
    with closing(sqlite3.connect(source / CAPTURE_DB)) as c:
        # Simulate offline owner tampering beyond the normal immutable SQL API.
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')
        if mutation == "body":
            c.execute("UPDATE raw_pages SET body=?", (b"{}",))
        elif mutation == "receipt":
            c.execute("UPDATE raw_pages SET collected_at='2025-07-03T00:00:00+00:00'")
        elif mutation == "metadata":
            c.execute("UPDATE forward_responses SET metadata='{}'")
        elif mutation == "manifest":
            c.execute("UPDATE forward_manifests SET document='{}'")
        else:
            c.execute("DELETE FROM forward_manifests")
        c.commit()
    with pytest.raises((ValueError, KeyError)):
        ingest(destination, source, owner)
    assert snapshot(destination) == before


@pytest.mark.parametrize(
    "changes,raw,status",
    [
        ({"BAS_DD": "20250702"}, None, 200),
        ({"MKT_NM": "KOSDAQ"}, None, 200),
        ({"ISU_CD": "005930"}, None, 200),
        ({"TDD_CLSPRC": "-1"}, None, 200),
        ({}, b'{"OutBlock_1":{}}', 200),
        ({}, b"not-json", 200),
        ({}, None, 403),
        ({}, None, 500),
    ],
)
def test_invalid_full_response_not_partially_imported(
    tmp_path: Path, destination: Path, changes: dict[str, Any], raw: bytes | None, status: int
) -> None:
    source, owner = captured(tmp_path, count=2, changes=changes, raw=raw, status=status)
    before = snapshot(destination)
    with pytest.raises((ValueError, ProviderError)):
        ingest(destination, source, owner)
    assert snapshot(destination) == before


def test_conflict_rolls_back_all_new_market_rows(tmp_path: Path, destination: Path) -> None:
    source, owner = captured(tmp_path / "one", count=1)
    ingest(destination, source, owner)
    second, owner2 = captured(tmp_path / "two", changes={"TDD_CLSPRC": "106"})
    before = snapshot(destination)
    with pytest.raises(ValueError, match="conflicting_observation"):
        ingest(destination, second, owner2)
    assert snapshot(destination) == before


def test_interrupted_write_rolls_back(tmp_path: Path, destination: Path) -> None:
    source, owner = captured(tmp_path)
    before = snapshot(destination)
    with patch.object(OperationalMarketStore, "_manifest", side_effect=RuntimeError("interrupted")):
        with pytest.raises(RuntimeError, match="interrupted"):
            ingest(destination, source, owner)
    assert snapshot(destination) == before
    assert ingest(destination, source, owner).to_dict()["observation_count"] == 940


def test_fixture_acknowledgement_and_distinct_root_required(
    tmp_path: Path, destination: Path
) -> None:
    source, owner = captured(tmp_path)
    with pytest.raises(ValueError, match="acknowledgement"):
        ingest(destination, source, owner, allow_synthetic_fixture=False)
    with pytest.raises(ValueError, match="separate_operational"):
        ingest(source / "nested", source, owner)
    with pytest.raises(ValueError, match="unknown_response"):
        ingest(destination, source, owner, sequence=1)


def test_authenticated_mock_cannot_expand_real_plan_scope(
    tmp_path: Path, destination: Path
) -> None:
    base = replace(offline_plan(), mode="authorized_provider", max_response_bytes=2 * 1024**2)
    plans, auth, trust = permission_fixture(tmp_path, [base])
    plan = plans[0]
    rows = [row(plan.ticker, plan.day), row("000660", plan.day)]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"OutBlock_1": rows}))
    source = tmp_path / "capture"
    owner = execute(
        source,
        plan,
        transport=transport,
        clock=Clock(),
        api_key=KEY,
        allow_network=True,
        authorization=auth,
        authorization_trust=trust,
    )
    before = snapshot(destination)
    with pytest.raises(ValueError, match="full_market_ingestion_not_authorized"):
        ingest(
            destination,
            source,
            owner,
            authorization=auth,
            trust=trust,
            allow_synthetic_fixture=False,
        )
    with pytest.raises(ValueError, match="cannot_be_relabelled"):
        ingest(destination, source, owner, authorization=auth, trust=trust)
    with pytest.raises(ValueError, match="original_authorization_required"):
        ingest(destination, source, owner, allow_synthetic_fixture=False)
    assert snapshot(destination) == before


def test_cli_offline_explicit_source_and_pins(
    tmp_path: Path, destination: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, owner = captured(tmp_path)
    plan = FrozenJSON.freeze(owner.to_dict()["plan"])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.payload_json)
    args = [
        "market-storage",
        "ingest-capture",
        "--root",
        str(destination),
        "--capture-root",
        str(source),
        "--plan",
        str(plan_path),
        "--plan-pin",
        plan.identifier,
        "--capture-pin",
        owner.identifier,
        "--attempt",
        "a",
        "--allow-synthetic-fixture",
    ]
    with patch("httpx.Client.send", side_effect=AssertionError("network forbidden")):
        assert main(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["observation_count"] == 940
        assert main(args) == 0
        assert json.loads(capsys.readouterr().out) == result
    assert main(["market-storage", "ingest-capture", "--root", str(destination)]) == 2
    assert main(["market-storage", "ingest-capture"]) == 2


def test_tampered_local_reference_detected(tmp_path: Path, destination: Path) -> None:
    source, owner = captured(tmp_path)
    ingest(destination, source, owner)
    with closing(sqlite3.connect(destination / DB)) as c:
        trigger = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='ingestion_batches_immutable_update'"
        ).fetchone()[0]
        c.execute("DROP TRIGGER ingestion_batches_immutable_update")
        doc = json.loads(c.execute("SELECT document FROM ingestion_batches").fetchone()[0])
        doc["request"]["source_capture"]["raw_sha256"] = "0" * 64
        altered = FrozenJSON.freeze(doc)
        c.execute(
            "UPDATE ingestion_batches SET document=?,fingerprint=?,request_hash=?",
            (
                altered.payload_json,
                altered.identifier,
                FrozenJSON.freeze(doc["request"]).identifier,
            ),
        )
        c.execute(trigger)
        c.commit()
    with closing(OperationalMarketStore(destination)) as store:
        assert not store.validate().to_dict()["valid"]


@pytest.mark.parametrize("damage", ["none", "pin", "trust"])
def test_authorization_reverified_for_scoped_provider_mock(
    tmp_path: Path, destination: Path, damage: str
) -> None:
    plans, auth, trust = permission_fixture(
        tmp_path, [replace(offline_plan(), mode="authorized_provider")]
    )
    plan = plans[0]
    source = tmp_path / "capture"
    owner = execute(
        source, plan, api_key=KEY, allow_network=True, authorization=auth, authorization_trust=trust
    )
    before = snapshot(destination)
    if damage == "pin":
        auth = FrozenJSON.freeze(auth.to_dict() | {"grant_pin": "0" * 64})
    if damage == "trust":
        trust = FrozenJSON.freeze(trust.to_dict() | {"registry_sha256": "0" * 64})
    if damage != "none":
        with pytest.raises(ValueError):
            ingest(
                destination,
                source,
                owner,
                authorization=auth,
                trust=trust,
                allow_synthetic_fixture=False,
            )
        assert snapshot(destination) == before
    else:
        result = ingest(
            destination,
            source,
            owner,
            authorization=auth,
            trust=trust,
            allow_synthetic_fixture=False,
        )
        assert result.to_dict()["observation_count"] == 1
        assert result.to_dict()["source_origin"] == "authorized_provider_mock_fixture"
        assert not any(result.to_dict()["eligibility"].values())


def test_cancelled_capture_without_response_not_imported(tmp_path: Path, destination: Path) -> None:
    plan = offline_plan()
    source = tmp_path / "capture"
    owner = execute(source, cancelled=lambda: True)
    before = snapshot(destination)
    with pytest.raises(ValueError, match="unknown_response"):
        ingest(destination, source, owner)
    assert snapshot(destination) == before
    assert plan.mode == "offline_fixture"


def test_protected_partition_rejected_before_source_access(
    tmp_path: Path, destination: Path
) -> None:
    plan = offline_plan()
    object.__setattr__(plan, "partition", "final_test")
    with patch(
        "donghak_stock_vision.storage.operational_capture.verify_capture",
        side_effect=AssertionError("protected source read"),
    ):
        with pytest.raises(ValueError, match="forbidden_partition"):
            ingest_capture(destination, tmp_path / "missing", plan, "0" * 64, "a", "0" * 64)


def test_operator_v2_scope_replay_keeps_restrictions(tmp_path: Path, destination: Path) -> None:
    from tests.integration.test_forward_capture_scope import NOW, scoped_package
    from tests.integration.test_krx_operator_policy import record

    plan, auth, _ = scoped_package(tmp_path)
    source = tmp_path / "capture"
    with patch("tests.integration.test_krx_operator_policy.NOW", NOW):
        owner = record(source, plan, auth)
    before = snapshot(source)
    result = ingest_capture(
        destination,
        source,
        plan,
        plan.document.identifier,
        "a",
        owner.identifier,
        authorization=auth,
        authorization_policy="operator_read_only_pilot_v1",
    )
    assert result.to_dict()["observation_count"] == 1
    assert not any(result.to_dict()["eligibility"].values())
    assert snapshot(source) == before
