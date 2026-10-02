"""Offline market-day responses only. Never open the user's real market/analysis DB."""

import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.unit.test_http import Clock

from donghak_stock_vision.cli import main, parser
from donghak_stock_vision.ingestion.market import KRXMarketPipeline
from donghak_stock_vision.ingestion.pipeline import Pipeline
from donghak_stock_vision.providers.base import ProviderError, RawPage, krx_market_scope
from donghak_stock_vision.providers.http import RequestGate, RetryingHTTP
from donghak_stock_vision.providers.krx import KRXProvider
from donghak_stock_vision.storage.sqlite import SQLiteStore

DAY = date(2024, 1, 2)


def row(ticker: str = "005930", day: date = DAY, **changes: Any) -> dict[str, Any]:
    return {
        "ISU_CD": ticker,
        "BAS_DD": day.strftime("%Y%m%d"),
        "MKT_NM": "KOSPI",
        "TDD_OPNPRC": "100",
        "TDD_HGPRC": "110",
        "TDD_LWPRC": "90",
        "TDD_CLSPRC": "105",
        "ACC_TRDVOL": "10",
        "ACC_TRDVAL": "1020",
        **changes,
    }


def provider(client: httpx.Client, path: Path, *, attempts: int = 3) -> KRXProvider:
    clock = Clock()
    gate = RequestGate(path / "quota.db", clock=clock.time, sleep=clock.sleep)
    return KRXProvider(
        "mock-auth-not-a-credential",
        RetryingHTTP(client, gate, attempts=attempts, sleep=clock.sleep),
    )


def test_all_rows_single_request_and_single_raw_page(store: SQLiteStore, tmp_path: Path) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert dict(request.url.params) == {"basDd": "20240102"}
        return httpx.Response(200, json={"OutBlock_1": [row(), row("000660")]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
    assert (result.succeeded, result.failed, result.rows, result.changed) == (1, 0, 2, 2)
    assert len(requests) == 1
    assert [b.ticker for t in ("005930", "000660") for b in store.read(t, DAY, DAY)] == [
        "005930",
        "000660",
    ]
    assert store.connection.execute("SELECT ticker FROM raw_pages").fetchall() == [
        (krx_market_scope("KOSPI"),)
    ]
    assert store.connection.execute("SELECT COUNT(*) FROM bar_sources").fetchone()[0] == 2
    assert store.check_integrity() == []


def test_long_range_requests_scale_with_dates_not_tickers(
    store: SQLiteStore, tmp_path: Path
) -> None:
    # Exceeds the old 128-page cache, so ticker-by-ticker fetch would be detected.
    end = DAY + timedelta(days=190)
    calls: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.params["basDd"]
        calls[key] += 1
        day = datetime.strptime(key, "%Y%m%d").date()
        return httpx.Response(
            200, json={"OutBlock_1": [row(t, day) for t in ("005930", "000660", "035420")]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, end)
    weekdays = sum((DAY + timedelta(days=i)).weekday() < 5 for i in range(191))
    assert weekdays > 128
    assert len(calls) == weekdays and set(calls.values()) == {1}
    assert result.succeeded == weekdays and result.rows == result.changed == weekdays * 3
    assert store.connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == weekdays


def test_recollect_and_coexist_with_existing_ticker(store: SQLiteStore, tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"OutBlock_1": [row(), row("000660")]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert (
            Pipeline(provider(client, tmp_path), store).collect(["005930"], DAY, DAY).changed == 1
        )
        before = store.read("005930", DAY, DAY)
        first = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
        again = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
    assert (first.changed, again.changed, again.rows) == (1, 0, 2)
    assert calls == 3
    assert store.read("005930", DAY, DAY) == before  # First collected_at is retained.
    assert store.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 2
    assert store.check_integrity() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("ISU_CD", "invalid"),
        ("ISU_CD", None),
        ("BAS_DD", "20240103"),
        ("MKT_NM", "KOSDAQ"),
        ("TDD_OPNPRC", None),
        ("TDD_HGPRC", "80"),
        ("TDD_LWPRC", "120"),
        ("TDD_CLSPRC", "NaN"),
        ("ACC_TRDVOL", "-1"),
        ("ACC_TRDVAL", "1.5"),
        ("ACC_TRDVAL", True),
    ],
)
def test_every_market_row_validated_before_any_save(
    store: SQLiteStore, tmp_path: Path, field: str, value: Any
) -> None:
    body = {"OutBlock_1": [row(), row("000660", **{field: value})]}
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
    assert result.failed == 1 and result.changed == result.rows == 0
    assert store.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == 1


@pytest.mark.parametrize("body", [b"bad", b"{}", b'{"OutBlock_1":null}', b'{"OutBlock_1":[null]}'])
def test_invalid_envelope_not_empty(store: SQLiteStore, tmp_path: Path, body: bytes) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
    assert result.failed == 1 and result.empty == 0


def test_empty_holiday_and_weekend(store: SQLiteStore, tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"OutBlock_1": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(
            date(2024, 1, 5), date(2024, 1, 7)
        )
    assert (result.succeeded, result.empty, result.rows, len(calls)) == (1, 1, 0, 1)


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_row_policy(store: SQLiteStore, tmp_path: Path, conflict: bool) -> None:
    second = row(TDD_CLSPRC="106") if conflict else row()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"OutBlock_1": [row(), second]})
        )
    ) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
    assert (result.failed, result.changed) == ((1, 0) if conflict else (0, 1))


def test_sqlite_failure_rolls_back_complete_date(store: SQLiteStore, tmp_path: Path) -> None:
    store.connection.execute(
        "CREATE TRIGGER fail BEFORE INSERT ON bars WHEN NEW.ticker='005930' "
        "BEGIN SELECT RAISE(ABORT,'secret-in-error'); END"
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"OutBlock_1": [row("000660"), row()]})
        )
    ) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
    assert result.failed == 1
    assert store.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0
    assert (
        store.connection.execute("SELECT error FROM collection_runs").fetchone()[0]
        == "IntegrityError"
    )


def test_failure_preserves_existing_then_continues(
    store: SQLiteStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls = []
    secret = "server-echo-do-not-log"

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.params["basDd"]
        calls.append(key)
        return httpx.Response(
            200,
            json={
                "OutBlock_1": [row(BAS_DD=secret)]
                if key == "20240102"
                else [row(day=date(2024, 1, 3))]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"OutBlock_1": [row()]})
        )
    ) as client:
        Pipeline(provider(client, tmp_path), store).collect(["005930"], DAY, DAY)
    before = store.read("005930", DAY, DAY)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, date(2024, 1, 3))
    assert (result.failed, result.succeeded) == (1, 1)
    assert store.read("005930", DAY, DAY) == before
    assert calls == ["20240102", "20240103"]
    assert secret not in caplog.text
    assert secret not in str(
        store.connection.execute("SELECT error FROM collection_runs").fetchall()
    )


def test_source_page_date_and_scoped_provenance(
    store: SQLiteStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with httpx.Client() as client:
        p = provider(client, tmp_path)
        page = RawPage(json.dumps({"OutBlock_1": [row()]}).encode(), datetime.now(UTC), DAY)
        bars = p.parse_market(page)
        wrong = store.archive("audit", "krx", krx_market_scope("KOSDAQ"), page)
        with pytest.raises(ValueError, match="matching raw provenance"):
            store.save(bars, [wrong])
        monkeypatch.setattr(
            p, "fetch_market", lambda day: replace(page, source_date=date(2024, 1, 3))
        )
        assert KRXMarketPipeline(p, store).collect(DAY, DAY).failed == 1
    assert store.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0


def test_retries_are_bounded_and_independent_of_tickers(store: SQLiteStore, tmp_path: Path) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(503, text="secret-body")
        return httpx.Response(200, json={"OutBlock_1": [row(), row("000660")]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY)
    assert count == 2 and result.changed == 2


ARGS = ["collect", "--start", "2024-01-02", "--end", "2024-01-02"]


def test_cli_mutual_exclusion_and_required_selector() -> None:
    for extra in ([], ["--all", "--tickers", "005930"]):
        with pytest.raises(SystemExit) as error:
            parser().parse_args(ARGS + extra)
        assert error.value.code == 2


def test_cli_all_fake_refused_before_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "missing.db"
    assert main(["--db", str(db), *ARGS, "--all", "--provider", "fake"]) == 2
    assert not db.exists()


def test_cli_all_krx_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRX_API_KEY", "mock-auth-not-a-credential")
    calls = []
    real_client = httpx.Client

    def mock_client(**kwargs: Any) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"OutBlock_1": [row(), row("000660")]})

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", mock_client)
    assert (
        main(
            [
                "--db",
                str(tmp_path / "market.db"),
                *ARGS,
                "--all",
                "--provider",
                "krx",
                "--market",
                "KOSPI",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "succeeded": 1,
        "failed": 0,
        "empty": 0,
        "changed": 2,
        "rows": 2,
    }
    assert len(calls) == 1


def test_provider_error_does_not_echo_body_or_cause(tmp_path: Path) -> None:
    import traceback

    secret = "credential-echo-test-only"
    with httpx.Client() as client:
        p = provider(client, tmp_path)
        page = RawPage(
            json.dumps({"OutBlock_1": [row(BAS_DD=secret)]}).encode(), datetime.now(UTC), DAY
        )
        with pytest.raises(ProviderError) as caught:
            p.parse_market(page)
    assert secret not in str(caught.value)
    assert secret not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("source_market,source_date", [("KOSDAQ", DAY), ("KOSPI", None)])
def test_market_provenance_requires_exact_market_and_date(
    store: SQLiteStore, tmp_path: Path, source_market: str, source_date: date | None
) -> None:
    with httpx.Client() as client:
        p = provider(client, tmp_path)
        page = RawPage(json.dumps({"OutBlock_1": [row()]}).encode(), datetime.now(UTC), DAY)
        bars = p.parse_market(page)
        raw_id = store.archive(
            "scope-check",
            "krx",
            krx_market_scope(source_market),
            replace(page, source_date=source_date),
        )
        with pytest.raises(ValueError, match="matching raw provenance"):
            store.save(bars, [raw_id])
    assert store.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0


def test_market_revisions_keep_existing_store_policy(
    store: SQLiteStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = datetime(2024, 2, 1, tzinfo=UTC)
    first = RawPage(json.dumps({"OutBlock_1": [row(), row("000660")]}).encode(), at, DAY)
    revised = RawPage(
        json.dumps({"OutBlock_1": [row(TDD_CLSPRC="106"), row("000660")]}).encode(),
        at + timedelta(days=1),
        DAY,
    )
    pages = iter([first, revised, first])
    with httpx.Client() as client:
        p = provider(client, tmp_path)
        monkeypatch.setattr(p, "fetch_market", lambda day: next(pages))
        pipeline = KRXMarketPipeline(p, store)
        assert pipeline.collect(DAY, DAY).changed == 2
        assert pipeline.collect(DAY, DAY).changed == 1
        assert pipeline.collect(DAY, DAY).failed == 1
    assert store.read("005930", DAY, DAY)[0].close == 106
    assert store.read("000660", DAY, DAY)[0].collected_at == at
    assert store.check_integrity() == []


def test_future_range_rejected_without_requests(store: SQLiteStore, tmp_path: Path) -> None:
    from donghak_stock_vision.data.schema import SEOUL

    with httpx.Client() as client:
        p = provider(client, tmp_path)
        today = datetime.now(SEOUL).date()
        with pytest.raises(ValueError, match="end must be before today"):
            KRXMarketPipeline(p, store).collect(today, today)
        with pytest.raises(ValueError):
            KRXMarketPipeline(p, store).collect(DAY + timedelta(days=1), DAY)
    assert store.connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == 0


def test_explicit_universe_keeps_one_request_per_date_and_old_rows(
    store: SQLiteStore, tmp_path: Path
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["basDd"])
        day = datetime.strptime(calls[-1], "%Y%m%d").date()
        return httpx.Response(200, json={"OutBlock_1": [row(t, day) for t in ("005930", "000660")]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        pipeline = KRXMarketPipeline(provider(client, tmp_path), store)
        pipeline.collect(DAY, DAY)
        old = store.read("005930", DAY, DAY)
        start, end = DAY + timedelta(days=1), DAY + timedelta(days=2)
        result = pipeline.collect(start, end, tickers=["005930", "005930", "035420"])
        repeated = pipeline.collect(start, end, tickers=["005930", "035420"])
    assert result.rows == result.changed == 2
    assert repeated.changed == 0
    assert len(calls) == 3  # One per date; repeated fetches use the existing cache.
    assert store.read("005930", DAY, DAY) == old
    assert store.read("000660", start, end) == []
    assert store.read("035420", start, end) == []
    assert store.check_integrity() == []


@pytest.mark.parametrize("conflict", [False, True])
def test_universe_filter_does_not_hide_invalid_outside_rows(
    store: SQLiteStore, tmp_path: Path, conflict: bool
) -> None:
    outside = (
        [row("000660"), row("000660", TDD_CLSPRC="106")]
        if conflict
        else [row("000660", TDD_LWPRC="999")]
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"OutBlock_1": [row(), *outside]})
        )
    ) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(
            DAY, DAY, tickers=["005930"]
        )
    assert result.failed == 1 and result.changed == 0
    assert store.read("005930", DAY, DAY) == []


@pytest.mark.parametrize("tickers", [[], ["bad"], "005930"])
def test_invalid_universe_rejected_before_request(
    store: SQLiteStore, tmp_path: Path, tickers: Any
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not request")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            KRXMarketPipeline(provider(client, tmp_path), store).collect(DAY, DAY, tickers=tickers)
    assert store.connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()[0] == 0


def test_absent_universe_is_not_a_holiday_claim(store: SQLiteStore, tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"OutBlock_1": [row("000660")]})
        )
    ) as client:
        result = KRXMarketPipeline(provider(client, tmp_path), store).collect(
            DAY, DAY, tickers=["005930"]
        )
    assert result.empty == 1 and result.rows == 0
    body = store.connection.execute("SELECT body FROM raw_pages").fetchone()[0]
    assert len(json.loads(body)["OutBlock_1"]) == 1
