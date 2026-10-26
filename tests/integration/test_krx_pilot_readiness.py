"""First-pilot safety regressions. All documents/prices/transports are synthetic."""

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
from tests.integration.test_krx_forward_authorization import KEY, NOW, permission_fixture
from tests.integration.test_krx_operator_policy import package

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.storage.forward_capture import (
    DB,
    readback,
    run_capture,
    verify_capture,
    verify_operator_capture_authorization,
)
from donghak_stock_vision.storage.krx_authorization import OPERATOR


def market_body(plan: ForwardCapturePlan) -> dict[str, Any]:
    row = {
        "BAS_DD": plan.day.strftime("%Y%m%d"),
        "ISU_CD": "005930",
        "MKT_NM": "KOSPI",
        "TDD_OPNPRC": "100",
        "TDD_HGPRC": "110",
        "TDD_LWPRC": "90",
        "TDD_CLSPRC": "105",
        "ACC_TRDVOL": "10",
        "ACC_TRDVAL": "1050",
    }
    return {"OutBlock_1": [row | {"ISU_CD": "000660"}, row, row | {"ISU_CD": "00088K"}]}


def capture(
    root: Path,
    plan: ForwardCapturePlan,
    ref: FrozenJSON,
    client: httpx.Client,
    pin: str | None = None,
) -> FrozenJSON:
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
        trusted_resume_pin=pin,
        clock=lambda: NOW,
        monotonic=lambda: 0,
        sleep=lambda _: None,
    )


def test_whole_market_original_retained_only_samsung_ingested_and_resume_offline(
    tmp_path: Path,
) -> None:
    plan, ref, _ = package(tmp_path)
    root = tmp_path / "capture"
    raw = json.dumps(market_body(plan)).encode()
    calls = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert str(request.url) == (
            "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd="
            + plan.day.strftime("%Y%m%d")
        )
        assert request.method == "GET" and request.headers["AUTH_KEY"] == KEY
        return httpx.Response(200, content=raw)

    with (
        patch("socket.create_connection", side_effect=AssertionError("live network forbidden")),
        httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client,
    ):
        owner = capture(root, plan, ref, client)
        assert owner.to_dict()["ingestion"] == [["success", 1, None]]
        response = owner.to_dict()["responses"][0]
        assert len(response["parsed_bars"]) == 1
        assert response["parsed_bars"][0]["ticker"] == "005930"
        assert response["receipt"]["rawSha256"] == hashlib.sha256(raw).hexdigest()
        assert readback(root, plan, "a", owner.identifier, 0) == raw
        with closing(sqlite3.connect(f"{(root / DB).as_uri()}?mode=ro", uri=True)) as db:
            assert db.execute("SELECT ticker FROM bars").fetchall() == [("005930",)]
        with pytest.raises(ValueError, match="trusted_resume_pin_required"):
            capture(root, plan, ref, client)
        assert capture(root, plan, ref, client, owner.identifier) == owner
        assert verify_capture(root, plan, "a", owner.identifier) == owner
        assert (
            verify_operator_capture_authorization(root, plan, "a", owner.identifier, ref, NOW)
            == "authorized_provider_mock_fixture"
        )
    assert len(calls) == 1
    assert owner.to_dict()["publication_at"] is None
    assert not owner.to_dict()["pit_qualified"]


@pytest.mark.parametrize(
    "case",
    [
        "missing_array",
        "malformed_array",
        "missing_field",
        "bad_number",
        "wrong_day",
        "wrong_market",
        "other_symbols_only",
        "duplicate_samsung",
        "invalid_unrelated_row",
        "invalid_json",
    ],
)
def test_unexpected_response_is_retained_but_not_ingested(tmp_path: Path, case: str) -> None:
    plan, ref, _ = package(tmp_path)
    body = market_body(plan)
    rows = body["OutBlock_1"]
    if case == "missing_array":
        body = {"error": "fixture error; not a provider response"}
    elif case == "malformed_array":
        body = {"OutBlock_1": {}}
    elif case == "missing_field":
        del rows[1]["TDD_CLSPRC"]
    elif case == "bad_number":
        rows[1]["TDD_CLSPRC"] = "not-a-price"
    elif case == "wrong_day":
        rows[1]["BAS_DD"] = "20250702"
    elif case == "wrong_market":
        rows[1]["MKT_NM"] = "KOSDAQ"
    elif case == "other_symbols_only":
        rows.pop(1)
    elif case == "duplicate_samsung":
        rows.append(dict(rows[1]))
    elif case == "invalid_unrelated_row":
        rows[0]["TDD_CLSPRC"] = "-"
    raw = b"not JSON" if case == "invalid_json" else json.dumps(body).encode()
    calls = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=raw)

    root = tmp_path / "capture"
    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        owner = capture(root, plan, ref, client)
        doc = owner.to_dict()
        assert not doc["ingestion"]
        assert doc["responses"][0]["parse_error"] == "invalid_or_missing_scoped_bar"
        assert readback(root, plan, "a", owner.identifier, 0) == raw
        assert capture(root, plan, ref, client, owner.identifier) == owner
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["query", "cookie", "host", "header_cookie", "authorization"])
@pytest.mark.parametrize("policy", ["operator", "independent"])
def test_client_defaults_cannot_modify_reviewed_request(
    tmp_path: Path, kind: str, policy: str
) -> None:
    kwargs: dict[str, Any] = {"allow_network": True, "clock": lambda: NOW}
    if policy == "operator":
        plan, ref, _ = package(tmp_path)
        kwargs.update(authorization_policy=OPERATOR, operator_confirmed=True)
    else:
        plans, ref, trust = permission_fixture(tmp_path)
        plan = plans[0]
        kwargs["authorization_trust"] = trust
    defaults: dict[str, Any]
    if kind == "query":
        defaults = {"params": {"basDd": "20250101"}}
    elif kind == "cookie":
        defaults = {"cookies": {"x": "y"}}
    else:
        header = {"host": "Host", "header_cookie": "Cookie", "authorization": "Authorization"}[kind]
        defaults = {"headers": {header: "unreviewed-fixture-value"}}
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("request scope changed")),
        trust_env=False,
        **defaults,
    ) as client:
        with pytest.raises(ValueError, match="provider_client_default_parameters_or_cookies"):
            run_capture(
                tmp_path / "capture", plan, "a", client, api_key=KEY, authorization=ref, **kwargs
            )
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("kind", ["retries", "tls", "redirects", "environment", "hooks"])
def test_live_transport_preflight_rejects_hidden_behaviors_without_network(
    tmp_path: Path, kind: str
) -> None:
    plans, ref, trust = permission_fixture(tmp_path, transport="provider_https")
    kwargs: dict[str, Any] = {"trust_env": False}
    if kind == "retries":
        kwargs["transport"] = httpx.HTTPTransport(retries=1)
    elif kind == "tls":
        kwargs["verify"] = False
    elif kind == "redirects":
        kwargs["follow_redirects"] = True
    elif kind == "environment":
        kwargs["trust_env"] = True
    else:
        kwargs["event_hooks"] = {"request": [lambda r: None]}
    with (
        patch(
            "httpx.HTTPTransport.handle_request", side_effect=AssertionError("network forbidden")
        ),
        httpx.Client(**kwargs) as client,
        pytest.raises(ValueError, match="verified_direct_https_transport_required"),
    ):
        run_capture(
            tmp_path / "capture",
            plans[0],
            "a",
            client,
            api_key=KEY,
            authorization=ref,
            authorization_trust=trust,
            allow_network=True,
            clock=lambda: NOW,
        )
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500, 200])
def test_one_request_budget_including_redirect_failure_and_oversized_body(
    tmp_path: Path, status: int
) -> None:
    plan, ref, _ = package(tmp_path)
    # Existing owner reviewed a smaller byte budget; no expansion of approved scope.
    calls = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://example.invalid/forbidden"},
            content=b"x" * (plan.max_response_bytes + 1) if status == 200 else b"error",
        )

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        owner = capture(tmp_path / "capture", plan, ref, client)
        doc = owner.to_dict()
        assert len(calls) == 1 and not doc["ingestion"]
        assert doc["stop_reason"] == ("response_byte_budget" if status == 200 else f"HTTP_{status}")
        assert doc["responses"][0]["body_bytes"] <= plan.max_response_bytes
        assert capture(tmp_path / "capture", plan, ref, client, owner.identifier) == owner
    assert len(calls) == 1


def test_protected_scope_stops_before_source_or_policy_io(tmp_path: Path) -> None:
    plan, _, _ = package(tmp_path)
    with patch("pathlib.Path.open", side_effect=AssertionError("protected evidence read")):
        with pytest.raises(ValueError, match="forbidden_partition"):
            replace(plan, partition="final_test")
