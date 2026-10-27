"""Prospective collection scopes; synthetic documents/transport only, never KRX calls."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_krx_forward_authorization import KEY
from tests.integration.test_krx_operator_policy import package, record

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan, ForwardObservationScope
from donghak_stock_vision.forward_capture_cli import offline_plan
from donghak_stock_vision.storage.forward_capture import (
    DB,
    readback,
    run_capture,
    verify_capture,
    verify_operator_capture_authorization,
)
from donghak_stock_vision.storage.forward_signal import POLICY, request_contract
from donghak_stock_vision.storage.krx_authorization import (
    OPERATOR,
    operator_readiness,
    verify_authorization,
    verify_operator_authorization,
)

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def scoped_package(tmp: Path) -> tuple[ForwardCapturePlan, FrozenJSON, FrozenJSON]:
    old, ref, policy = package(tmp)
    d = policy.to_dict()
    d.update(
        valid_from=NOW.isoformat(),
        valid_until=(NOW + timedelta(hours=1)).isoformat(),
        reviewed_at=NOW.isoformat(),
        approved_from="2026-09-29",
        approved_through="2027-09-28",
    )
    plan = replace(
        old,
        day=date(2026, 10, 26),
        partition="forward_observation",
        split=None,
        split_pin=None,
        observation_scope=ForwardObservationScope(
            datetime.fromisoformat(d["valid_from"]), datetime.fromisoformat(d["valid_until"])
        ),
        authorization_reference=None,
    )
    d["captures"] = [dict(plan=replace(plan, terms_reference=None).document.to_dict(), attempt="a")]
    policy = FrozenJSON.freeze(d)
    root = Path(ref.to_dict()["root"])
    (root / "operator-policy.json").write_text(policy.payload_json)
    ref = FrozenJSON.freeze(dict(root=str(root), grant_pin=policy.identifier))
    return replace(plan, authorization_reference=policy.identifier), ref, policy


def test_v1_bytes_pin_and_research_validation_remain_unchanged() -> None:
    plan = offline_plan()
    assert (
        plan.document.identifier
        == "48bdf39e3a3b3f6274be7def9303644a09abe9fd258088cc3bd7bf86a54f9ee4"
    )
    assert "observation_scope" not in plan.document.to_dict()
    assert ForwardCapturePlan.from_dict(plan.document.to_dict()) == plan
    assert plan.split is not None
    with pytest.raises(ValueError, match="outside_authorized_partition"):
        replace(plan, day=plan.split.final_test.start)


def test_v2_roundtrip_and_no_research_split(tmp_path: Path) -> None:
    plan, ref, _ = scoped_package(tmp_path)
    assert ForwardCapturePlan.from_dict(plan.document.to_dict()) == plan
    assert plan.document.to_dict()["version"] == "forward_capture_plan_v2"
    assert not {"split", "split_pin", "partition"}.intersection(plan.document.to_dict())
    assert verify_operator_authorization(plan, "a", ref, None, NOW)


@pytest.mark.parametrize("partition", ["training", "tuning", "final_test", "oos", "final_holdout"])
def test_no_partition_relabel_or_fallback(tmp_path: Path, partition: str) -> None:
    plan, _, _ = scoped_package(tmp_path)
    with pytest.raises(ValueError, match="forward_scope_cannot_relabel_research_partition"):
        replace(plan, partition=partition)
    with pytest.raises(ValueError, match="forward_scope_cannot_relabel_research_partition"):
        replace(plan, split=offline_plan().split)
    doc = plan.document.to_dict() | {"partition": partition}
    with pytest.raises(ValueError, match="plan_fields"):
        ForwardCapturePlan.from_dict(doc)


@pytest.mark.parametrize("day", [date(2024, 7, 1), date(2025, 6, 30), date(2026, 10, 28)])
def test_historical_protected_or_future_dates_not_recent_forward_dates(
    tmp_path: Path, day: date
) -> None:
    plan, _, _ = scoped_package(tmp_path)
    with pytest.raises(ValueError, match="forward_scope_requires_recent_completed_date"):
        replace(plan, day=day)


@pytest.mark.parametrize("change", ["naive", "non_utc", "too_long", "reversed"])
def test_invalid_scope_instants(change: str) -> None:
    start, end = NOW, NOW + timedelta(hours=1)
    if change == "naive":
        start = start.replace(tzinfo=None)
    elif change == "non_utc":
        from zoneinfo import ZoneInfo

        start = start.astimezone(ZoneInfo("Asia/Seoul"))
    elif change == "too_long":
        end = start + timedelta(hours=25)
    else:
        end = start
    with pytest.raises(ValueError, match="forward_scope_"):
        ForwardObservationScope(start, end)


@pytest.mark.parametrize("version", ["forward_capture_plan_v3", None])
def test_unknown_version_rejected(tmp_path: Path, version: str | None) -> None:
    plan, _, _ = scoped_package(tmp_path)
    with pytest.raises(ValueError, match="plan_contract"):
        ForwardCapturePlan.from_dict(plan.document.to_dict() | {"version": version})


def test_policy_window_and_exact_scope_binding(tmp_path: Path) -> None:
    plan, ref, _ = scoped_package(tmp_path)
    with pytest.raises(ValueError, match="forward_scope_policy_window_mismatch"):
        verify_operator_authorization(
            replace(
                plan, observation_scope=ForwardObservationScope(NOW, NOW + timedelta(minutes=1))
            ),
            "a",
            ref,
            None,
            NOW,
        )
    with pytest.raises(ValueError, match="operator_capture_scope_mismatch"):
        verify_operator_authorization(replace(plan, day=date(2026, 10, 23)), "a", ref, None, NOW)
    with pytest.raises(ValueError, match="provider_permission_expired_or_not_yet_valid"):
        verify_operator_authorization(plan, "a", ref, None, NOW + timedelta(days=1))
    with pytest.raises(ValueError, match="forward_scope_requires_operator_read_only_policy"):
        verify_authorization(plan, "a", ref, None, NOW)


@pytest.mark.parametrize("kind", ["missing", "modified", "pin", "credential"])
def test_missing_or_modified_evidence_fails_closed(tmp_path: Path, kind: str) -> None:
    plan, ref, _ = scoped_package(tmp_path)
    root = Path(ref.to_dict()["root"])
    if kind == "missing":
        (root / "permission.bin").unlink()
    elif kind == "modified":
        (root / "terms.bin").write_bytes(b"tampered terms")
    elif kind == "pin":
        ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": "0" * 64})
    result = operator_readiness(
        plan,
        "a",
        ref,
        None,
        NOW,
        True,
        credential="wrong" if kind == "credential" else KEY,
        capture_root=tmp_path / "capture",
    ).to_dict()
    assert not result["ready_for_explicit_read_only_capture"]
    assert result["blockers"]
    assert result["network_requests"] == 0
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize(
    "field,value",
    [("max_requests", 2), ("daily_limit", 2), ("max_seconds", 31), ("mode", "offline_fixture")],
)
def test_v2_cannot_expand_operator_pilot_limits(tmp_path: Path, field: str, value: Any) -> None:
    plan, _, _ = scoped_package(tmp_path)
    with pytest.raises(ValueError, match="forward_scope_operator_pilot_only"):
        replace(plan, **{field: value})


def test_archive_manifest_readback_replay_and_denied_downstream(tmp_path: Path) -> None:
    plan, ref, _ = scoped_package(tmp_path)
    root = tmp_path / "capture"
    with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
        with patch("tests.integration.test_krx_operator_policy.NOW", NOW):
            owner = record(root, plan, ref)
        before = (root / DB).read_bytes()
        assert verify_capture(root, plan, "a", owner.identifier) == owner
        assert (
            verify_operator_capture_authorization(root, plan, "a", owner.identifier, ref, NOW)
            == "authorized_provider_mock_fixture"
        )
        raw = json.loads(readback(root, plan, "a", owner.identifier, 0))
        assert raw["OutBlock_1"][0]["ISU_CD"] == "005930"
        assert (root / DB).read_bytes() == before
    result = operator_readiness(
        plan, "a", ref, None, NOW, True, credential=KEY, capture_root=root
    ).to_dict()
    assert result["ready_for_explicit_read_only_capture"]
    assert not any(
        result[k]
        for k in (
            "forward_signal_eligible",
            "paper_execution_eligible",
            "pit_qualified",
            "independent_authorization_verified",
        )
    )
    request = FrozenJSON.freeze(
        {
            "version": "forward_signal_request_v2",
            "symbol": plan.ticker,
            "market": plan.market,
            "currency": "KRW",
            "mode": "authorized_provider",
            "start": plan.day.isoformat(),
            "end": plan.day.isoformat(),
            "calculated_at": NOW.isoformat(),
            "fast": 1,
            "slow": 2,
            "policy": POLICY,
            "max_latest_age_seconds": 3600,
            "database_pin": "0" * 64,
            "sources": [
                {
                    "plan": plan.document.to_dict(),
                    "plan_pin": plan.document.identifier,
                    "attempt": "a",
                    "capture_pin": owner.identifier,
                }
            ],
            "authorization": ref.to_dict(),
        }
    )
    with pytest.raises(ValueError, match="read_only_forward_scope_not_signal_eligible"):
        request_contract(request, request.identifier)


@pytest.mark.parametrize("action", ["authorization-readiness", "dry-run"])
def test_cli_readiness_no_network_and_no_archive_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    plan, ref, policy = scoped_package(tmp_path)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.document.payload_json)

    class Clock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW

    with (
        patch("donghak_stock_vision.forward_capture_cli.datetime", Clock),
        patch.dict("os.environ", {"KRX_API_KEY": KEY}),
        patch("socket.socket.connect", side_effect=AssertionError("network forbidden")),
    ):
        assert (
            main(
                [
                    "forward-capture",
                    action,
                    "--root",
                    str(tmp_path / "capture"),
                    "--plan",
                    str(plan_file),
                    "--plan-pin",
                    plan.document.identifier,
                    "--attempt",
                    "a",
                    "--authorization-policy",
                    OPERATOR,
                    "--authorization-root",
                    ref.to_dict()["root"],
                    "--authorization-pin",
                    policy.identifier,
                ]
            )
            == 0
        )
    result = json.loads(capsys.readouterr().out)
    assert result["ready_for_explicit_read_only_capture"]
    assert result["operator_policy_integrity_verified"] and result["credential_binding_verified"]
    assert result["network_requests"] == 0
    assert not (tmp_path / "capture").exists()


def test_network_opt_in_not_replaced_by_scope(tmp_path: Path) -> None:
    plan, ref, _ = scoped_package(tmp_path)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected HTTP"))
    ) as client:
        with pytest.raises(ValueError, match="explicit_network_permission_required"):
            run_capture(
                tmp_path / "capture",
                plan,
                "a",
                client,
                api_key=KEY,
                authorization=ref,
                authorization_policy=OPERATOR,
                clock=lambda: NOW,
            )
    assert not (tmp_path / "capture").exists()
