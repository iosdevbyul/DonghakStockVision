"""Signed TEST reviews and MockTransport only: no authentic KRX permission is supplied."""

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.forward_capture_cli import offline_plan
from donghak_stock_vision.paper_trade_demo_cli import action, configuration
from donghak_stock_vision.storage.forward_capture import (
    DB,
    run_capture,
    verify_capture_authorization,
)
from donghak_stock_vision.storage.forward_paper_journal import GENESIS, execute, inspect
from donghak_stock_vision.storage.forward_signal import POLICY, generate_signal, verify_signal
from donghak_stock_vision.storage.forward_signal_journal import GENESIS as SIGNAL_GENESIS
from donghak_stock_vision.storage.forward_signal_journal import append_signal
from donghak_stock_vision.storage.krx_authorization import (
    DOMAIN,
    endpoint,
    readiness,
    verify_authorization,
)

KEY = "fixture-only-not-credential"

NOW = datetime(2025, 7, 5, tzinfo=UTC)
CALCULATED = NOW + timedelta(seconds=20)


def permission_fixture(
    tmp: Path,
    plans: list[ForwardCapturePlan] | None = None,
    transport: str = "mock_fixture",
    archive_root: Path | None = None,
) -> tuple[list[ForwardCapturePlan], FrozenJSON, FrozenJSON]:
    if plans is None:
        split = ExplicitResearchSplit(
            ResearchDateRange(date(2025, 7, 1), date(2025, 7, 4)),
            ResearchDateRange(date(2025, 7, 7), date(2025, 7, 7)),
            ResearchDateRange(date(2025, 7, 8), date(2025, 7, 8)),
        )
        plans = [
            replace(
                offline_plan(),
                mode="authorized_provider",
                day=date(2025, 7, i),
                split=split,
                split_pin=split.identifier,
            )
            for i in range(1, 5)
        ]
    root = tmp / "authorization"
    root.mkdir(mode=0o700)
    documents = {
        n: f"SYNTHETIC NEGATIVE/INTEGRATION FIXTURE {n}; NOT KRX AUTHORIZATION".encode()
        for n in ("permission", "terms")
    }
    for name, raw in documents.items():
        (root / f"{name}.bin").write_bytes(raw)
    grant = FrozenJSON.freeze(
        {
            "version": "krx_forward_permission_review_v1",
            "reviewer": "fixture-independent-reviewer",
            "operator_id": "fixture-collector",
            "reviewed_at": "2025-06-30T00:00:00+00:00",
            "valid_from": "2025-06-29T00:00:00+00:00",
            "valid_until": "2025-07-11T00:00:00+00:00",
            **{
                n: {
                    "issuer": "KRX",
                    "source_url": f"https://openapi.krx.co.kr/fixture-only/{n}",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                for n, raw in documents.items()
            },
            "captures": [{"plan": p.document.to_dict(), "attempt": "a"} for p in plans],
            "endpoint": endpoint(plans[0]),
            "capture_root": str((archive_root or tmp / "capture").resolve()),
            "transport": transport,
            "credential_sha256": hashlib.sha256(
                b"DonghakStockVision:krx-credential-binding:v1\x00" + KEY.encode()
            ).hexdigest(),
            "max_observation_lag_days": 7,
            "uses": {"private_capture": True, "forward_signal": True, "paper": True},
        }
    )
    key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"explicit-test-key-only").digest())
    registry = FrozenJSON.freeze(
        {
            "schemaVersion": "trusted_reviewer_keys_v1",
            "keys": [
                {
                    "keyId": "fixture-key",
                    "reviewer": "fixture-independent-reviewer",
                    "status": "active",
                    "publicKeyBase64": base64.b64encode(
                        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                    ).decode(),
                }
            ],
        }
    )
    registry_path = tmp / "reviewers.json"
    registry_path.write_text(registry.payload_json)
    trust = FrozenJSON.freeze(
        {
            "registry": str(registry_path),
            "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            "operator_id": "fixture-collector",
        }
    )
    ref = FrozenJSON.freeze({"root": str(root), "grant_pin": grant.identifier})
    _sign(root, grant)
    return (
        [
            replace(
                p,
                authorization_reference=grant.identifier,
                terms_reference=grant.to_dict()["terms"]["sha256"],
            )
            for p in plans
        ],
        ref,
        trust,
    )


def _sign(root: Path, grant: FrozenJSON) -> None:
    key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"explicit-test-key-only").digest())
    (root / "grant.json").write_text(grant.payload_json)
    (root / "signature.json").write_text(
        FrozenJSON.freeze(
            {
                "version": "signed_krx_forward_permission_review_v1",
                "keyId": "fixture-key",
                "signatureBase64": base64.b64encode(
                    key.sign(DOMAIN + grant.payload_json.encode())
                ).decode(),
            }
        ).payload_json
    )


def mock_capture(
    root: Path,
    plan: ForwardCapturePlan,
    ref: FrozenJSON,
    trust: FrozenJSON,
    price: int = 105,
    at: datetime = NOW,
) -> FrozenJSON:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).split("?")[0] == endpoint(plan)
        return httpx.Response(
            200,
            json={
                "OutBlock_1": [
                    {
                        "ISU_CD": plan.ticker,
                        "BAS_DD": plan.day.strftime("%Y%m%d"),
                        "MKT_NM": plan.market,
                        "TDD_OPNPRC": str(price),
                        "TDD_HGPRC": str(price),
                        "TDD_LWPRC": str(price),
                        "TDD_CLSPRC": str(price),
                        "ACC_TRDVOL": "10",
                        "ACC_TRDVAL": str(price * 10),
                    }
                ]
            },
        )

    class AuditClock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW + timedelta(seconds=10)

    with (
        httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client,
        patch("donghak_stock_vision.storage.sqlite.datetime", AuditClock),
    ):
        return run_capture(
            root,
            plan,
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: at,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )


def pipeline(
    tmp: Path, closes: tuple[int, ...] = (100, 80, 120, 80)
) -> tuple[FrozenJSON, FrozenJSON, FrozenJSON]:
    plans, ref, trust = permission_fixture(tmp)
    root = tmp / "capture"
    sources = []
    for i, price in enumerate(closes):
        owner = mock_capture(root, plans[i], ref, trust, price, NOW + timedelta(seconds=i))
        assert (
            verify_capture_authorization(
                root, plans[i], "a", owner.identifier, ref, trust, CALCULATED
            )
            == "authorized_provider_mock_fixture"
        )
        sources.append(
            {
                "plan": plans[i].document.to_dict(),
                "plan_pin": plans[i].document.identifier,
                "attempt": "a",
                "capture_pin": owner.identifier,
            }
        )
    request = FrozenJSON.freeze(
        {
            "version": "forward_signal_request_v2",
            "symbol": "005930",
            "market": "KOSPI",
            "currency": "KRW",
            "mode": "authorized_provider",
            "start": "2025-07-01",
            "end": "2025-07-04",
            "calculated_at": CALCULATED.isoformat(),
            "fast": 1,
            "slow": 2,
            "policy": POLICY,
            "max_latest_age_seconds": 3600,
            "database_pin": hashlib.sha256((root / DB).read_bytes()).hexdigest(),
            "sources": sources,
            "authorization": ref.to_dict(),
        }
    )
    return request, trust, ref


def test_missing_authorization_no_network_or_writes(tmp_path: Path) -> None:
    plan = replace(
        offline_plan(),
        mode="authorized_provider",
        authorization_reference="self-declared",
        terms_reference="self-declared",
    )
    assessed = readiness(plan, "a", None, None, NOW, True).to_dict()
    assert not assessed["authorization_verified"] and not assessed["ready_for_explicit_capture"]
    assert "original_permission_terms_and_signed_review_required" in assessed["blockers"]
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("network before authorization"))
    ) as client:
        with pytest.raises(ValueError, match="original_permission"):
            run_capture(
                tmp_path / "absent",
                plan,
                "a",
                client,
                api_key="must-not-log",
                allow_network=True,
                clock=lambda: NOW,
            )
    assert not (tmp_path / "absent").exists()


def test_mock_authorized_source_signal_risk_broker_and_replay(tmp_path: Path) -> None:
    request, trust, _ = pipeline(tmp_path)
    journal = tmp_path / "signals.sqlite3"
    # The first decision uses a three-observation context; the next adds a fourth.
    buy = FrozenJSON.freeze(request.to_dict() | {"sources": request.to_dict()["sources"][:3]})
    sell = FrozenJSON.freeze(
        request.to_dict() | {"calculated_at": (CALCULATED + timedelta(seconds=3)).isoformat()}
    )
    a = append_signal(
        journal,
        tmp_path / "capture",
        buy,
        buy.identifier,
        SIGNAL_GENESIS,
        CALCULATED,
        authorization_trust=trust,
    )
    b = append_signal(
        journal,
        tmp_path / "capture",
        sell,
        sell.identifier,
        a.to_dict()["head_pin"],
        CALCULATED + timedelta(seconds=3),
        authorization_trust=trust,
    )
    config = FrozenJSON.freeze(
        configuration(CALCULATED).to_dict()
        | {
            "version": "forward_paper_account_v2",
            "mode": "PAPER_AUTHORIZED_CAPTURE",
            "authorization_trust": trust.to_dict(),
        }
    )
    jobs = [
        FrozenJSON.freeze(
            action(
                tmp_path / "capture",
                r,
                journal,
                b.to_dict()["head_pin"],
                e.to_dict()["entry"]["entry"]["decision"]["signal_id"],
                CALCULATED + timedelta(seconds=step),
            ).to_dict()
            | {"version": "forward_paper_action_v2", "mode": "PAPER_AUTHORIZED_CAPTURE"}
        )
        for r, e, step in ((buy, a, 0), (sell, b, 3))
    ]
    with (
        patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
        patch(
            "httpx.HTTPTransport.handle_request", side_effect=AssertionError("network forbidden")
        ),
    ):
        path = tmp_path / "paper.sqlite3"
        first = execute(path, config, config.identifier, GENESIS, jobs[0]).to_dict()
        assert first["result"]["signal"] == "BUY" and first["result"]["risk"]["allowed"]
        assert first["result"]["origin"] == "authorized_provider_mock_fixture"
        assert first["result"]["version"] == "forward_paper_result_v2"
        second = execute(path, config, config.identifier, first["head_pin"], jobs[1]).to_dict()
        assert second["result"]["signal"] == "SELL" and second["result"]["outcome"] == "filled"
        assert (
            second["result"]["after"]["account"]["balances"][0]["available"]["amount"] == "9915.27"
        )
        assert inspect(path, config, config.identifier, second["head_pin"]).to_dict()[
            "semantic_replay_verified"
        ]
        assert execute(path, config, config.identifier, second["head_pin"], jobs[1]).to_dict()[
            "duplicate"
        ]


@pytest.mark.parametrize("transport", ["provider_https", "mock_fixture"])
def test_transport_origin_cannot_be_relabelled(tmp_path: Path, transport: str) -> None:
    plans, ref, trust = permission_fixture(tmp_path, transport=transport)
    if transport == "provider_https":
        with pytest.raises(ValueError, match="verified_direct_https_transport_required"):
            mock_capture(tmp_path / "absent", plans[0], ref, trust)
    else:
        with httpx.Client(verify=False, trust_env=False) as client:
            with pytest.raises(ValueError, match="mock_transport"):
                run_capture(
                    tmp_path / "absent",
                    plans[0],
                    "a",
                    client,
                    api_key=KEY,
                    allow_network=True,
                    authorization=ref,
                    authorization_trust=trust,
                    clock=lambda: NOW,
                )
    assert not (tmp_path / "absent").exists()


def test_real_permission_cannot_use_disabled_tls(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path, transport="provider_https")
    with (
        httpx.Client(verify=False, trust_env=False) as client,
        pytest.raises(ValueError, match="verified_direct_https"),
    ):
        run_capture(
            tmp_path / "absent",
            plans[0],
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: NOW,
        )
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "case",
    [
        "permission",
        "terms",
        "grant",
        "signature",
        "registry",
        "revoked",
        "self_review",
        "expired",
        "future",
        "attempt",
        "budget",
        "lag",
        "old_bar",
    ],
)
def test_tampered_or_ineligible_permission(tmp_path: Path, case: str) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    plan = plans[0]
    root = Path(ref.to_dict()["root"])
    at = NOW
    attempt = "a"
    if case in ("permission", "terms"):
        (root / f"{case}.bin").write_bytes(b"altered")
    elif case == "signature":
        (root / "signature.json").write_text("{}")
    elif case in ("registry", "revoked"):
        p = Path(trust.to_dict()["registry"])
        d = json.loads(p.read_text())
        d["keys"][0]["status"] = "revoked"
        p.write_text(json.dumps(d))
        if case == "revoked":
            trust = FrozenJSON.freeze(
                trust.to_dict() | {"registry_sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            )
    elif case in ("self_review", "grant", "old_bar"):
        d = json.loads((root / "grant.json").read_text())
        if case == "self_review":
            d["reviewer"] = d["operator_id"]
        elif case == "old_bar":
            d["reviewed_at"] = "2025-07-02T00:00:00+00:00"
        else:
            d["uses"]["paper"] = False
        modified = FrozenJSON.freeze(d)
        _sign(root, modified)
        ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": modified.identifier})
        plan = replace(plan, authorization_reference=modified.identifier)
    elif case == "expired":
        at = datetime(2025, 7, 11, tzinfo=UTC)
    elif case == "future":
        at = datetime(2025, 6, 29, tzinfo=UTC)
    elif case == "attempt":
        attempt = "unapproved-extra-request"
    elif case == "budget":
        plan = replace(plan, max_requests=2)
    elif case == "lag":
        at = NOW + timedelta(days=4)
    with pytest.raises((ValueError, OSError)):
        verify_authorization(plan, attempt, ref, trust, at)


@pytest.mark.parametrize(
    "closes,signal", [((100,), "INSUFFICIENT_DATA"), ((100, 100, 100, 100), "HOLD")]
)
def test_missing_observations_and_hold_no_order(
    tmp_path: Path, closes: tuple[int, ...], signal: str
) -> None:
    request, trust, _ = pipeline(tmp_path, closes)
    journal = tmp_path / "signals.sqlite3"
    appended = append_signal(
        journal,
        tmp_path / "capture",
        request,
        request.identifier,
        SIGNAL_GENESIS,
        CALCULATED,
        authorization_trust=trust,
    ).to_dict()
    config = FrozenJSON.freeze(
        configuration(CALCULATED).to_dict()
        | {
            "version": "forward_paper_account_v2",
            "mode": "PAPER_AUTHORIZED_CAPTURE",
            "authorization_trust": trust.to_dict(),
        }
    )
    job = FrozenJSON.freeze(
        action(
            tmp_path / "capture",
            request,
            journal,
            appended["head_pin"],
            appended["entry"]["entry"]["decision"]["signal_id"],
            CALCULATED,
        ).to_dict()
        | {"version": "forward_paper_action_v2", "mode": "PAPER_AUTHORIZED_CAPTURE"}
    )
    result = execute(tmp_path / "paper.sqlite3", config, config.identifier, GENESIS, job).to_dict()[
        "result"
    ]
    assert result["signal"] == signal and result["order"] is None
    assert result["before"]["broker_state_id"] == result["after"]["broker_state_id"]


def test_missing_review_blocks_signal_and_untrusted_summary(tmp_path: Path) -> None:
    request, trust, ref = pipeline(tmp_path)
    with pytest.raises(ValueError, match="independent_authorization_reviewer_registry"):
        generate_signal(tmp_path / "capture", request, request.identifier)
    decision = generate_signal(
        tmp_path / "capture", request, request.identifier, authorization_trust=trust
    )
    forged = FrozenJSON.freeze(decision.to_dict() | {"origin": "real_provider_https_local_capture"})
    with pytest.raises(ValueError, match="signal_semantic"):
        verify_signal(
            tmp_path / "capture",
            request,
            request.identifier,
            forged,
            forged.identifier,
            authorization_trust=trust,
        )


def test_cli_missing_evidence_is_offline_readiness(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = replace(offline_plan(), mode="authorized_provider")
    p = tmp_path / "plan.json"
    p.write_text(plan.document.payload_json)
    with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
        assert (
            main(
                [
                    "forward-capture",
                    "authorization-readiness",
                    "--root",
                    str(tmp_path / "absent"),
                    "--plan",
                    str(p),
                    "--plan-pin",
                    plan.document.identifier,
                ]
            )
            == 0
        )
    d = json.loads(capsys.readouterr().out)
    assert d["network_requests"] == 0 and not d["authorization_verified"] and d["blockers"]
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("kind", ["binding", "raw", "retrospective"])
def test_tampered_capture_or_history_cannot_append_decision(tmp_path: Path, kind: str) -> None:
    request, trust, _ = pipeline(tmp_path)
    root = tmp_path / "capture"
    if kind == "retrospective":
        bad = FrozenJSON.freeze(
            {"version": "historical_sma_analysis_v1", "mode": "RETROSPECTIVE_HISTORICAL_ANALYSIS"}
        )
        with pytest.raises(ValueError):
            append_signal(
                tmp_path / "signals.sqlite3",
                root,
                bad,
                bad.identifier,
                SIGNAL_GENESIS,
                CALCULATED,
                authorization_trust=trust,
            )
    else:
        with closing(sqlite3.connect(root / DB)) as c:
            for (name,) in c.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall():
                c.execute(f'DROP TRIGGER "{name}"')
            if kind == "binding":
                c.execute("DELETE FROM forward_authorizations")
            else:
                c.execute("UPDATE raw_pages SET body=?", (b"changed-source",))
            c.commit()
        request = FrozenJSON.freeze(
            request.to_dict()
            | {"database_pin": hashlib.sha256((root / DB).read_bytes()).hexdigest()}
        )
        with pytest.raises(ValueError):
            append_signal(
                tmp_path / "signals.sqlite3",
                root,
                request,
                request.identifier,
                SIGNAL_GENESIS,
                CALCULATED,
                authorization_trust=trust,
            )
    assert not (tmp_path / "signals.sqlite3").exists()


def test_protected_partition_rejected_before_registry_io(tmp_path: Path) -> None:
    request, trust, _ = pipeline(tmp_path)
    d = request.to_dict()
    d["sources"][0]["plan"]["partition"] = "final_test"
    bad = FrozenJSON.freeze(d)
    with (
        patch(
            "donghak_stock_vision.storage.krx_authorization.bounded",
            side_effect=AssertionError("source I/O before split guard"),
        ),
        pytest.raises(ValueError),
    ):
        generate_signal(tmp_path / "capture", bad, bad.identifier, authorization_trust=trust)


def test_expiry_during_capture_prevents_request(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    root = Path(ref.to_dict()["root"])
    d = json.loads((root / "grant.json").read_text())
    d["valid_until"] = (NOW + timedelta(seconds=1)).isoformat()
    grant = FrozenJSON.freeze(d)
    _sign(root, grant)
    ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": grant.identifier})
    plan = replace(plans[0], authorization_reference=grant.identifier)
    times = iter([NOW] + [NOW + timedelta(seconds=2)] * 20)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("expired permission reached provider")),
        trust_env=False,
    ) as client:
        result = run_capture(
            tmp_path / "capture",
            plan,
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: next(times),
            monotonic=lambda: 0,
            sleep=lambda _: None,
        ).to_dict()
    assert result["stop_reason"] == "provider_permission_expired_during_capture"
    assert result["responses"] == []


def test_exact_request_budget_and_no_extra_attempt(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    calls = []

    def failed(r: httpx.Request) -> httpx.Response:
        calls.append(r.url)
        return httpx.Response(500, content=b"fixture error")

    with httpx.Client(transport=httpx.MockTransport(failed), trust_env=False) as client:
        result = run_capture(
            tmp_path / "capture",
            plans[0],
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: NOW,
            monotonic=lambda: 0,
            sleep=lambda _: None,
        )
        assert len(calls) == 1 and result.to_dict()["stop_reason"] == "HTTP_500"
        with pytest.raises(ValueError, match="request_budget"):
            run_capture(
                tmp_path / "capture",
                plans[0],
                "extra",
                client,
                api_key=KEY,
                allow_network=True,
                authorization=ref,
                authorization_trust=trust,
                clock=lambda: NOW,
            )
    assert len(calls) == 1


def test_completed_capture_retry_uses_original_receipt_without_network(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    owner = mock_capture(tmp_path / "capture", plans[0], ref, trust)
    before = (tmp_path / "capture" / DB).read_bytes()
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("duplicate HTTP")), trust_env=False
    ) as client:
        again = run_capture(
            tmp_path / "capture",
            plans[0],
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            trusted_resume_pin=owner.identifier,
            clock=lambda: NOW,
        )
    assert again == owner and (tmp_path / "capture" / DB).read_bytes() == before


def test_paper_v2_requires_independent_configuration_and_preserves_state(tmp_path: Path) -> None:
    request, trust, _ = pipeline(tmp_path)
    appended = append_signal(
        tmp_path / "signals.sqlite3",
        tmp_path / "capture",
        request,
        request.identifier,
        SIGNAL_GENESIS,
        CALCULATED,
        authorization_trust=trust,
    ).to_dict()
    job = FrozenJSON.freeze(
        action(
            tmp_path / "capture",
            request,
            tmp_path / "signals.sqlite3",
            appended["head_pin"],
            appended["entry"]["entry"]["decision"]["signal_id"],
            CALCULATED,
        ).to_dict()
        | {"version": "forward_paper_action_v2", "mode": "PAPER_AUTHORIZED_CAPTURE"}
    )
    config = configuration(CALCULATED)
    with pytest.raises(ValueError, match="mode_mismatch"):
        execute(tmp_path / "paper.sqlite3", config, config.identifier, GENESIS, job)
    assert not (tmp_path / "paper.sqlite3").exists()


def test_cli_authorized_signal_dry_run_and_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request, trust, _ = pipeline(tmp_path)
    request_file = tmp_path / "request.json"
    request_file.write_text(request.payload_json)
    t = trust.to_dict()
    args = [
        "--capture-root",
        str(tmp_path / "capture"),
        "--request",
        str(request_file),
        "--request-pin",
        request.identifier,
        "--reviewer-registry",
        t["registry"],
        "--reviewer-registry-pin",
        t["registry_sha256"],
        "--operator-id",
        t["operator_id"],
    ]
    assert main(["forward-signal", "dry-run", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["decision"]["origin"] == "authorized_provider_mock_fixture"
    assert not result["journal_written"]


def test_no_network_opt_in_is_still_required(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: pytest.fail("no opt-in")), trust_env=False
        ) as client,
        pytest.raises(ValueError, match="explicit_network_permission"),
    ):
        run_capture(
            tmp_path / "absent",
            plans[0],
            "a",
            client,
            api_key=KEY,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: NOW,
        )
    assert not (tmp_path / "absent").exists()


def test_live_transport_cannot_backdate_permission_clock(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path, transport="provider_https")

    class ActualClock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW + timedelta(days=10)

    with (
        httpx.Client(trust_env=False) as client,
        patch("donghak_stock_vision.storage.forward_capture.datetime", ActualClock),
        patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
        pytest.raises(ValueError, match="expired"),
    ):
        run_capture(
            tmp_path / "absent",
            plans[0],
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: NOW,
        )
    assert not (tmp_path / "absent").exists()


def test_no_new_signal_insertion_after_permission_expiry(tmp_path: Path) -> None:
    request, trust, _ = pipeline(tmp_path)
    with pytest.raises(ValueError, match="expired"):
        append_signal(
            tmp_path / "signals.sqlite3",
            tmp_path / "capture",
            request,
            request.identifier,
            SIGNAL_GENESIS,
            NOW + timedelta(days=10),
            authorization_trust=trust,
        )
    assert not (tmp_path / "signals.sqlite3").exists()


def test_new_real_operations_require_current_clock_without_changing_replay() -> None:
    from donghak_stock_vision.storage.krx_authorization import current_operation

    with pytest.raises(ValueError, match="current_local_utc_clock"):
        current_operation("real_provider_https_local_capture", datetime(2000, 1, 1, tzinfo=UTC))
    current_operation("authorized_provider_mock_fixture", datetime(2000, 1, 1, tzinfo=UTC))


def test_permission_tamper_cannot_mutate_existing_paper_or_signal_journal(tmp_path: Path) -> None:
    request, trust, ref = pipeline(tmp_path, (100, 80, 120))
    source_journal = tmp_path / "signals.sqlite3"
    r = append_signal(
        source_journal,
        tmp_path / "capture",
        request,
        request.identifier,
        SIGNAL_GENESIS,
        CALCULATED,
        authorization_trust=trust,
    ).to_dict()
    config = FrozenJSON.freeze(
        configuration(CALCULATED).to_dict()
        | {
            "version": "forward_paper_account_v2",
            "mode": "PAPER_AUTHORIZED_CAPTURE",
            "authorization_trust": trust.to_dict(),
        }
    )
    job = FrozenJSON.freeze(
        action(
            tmp_path / "capture",
            request,
            source_journal,
            r["head_pin"],
            r["entry"]["entry"]["decision"]["signal_id"],
            CALCULATED,
        ).to_dict()
        | {"version": "forward_paper_action_v2", "mode": "PAPER_AUTHORIZED_CAPTURE"}
    )
    target = tmp_path / "paper.sqlite3"
    a = execute(target, config, config.identifier, GENESIS, job).to_dict()
    before = (
        target.read_bytes(),
        source_journal.read_bytes(),
        (tmp_path / "capture" / DB).read_bytes(),
    )
    Path(ref.to_dict()["root"], "permission.bin").write_bytes(b"tampered authority")
    with pytest.raises(ValueError, match="permission_hash"):
        execute(target, config, config.identifier, a["head_pin"], job)
    assert before == (
        target.read_bytes(),
        source_journal.read_bytes(),
        (tmp_path / "capture" / DB).read_bytes(),
    )


def test_credential_is_bound_without_exposing_secret(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    valid = readiness(
        plans[0], "a", ref, trust, NOW, True, credential=KEY, capture_root=tmp_path / "capture"
    )
    assert (
        valid.to_dict()["authorization_verified"] and valid.to_dict()["credential_binding_verified"]
    )
    assert not valid.to_dict()["live_provider_collection_eligible"]
    assert KEY not in valid.payload_json and "credential_sha256" not in valid.payload_json
    bad = readiness(
        plans[0],
        "a",
        ref,
        trust,
        NOW,
        True,
        credential="wrong-private-credential",
        capture_root=tmp_path / "capture",
    )
    assert "credential_not_bound" in bad.to_dict()["blockers"][-1]
    assert "wrong-private-credential" not in bad.payload_json
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: pytest.fail("wrong credential contacted provider")
            ),
            trust_env=False,
        ) as client,
        pytest.raises(ValueError, match="credential_not_bound"),
    ):
        run_capture(
            tmp_path / "absent",
            plans[0],
            "a",
            client,
            api_key="wrong-private-credential",
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: NOW,
        )
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "case", ["endpoint", "validity", "issuer", "mirror", "duplicate", "version", "injected_key"]
)
def test_rehashed_semantically_invalid_review(tmp_path: Path, case: str) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    root = Path(ref.to_dict()["root"])
    d = json.loads((root / "grant.json").read_text())
    if case == "endpoint":
        d["endpoint"] = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_isu_base_info"
    elif case == "validity":
        d["valid_until"] = "2024-01-01T00:00:00+00:00"
    elif case == "issuer":
        d["permission"]["issuer"] = "collector"
    elif case == "mirror":
        d["terms"]["source_url"] = "https://unofficial.example/terms"
    elif case == "duplicate":
        d["captures"].append(d["captures"][0])
    elif case == "version":
        d["version"] = "unknown"
    else:
        d["publicKeyBase64"] = "untrusted-self-signed-key"
    grant = FrozenJSON.freeze(d)
    _sign(root, grant)
    ref = FrozenJSON.freeze(ref.to_dict() | {"grant_pin": grant.identifier})
    plan = replace(plans[0], authorization_reference=grant.identifier)
    with pytest.raises(ValueError):
        verify_authorization(plan, "a", ref, trust, NOW)


def test_review_cannot_be_reused_in_another_archive(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: pytest.fail("archive budget bypass")),
            trust_env=False,
        ) as client,
        pytest.raises(ValueError, match="capture_root_outside_reviewed"),
    ):
        run_capture(
            tmp_path / "other-archive",
            plans[0],
            "a",
            client,
            api_key=KEY,
            allow_network=True,
            authorization=ref,
            authorization_trust=trust,
            clock=lambda: NOW,
        )
    assert not (tmp_path / "other-archive").exists()


def test_readiness_reports_wrong_archive_as_blocked(tmp_path: Path) -> None:
    plans, ref, trust = permission_fixture(tmp_path)
    result = readiness(
        plans[0], "a", ref, trust, NOW, True, credential=KEY, capture_root=tmp_path / "different"
    ).to_dict()
    assert not result["authorization_verified"] and not result["ready_for_explicit_capture"]
    assert "capture_root_outside_reviewed_request_budget" in result["blockers"]
