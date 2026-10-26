"""Explicit owner-local pilot fixtures: no authentic entitlement or provider request."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.integration.test_krx_forward_authorization import (
    CALCULATED,
    KEY,
    NOW,
    permission_fixture,
)

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.forward_capture_cli import offline_plan
from donghak_stock_vision.storage.forward_capture import (
    DB,
    run_capture,
    verify_capture,
    verify_capture_authorization,
    verify_operator_capture_authorization,
)
from donghak_stock_vision.storage.forward_signal import POLICY, generate_signal
from donghak_stock_vision.storage.krx_authorization import (
    OPERATOR,
    OPERATOR_ACK,
    operator_readiness,
    verify_operator_authorization,
)


def package(tmp: Path) -> tuple[ForwardCapturePlan, FrozenJSON, FrozenJSON]:
    plans, ref, _ = permission_fixture(
        tmp, [replace(offline_plan(), mode="authorized_provider", daily_limit=1)]
    )
    root = Path(ref.to_dict()["root"])
    d = FrozenJSON((root / "grant.json").read_text()).to_dict()
    del d["reviewer"]
    d.update(
        version="krx_operator_read_only_pilot_v1",
        acknowledgement=OPERATOR_ACK,
        approved_from="2025-06-29",
        approved_through="2025-07-10",
        service_id="stk_bydd_trd",
        method="GET",
        reviewed_at=NOW.isoformat(),
        valid_from=NOW.isoformat(),
        valid_until=(NOW + timedelta(hours=1)).isoformat(),
        document_origin="synthetic_test_fixture",
        uses={"private_capture": True, "forward_signal": False, "paper": False},
    )
    spec = b"SYNTHETIC TEST SPECIFICATION ONLY; NOT ORIGINAL KRX PERMISSION"
    (root / "specification.bin").write_bytes(spec)
    d["specification"] = {
        "issuer": "KRX",
        "source_url": "https://openapi.krx.co.kr/fixture-only/specification",
        "sha256": hashlib.sha256(spec).hexdigest(),
    }
    policy = FrozenJSON.freeze(d)
    (root / "operator-policy.json").write_text(policy.payload_json)
    (root / "grant.json").unlink()
    (root / "signature.json").unlink()
    ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": policy.identifier})
    plan = replace(plans[0], authorization_reference=policy.identifier)
    return plan, ref, policy


def record(root: Path, plan: ForwardCapturePlan, ref: FrozenJSON, **kwargs: Any) -> FrozenJSON:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "OutBlock_1": [
                    {
                        "ISU_CD": plan.ticker,
                        "BAS_DD": plan.day.strftime("%Y%m%d"),
                        "MKT_NM": plan.market,
                        "TDD_OPNPRC": "100",
                        "TDD_HGPRC": "110",
                        "TDD_LWPRC": "90",
                        "TDD_CLSPRC": "105",
                        "ACC_TRDVOL": "10",
                        "ACC_TRDVAL": "1050",
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        return run_capture(
            root,
            plan,
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_policy=OPERATOR,
            operator_confirmed=True,
            clock=lambda: NOW,
            monotonic=lambda: 0,
            sleep=lambda _: None,
            **kwargs,
        )


def test_explicit_read_only_capture_and_original_receipt_replay(tmp_path: Path) -> None:
    plan, ref, policy = package(tmp_path)
    root = tmp_path / "capture"
    with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
        owner = record(root, plan, ref)
        assert verify_capture(root, plan, "a", owner.identifier) == owner
        assert (
            verify_operator_capture_authorization(root, plan, "a", owner.identifier, ref, NOW)
            == "authorized_provider_mock_fixture"
        )
        before = (root / DB).read_bytes()
        assert record(root, plan, ref, trusted_resume_pin=owner.identifier) == owner
        assert (root / DB).read_bytes() == before
    assert not (Path(ref.to_dict()["root"]) / "signature.json").exists()
    assert policy.to_dict()["uses"]["paper"] is False


@pytest.mark.parametrize(
    "kind", ["default_signed", "unknown", "unconfirmed", "no_network", "reviewer_trust"]
)
def test_no_implicit_downgrade_or_unauthorized_http(tmp_path: Path, kind: str) -> None:
    plan, ref, _ = package(tmp_path)
    options: dict[str, Any] = {
        "authorization_policy": OPERATOR,
        "operator_confirmed": True,
        "allow_network": True,
    }
    if kind == "default_signed":
        options = {"allow_network": True}
    elif kind == "unknown":
        options["authorization_policy"] = "auto"
    elif kind == "unconfirmed":
        options["operator_confirmed"] = False
    elif kind == "no_network":
        options["allow_network"] = False
    else:
        options["authorization_trust"] = FrozenJSON.freeze({"untrusted": True})
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("unauthorized HTTP"))
    ) as client:
        with pytest.raises((ValueError, OSError)):
            run_capture(
                tmp_path / "absent",
                plan,
                "a",
                client,
                api_key=KEY,
                authorization=ref,
                clock=lambda: NOW,
                **options,
            )
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "case",
    [
        "service",
        "method",
        "endpoint",
        "approved_dates",
        "expired",
        "window",
        "operator_ack",
        "paper",
        "origin",
        "attempt",
        "scope",
        "budget",
        "modified_permission",
        "missing_terms",
        "missing_specification",
        "403_terms",
        "wrong_pin",
        "utc",
        "root",
    ],
)
def test_operator_policy_integrity_and_semantic_rejections(tmp_path: Path, case: str) -> None:
    plan, ref, policy = package(tmp_path)
    root = Path(ref.to_dict()["root"])
    d = policy.to_dict()
    at = NOW
    attempt = "a"
    if case == "service":
        d["service_id"] = "stk_isu_base_info"
    elif case == "method":
        d["method"] = "POST"
    elif case == "endpoint":
        d["endpoint"] += "?unauthorized=1"
    elif case == "approved_dates":
        d["approved_through"] = "2025-07-04"
    elif case == "expired":
        at += timedelta(hours=1)
    elif case == "window":
        d["valid_until"] = (NOW + timedelta(hours=25)).isoformat()
    elif case == "operator_ack":
        d["acknowledgement"] = "approved"
    elif case == "paper":
        d["uses"]["paper"] = True
    elif case == "origin":
        d["document_origin"] = "operator_supplied_provider_originals"
    elif case == "attempt":
        attempt = "extra"
    elif case == "scope":
        plan = replace(plan, ticker="000660")
    elif case == "budget":
        plan = replace(plan, max_requests=2, daily_limit=2)
    elif case == "modified_permission":
        (root / "permission.bin").write_bytes(b"changed original")
    elif case == "missing_terms":
        (root / "terms.bin").unlink()
    elif case == "missing_specification":
        (root / "specification.bin").unlink()
    elif case == "403_terms":
        raw = b"<title>403 Forbidden</title> this is not a successful terms response"
        (root / "terms.bin").write_bytes(raw)
        d["terms"]["sha256"] = hashlib.sha256(raw).hexdigest()
    elif case == "utc":
        d["reviewed_at"] = "2025-07-05T09:00:00+09:00"
    elif case == "root":
        d["capture_root"] = "relative/path"
    changed = FrozenJSON.freeze(d)
    (root / "operator-policy.json").write_text(changed.payload_json)
    ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": changed.identifier})
    plan = replace(
        plan, authorization_reference=changed.identifier, terms_reference=d["terms"]["sha256"]
    )
    if case == "wrong_pin":
        ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": "0" * 64})
    with pytest.raises((ValueError, OSError)):
        verify_operator_authorization(plan, attempt, ref, None, at)


def test_operator_capture_cannot_become_independent_signal_or_paper_evidence(
    tmp_path: Path,
) -> None:
    plan, ref, _ = package(tmp_path)
    root = tmp_path / "capture"
    owner = record(root, plan, ref)
    with pytest.raises((ValueError, OSError)):
        verify_capture_authorization(root, plan, "a", owner.identifier, ref, None, NOW)
    request = FrozenJSON.freeze(
        {
            "version": "forward_signal_request_v2",
            "symbol": plan.ticker,
            "market": plan.market,
            "currency": "KRW",
            "mode": "authorized_provider",
            "start": plan.day.isoformat(),
            "end": plan.day.isoformat(),
            "calculated_at": CALCULATED.isoformat(),
            "fast": 1,
            "slow": 2,
            "policy": POLICY,
            "max_latest_age_seconds": 3600,
            "database_pin": hashlib.sha256((root / DB).read_bytes()).hexdigest(),
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
    with pytest.raises((ValueError, OSError)):
        generate_signal(root, request, request.identifier)
    assert not (tmp_path / "signals.sqlite3").exists()
    assert not (tmp_path / "paper.sqlite3").exists()


def test_invalid_credential_and_wrong_archive_fail_before_http(tmp_path: Path) -> None:
    plan, ref, _ = package(tmp_path)
    bad = operator_readiness(
        plan,
        "a",
        ref,
        None,
        NOW,
        True,
        credential="wrong-private-value",
        capture_root=tmp_path / "capture",
    ).to_dict()
    assert not bad["ready_for_explicit_read_only_capture"]
    assert "credential_not_bound" in bad["blockers"][-1]
    assert "wrong-private-value" not in json.dumps(bad)
    with pytest.raises(ValueError, match="capture_root_outside"):
        record(tmp_path / "different", plan, ref)
    assert not (tmp_path / "different").exists()


def test_readiness_is_explicitly_lower_trust_and_never_networks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, ref, _ = package(tmp_path)
    root = tmp_path / "capture"
    d = operator_readiness(
        plan, "a", ref, None, NOW, True, credential=KEY, capture_root=root
    ).to_dict()
    assert d["ready_for_explicit_read_only_capture"] and not d["independent_authorization_verified"]
    assert not d["forward_signal_eligible"] and not d["paper_execution_eligible"]
    assert KEY not in json.dumps(d)
    p = tmp_path / "plan.json"
    p.write_text(plan.document.payload_json)

    class Clock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW

    with (
        patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
        patch("donghak_stock_vision.forward_capture_cli.datetime", Clock),
        patch.dict("os.environ", {"KRX_API_KEY": KEY}),
    ):
        args = [
            "--root",
            str(root),
            "--plan",
            str(p),
            "--plan-pin",
            plan.document.identifier,
            "--attempt",
            "a",
            "--authorization-root",
            ref.to_dict()["root"],
            "--authorization-pin",
            ref.to_dict()["grant_pin"],
            "--authorization-policy",
            OPERATOR,
        ]
        assert main(["forward-capture", "authorization-readiness", *args]) == 0
        assert json.loads(capsys.readouterr().out) == d
        assert main(["forward-capture", "dry-run", *args]) == 0
        assert json.loads(capsys.readouterr().out) == d
        assert main(["forward-capture", "capture", *args, "--allow-network"]) == 2
        assert (
            main(
                [
                    "forward-capture",
                    "capture",
                    *args,
                    "--allow-network",
                    "--confirm-operator-authorization",
                ]
            )
            == 2
        )
    assert not root.exists()


def test_protected_partition_blocks_policy_files_before_io(tmp_path: Path) -> None:
    plan, ref, _ = package(tmp_path)
    d = plan.document.to_dict() | {"partition": "final_test"}
    with patch(
        "donghak_stock_vision.storage.krx_authorization.bounded",
        side_effect=AssertionError("protected source I/O"),
    ):
        with pytest.raises(ValueError, match="forbidden_partition"):
            ForwardCapturePlan.from_dict(d)


def test_revoked_independent_reviewer_never_falls_back_to_operator_mode(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    t = trust.to_dict()
    registry = Path(t["registry"])
    d = json.loads(registry.read_text())
    d["keys"][0]["status"] = "revoked"
    registry.write_text(json.dumps(d))
    trust = FrozenJSON.freeze(
        t | {"registry_sha256": hashlib.sha256(registry.read_bytes()).hexdigest()}
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("revoked reviewer HTTP"))
    ) as client:
        with pytest.raises(ValueError, match="reviewer_untrusted_revoked"):
            run_capture(
                tmp_path / "capture",
                plans[0],
                "a",
                client,
                api_key=KEY,
                allow_network=True,
                authorization=ref,
                authorization_trust=trust,
                clock=lambda: NOW,
            )
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("bad", ["wrong-private-value", "bad\r\nheader"])
def test_wrong_or_invalid_operator_credential_never_calls_provider(
    tmp_path: Path, bad: str
) -> None:
    plan, ref, policy = package(tmp_path)
    if "\r" in bad:
        d = policy.to_dict() | {
            "credential_sha256": hashlib.sha256(
                b"DonghakStockVision:krx-credential-binding:v1\x00" + bad.encode()
            ).hexdigest()
        }
        modified = FrozenJSON.freeze(d)
        Path(ref.to_dict()["root"], "operator-policy.json").write_text(modified.payload_json)
        ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": modified.identifier})
        plan = replace(plan, authorization_reference=modified.identifier)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("invalid credential HTTP"))
    ) as client:
        with pytest.raises(ValueError, match="credential"):
            run_capture(
                tmp_path / "capture",
                plan,
                "a",
                client,
                api_key=bad,
                allow_network=True,
                authorization=ref,
                authorization_policy=OPERATOR,
                operator_confirmed=True,
                clock=lambda: NOW,
            )
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("kind", ["raw", "binding"])
def test_operator_source_tampering_rejected_by_original_owners(tmp_path: Path, kind: str) -> None:
    import sqlite3
    from contextlib import closing

    plan, ref, _ = package(tmp_path)
    root = tmp_path / "capture"
    owner = record(root, plan, ref)
    with closing(sqlite3.connect(root / DB)) as connection:
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall():
            connection.execute(f'DROP TRIGGER "{name}"')
        if kind == "raw":
            connection.execute("UPDATE raw_pages SET body=?", (b"tampered original",))
        else:
            connection.execute("DELETE FROM forward_authorizations")
        connection.commit()
    with pytest.raises(ValueError):
        verify_operator_capture_authorization(root, plan, "a", owner.identifier, ref, NOW)
