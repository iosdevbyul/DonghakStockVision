import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from donghak_stock_vision.ingestion.pipeline import Pipeline
from donghak_stock_vision.providers.base import ProviderError, RawPage
from donghak_stock_vision.providers.fake import FakeProvider, demo_bars
from donghak_stock_vision.storage.sqlite import SQLiteStore

START, END = date(2024, 1, 2), date(2024, 1, 8)


def test_idempotency_round_trip_and_multiple_tickers(store: SQLiteStore) -> None:
    pipeline = Pipeline(FakeProvider(), store)
    first = pipeline.collect(["005930", "000660", "005930"], START, END)
    second = pipeline.collect(["005930", "000660"], START, END)
    assert (first.succeeded, first.changed, second.changed) == (2, 10, 0)
    assert store.read("005930", START, END) == demo_bars()[:5]
    assert store.check_integrity() == []


def test_incremental_with_overlap_and_fresh_ticker(store: SQLiteStore) -> None:
    provider = FakeProvider()
    pipeline = Pipeline(provider, store)
    pipeline.collect(["005930"], START, date(2024, 1, 4))
    result = pipeline.collect(["005930", "000660"], START, END, incremental=True, overlap_days=1)
    assert provider.calls[-2:] == [("005930", date(2024, 1, 3), END), ("000660", START, END)]
    assert result.changed == 7
    assert len(store.read("005930", START, END)) == 5


def test_late_revision_and_stale_revision(store: SQLiteStore) -> None:
    Pipeline(FakeProvider(), store).collect(["005930"], START, END)
    revised = replace(demo_bars()[0], close=106, collected_at=datetime(2024, 3, 1, tzinfo=UTC))
    result = Pipeline(FakeProvider([revised]), store).collect(["005930"], START, END)
    assert result.changed == 1
    stale = Pipeline(FakeProvider(), store).collect(["005930"], START, END)
    assert stale.failed == 1
    assert store.read("005930", START, END)[0] == revised


class BrokenProvider(FakeProvider):
    def fetch(self, ticker: str, start: date, end: date) -> Iterator[RawPage]:
        yield from super().fetch(ticker, start, end)
        if ticker == "005930":
            raise ProviderError("secret-like text must not be persisted")


def test_partial_network_failure_keeps_existing_and_continues(
    store: SQLiteStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    Pipeline(FakeProvider(), store).collect(["005930"], START, START)
    result = Pipeline(BrokenProvider(), store).collect(["005930", "000660"], START, END)
    assert (result.failed, result.succeeded) == (1, 1)
    assert len(store.read("005930", START, END)) == 1
    assert len(store.read("000660", START, END)) == 5
    assert store.connection.execute("SELECT count(*) FROM raw_pages").fetchone()[0] == 3
    assert (
        store.connection.execute(
            "SELECT error FROM collection_runs WHERE status='failed'"
        ).fetchone()[0]
        == "ProviderError"
    )
    assert "secret-like" not in caplog.text


class InvalidProvider(FakeProvider):
    def fetch(self, ticker: str, start: date, end: date) -> Iterator[RawPage]:
        row = demo_bars()[0].to_dict()
        row["volume"] = None
        yield RawPage(json.dumps([row]).encode(), datetime.now(UTC))


def test_invalid_bar_archived_and_rejected(store: SQLiteStore) -> None:
    result = Pipeline(InvalidProvider(), store).collect(["005930"], START, END)
    assert result.failed == 1
    assert not store.read("005930", START, END)
    assert store.connection.execute("SELECT count(*) FROM raw_pages").fetchone()[0] == 1


def test_empty_not_filled_or_checkpointed(store: SQLiteStore) -> None:
    result = Pipeline(FakeProvider([]), store).collect(["005930"], START, END)
    assert (result.empty, result.changed, result.failed) == (1, 0, 0)
    assert store.latest("005930", START, END) is None


def test_transaction_rolls_back_after_write_error(store: SQLiteStore) -> None:
    Pipeline(FakeProvider(), store).collect(["005930"], START, START)
    before = store.read("005930", START, END)
    store.connection.execute(
        "CREATE TRIGGER fail_insert BEFORE INSERT ON bars WHEN NEW.trading_date='2024-01-04' "
        "BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END"
    )
    result = Pipeline(FakeProvider(), store).collect(["005930"], START, END)
    assert result.failed == 1
    assert store.read("005930", START, END) == before


def test_integrity_detects_corrupted_payload_and_raw(store: SQLiteStore) -> None:
    Pipeline(FakeProvider(), store).collect(["005930"], START, START)
    with store.connection:
        store.connection.execute("UPDATE bars SET payload='{}'")
        store.connection.execute("UPDATE raw_pages SET sha256='bad'")
    issues = store.check_integrity()
    assert any("invalid bar" in issue for issue in issues)
    assert any("checksum" in issue for issue in issues)


def test_future_range_and_invalid_ticker_rejected_before_fetch(store: SQLiteStore) -> None:
    provider = FakeProvider()
    pipeline = Pipeline(provider, store)
    with pytest.raises(ValueError):
        pipeline.collect(["005930"], START, date(9999, 1, 1))
    with pytest.raises(ValueError):
        pipeline.collect(["5930"], START, END)
    assert not provider.calls


def test_provenance_failure_rolls_back(store: SQLiteStore) -> None:
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        store.save(demo_bars()[:1], [999])
    assert not store.read("005930", START, END)


def test_nonoverlapping_series_cannot_mix_providers(store: SQLiteStore) -> None:
    Pipeline(FakeProvider(), store).collect(["005930"], START, START)
    other = FakeProvider([replace(demo_bars()[1], provider="other")])
    other.name = "other"
    result = Pipeline(other, store).collect(["005930"], date(2024, 1, 3), END)
    assert result.failed == 1
    assert len(store.read("005930", START, END)) == 1


def test_wrong_raw_date_cannot_create_untraceable_bar(store: SQLiteStore) -> None:
    bar = demo_bars()[0]
    raw_id = store.archive(
        "test", "fake", bar.ticker, RawPage(b"[]", bar.collected_at, date(2024, 1, 3))
    )
    with pytest.raises(ValueError, match="matching raw"):
        store.save([bar], [raw_id])
    assert not store.read("005930", START, END)
