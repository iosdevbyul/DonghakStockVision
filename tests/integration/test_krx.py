import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from donghak_stock_vision.ingestion.pipeline import Pipeline
from donghak_stock_vision.providers.base import ProviderError, RawPage
from donghak_stock_vision.providers.http import RequestGate, RetryingHTTP
from donghak_stock_vision.providers.krx import KRXProvider
from donghak_stock_vision.storage.sqlite import SQLiteStore


def payload() -> dict[str, object]:
    # Synthetic fields based on the official schema; these are not market prices.
    return {
        "OutBlock_1": [
            {
                "BAS_DD": "20240102",
                "ISU_CD": "005930",
                "MKT_NM": "KOSPI",
                "TDD_OPNPRC": "100",
                "TDD_HGPRC": "110",
                "TDD_LWPRC": "90",
                "TDD_CLSPRC": "105",
                "ACC_TRDVOL": "10",
                "ACC_TRDVAL": "1020",
            }
        ]
    }


def test_official_request_auth_mapping_and_cached_market_day(
    tmp_path: Path,
    store: SQLiteStore,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["AUTH_KEY"] == "test-only-not-a-real-key"
        assert str(request.url) == (
            "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20240102"
        )
        return httpx.Response(200, json=payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = KRXProvider(
            "test-only-not-a-real-key", RetryingHTTP(client, RequestGate(tmp_path / "quota.db"))
        )
        result = Pipeline(provider, store).collect(
            ["005930", "000660"], date(2024, 1, 2), date(2024, 1, 2)
        )
    assert len(requests) == 1
    assert (result.succeeded, result.empty, result.changed) == (2, 1, 1)
    bar = store.read("005930", date(2024, 1, 2), date(2024, 1, 2))[0]
    assert (bar.trading_value, bar.provider, bar.adjustment) == (1020, "krx", "unadjusted")
    assert store.check_integrity() == []


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'{"OutBlock_1": {}}',
        b'{"OutBlock_1": [null]}',
        b'{"error": "invalid key"}',
    ],
)
def test_malformed_or_error_response_not_treated_as_empty(tmp_path: Path, body: bytes) -> None:
    with httpx.Client() as client:
        provider = KRXProvider("test", RetryingHTTP(client, RequestGate(tmp_path / "quota.db")))
        with pytest.raises(ProviderError, match="invalid KRX"):
            provider.parse(RawPage(body, datetime.now(UTC), date(2024, 1, 2)), "005930")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ACC_TRDVAL", "-"),
        ("ACC_TRDVOL", "1.5"),
        ("TDD_OPNPRC", None),
        ("BAS_DD", "20240103"),
        ("MKT_NM", "KOSDAQ"),
    ],
)
def test_bad_fields_rejected(tmp_path: Path, field: str, value: object) -> None:
    body = json.loads(json.dumps(payload()))
    body["OutBlock_1"][0][field] = value
    with httpx.Client() as client:
        provider = KRXProvider("test", RetryingHTTP(client, RequestGate(tmp_path / "quota.db")))
        with pytest.raises(ProviderError):
            provider.parse(
                RawPage(json.dumps(body).encode(), datetime.now(UTC), date(2024, 1, 2)), "005930"
            )


def test_missing_key_fails_without_request(tmp_path: Path) -> None:
    with httpx.Client() as client, pytest.raises(ValueError, match="KRX_API_KEY"):
        KRXProvider("", RetryingHTTP(client, RequestGate(tmp_path / "quota.db")))
