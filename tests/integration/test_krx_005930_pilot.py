"""Samsung 005930 pilot audit: fixture evidence cannot authorize live collection."""

from argparse import Namespace
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from tests.integration.test_operational_capture import captured, ingest, snapshot

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.krx_scheduler_cli import run
from donghak_stock_vision.storage.krx_pilot import (
    ENDPOINT,
    PilotEvidence,
    audit_stored_005930,
    pilot_preflight,
)
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig
from donghak_stock_vision.storage.operational_market import (
    OperationalMarketStore,
    StorageLimits,
)


def retained(tmp_path: Path) -> tuple[Path, PilotEvidence]:
    source_root, owner = captured(tmp_path)
    plan = ForwardCapturePlan.from_dict(owner.to_dict()["plan"])
    dest = tmp_path / "operational"
    OperationalMarketStore.initialize(dest, StorageLimits(min_free_bytes=0))
    receipt = ingest(dest, source_root, owner)
    evidence = PilotEvidence(
        source_root,
        plan,
        plan.document.identifier,
        owner.to_dict()["attempt"],
        owner.identifier,
        receipt.to_dict()["batch_pin"],
        allow_synthetic_fixture=True,
    )
    return dest, evidence


def test_synthetic_005930_original_to_transactional_readback_without_network(
    tmp_path: Path,
) -> None:
    destination, source = retained(tmp_path)
    before_original = snapshot(source.capture_root)
    before_store = snapshot(destination)
    with patch("httpx.Client.send", side_effect=AssertionError("network forbidden")):
        first = audit_stored_005930(destination, source)
        assert audit_stored_005930(destination, source) == first
    d = first.to_dict()
    assert d["ticker"] == "005930"
    assert d["market"] == "KOSPI"
    assert d["source_origin"] == "offline_fixture"
    assert d["verified_batch_pin"] == source.batch_pin
    assert d["locally_verified"]
    assert not d["independently_authenticated_market_origin"]
    assert not d["genuine_forward_evidence"]
    assert d["network_requests"] == 0
    assert snapshot(source.capture_root) == before_original
    assert snapshot(destination) == before_store


@pytest.mark.parametrize("name", ["capture_pin", "plan_pin", "batch_pin"])
def test_pinned_capture_source_or_batch_mismatch_fails_closed(tmp_path: Path, name: str) -> None:
    destination, source = retained(tmp_path)
    damaged = replace(source, **{name: "0" * 64})
    with pytest.raises(ValueError):
        audit_stored_005930(destination, damaged)


def test_explicit_fixture_required_and_provider_authorization_not_inferred(
    tmp_path: Path,
) -> None:
    destination, source = retained(tmp_path)
    with pytest.raises(ValueError, match="synthetic_fixture_acknowledgement_required"):
        audit_stored_005930(destination, replace(source, allow_synthetic_fixture=False))
    with pytest.raises(ValueError):
        audit_stored_005930(
            destination, replace(source, authorization=FrozenJSON.freeze({"approved": True}))
        )


def test_missing_verified_005930_observation_rejected(tmp_path: Path) -> None:
    destination, source = retained(tmp_path)
    wrong_day = replace(source.plan, day=source.plan.day.replace(year=2024))
    with pytest.raises(ValueError):
        audit_stored_005930(destination, replace(source, plan=wrong_day))


def fake_preflight(*, endpoint: str = ENDPOINT, ready: bool = True) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "checked_at": "2026-11-10T00:00:00+00:00",
            "selected_trading_date": "2026-11-09",
            "origin": "provider_https" if ready else None,
            "ready_for_manual_provider_capture": ready,
            "blockers": [] if ready else ["missing_original_grant"],
            "request": {
                "endpoint": endpoint,
                "method": "GET",
                "parameters": {"basDd": "20261109"},
                "requests_maximum": 1,
                "retries": 0,
                "concurrency": 1,
                "max_seconds": 30,
                "max_response_bytes": 2097152,
            }
            if ready
            else None,
        }
    )


@pytest.mark.parametrize("ready", [True, False])
def test_pilot_preflight_never_authorizes_network_or_trading(ready: bool) -> None:
    config = cast(SchedulerConfig, object())
    expected = fake_preflight(ready=ready)
    with (
        patch(
            "donghak_stock_vision.storage.krx_pilot.preflight",
            return_value=expected,
        ),
        patch("httpx.Client.send", side_effect=AssertionError("network forbidden")),
    ):
        result = pilot_preflight(config, datetime.now(UTC), Path("unused"))
    d = result.to_dict()
    assert d["request_contract_verified"] is ready
    assert d["configured_provider_ready_for_separate_review"] is ready
    assert d["live_pilot_authorized"] is False
    assert "separate_explicit_live_005930_operator_authorization_required" in d["blockers"]
    assert d["stored_005930"] is None and d["network_requests"] == 0


def test_wrong_endpoint_cannot_claim_005930_request_contract() -> None:
    with patch(
        "donghak_stock_vision.storage.krx_pilot.preflight",
        return_value=fake_preflight(endpoint="https://example.invalid"),
    ):
        d = pilot_preflight(
            cast(SchedulerConfig, object()), datetime.now(UTC), Path("unused")
        ).to_dict()
    assert not d["request_contract_verified"]
    assert "005930_pilot_request_contract_not_verified" in d["blockers"]


@pytest.mark.parametrize("flag", ["allow_network", "execute", "confirm_operator_authorization"])
def test_cli_pilot_preflight_rejects_execution_flags(flag: str, tmp_path: Path) -> None:
    flags = {
        "apply": False,
        "execute": False,
        "scheduled": False,
        "allow_network": False,
        "confirm_operator_authorization": False,
    }
    flags[flag] = True
    args = Namespace(
        db=None,
        action="pilot-preflight",
        config=tmp_path / "private.json",
        config_pin="0" * 64,
        source_plan=None,
        source_plan_pin=None,
        source_attempt=None,
        capture_pin=None,
        batch_pin=None,
        source_authorization_root=None,
        source_authorization_pin=None,
        **flags,
    )
    with patch(
        "donghak_stock_vision.krx_scheduler_cli.load_config",
        return_value=object(),
    ):
        with pytest.raises(ValueError, match="pilot_preflight_is_read_only"):
            run(args)
