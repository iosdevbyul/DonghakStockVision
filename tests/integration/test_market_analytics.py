"""Deterministic synthetic-only analytics fixtures; never use protected real market rows."""

import hashlib
import json
import math
import sqlite3
import statistics
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import ROUND_UP, localcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.backtest.test_official_evidence import month

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import CalendarMonthSource
from donghak_stock_vision.backtest.official_evidence import normalize_month
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.storage.market_analytics import analyze_market_data
from donghak_stock_vision.storage.sqlite import SQLiteStore

START, END = date(2024, 6, 3), date(2024, 6, 7)
SPLIT = ExplicitResearchSplit(
    ResearchDateRange(START, date(2024, 6, 10)),
    ResearchDateRange(date(2024, 6, 11), date(2024, 6, 14)),
    ResearchDateRange(date(2024, 6, 17), date(2024, 6, 21)),
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bar(day: int, close: int, ticker: str = "005930") -> DailyBar:
    return DailyBar(
        ticker,
        date(2024, 6, day),
        close,
        close + 10,
        close - 10,
        close,
        day * 100,
        day * 100 * close,
        "krx",
        "KOSPI",
        "unadjusted",
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def fixture(path: Path, *, unique: bool = True) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE bars(ticker TEXT, trading_date TEXT,payload TEXT"
            + (", PRIMARY KEY(ticker,trading_date))" if unique else ")")
        )
        for day, close in ((3, 100), (4, 110), (5, 99), (7, 121)):
            item = bar(day, close)
            conn.execute(
                "INSERT INTO bars VALUES(?,?,?)",
                (item.ticker, item.trading_date.isoformat(), json.dumps(item.to_dict())),
            )


def analyze(path: Path, **kwargs: Any) -> FrozenJSON:
    start = kwargs.pop("start", START)
    end = kwargs.pop("end", END)
    return analyze_market_data(
        path,
        kwargs.pop("ticker", "005930"),
        start,
        end,
        expected_sha256=kwargs.pop("expected_sha256", None) or file_hash(path),
        split=SPLIT,
        split_pin=kwargs.pop("split_pin", SPLIT.identifier),
        data_origin=kwargs.pop("data_origin", "synthetic"),
        **kwargs,
    )


def calendar() -> CalendarMonthSource:
    raw, source = month(2024, 6)
    return CalendarMonthSource(
        raw, FrozenJSON.freeze(source), normalize_month(raw, source).identifier
    )


def test_valid_symbol_summary_and_latest(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    doc = analyze(path).to_dict()
    assert doc["symbol_coverage"] == {
        "observations": 4,
        "first_date": "2024-06-03",
        "last_date": "2024-06-07",
    }
    assert doc["latest_available_ohlcv"]["close"] == 121
    assert doc["price_semantics"] == ["unadjusted"]
    assert doc["security_name"] is None
    assert not doc["access"]["pit_qualified"] and not doc["access"]["oos_approved"]


def test_history_returns_price_and_volume(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    doc = analyze(path, view="history").to_dict()
    assert len(doc["observations"]) == 4
    stats = doc["statistics"]
    assert stats["price_change"] == 21 and float(stats["percentage_change"]) == 21
    assert [float(r["return"]) for r in stats["daily_returns"]] == pytest.approx(
        [0.1, -0.1, 121 / 99 - 1]
    )
    assert float(stats["average_observed_daily_volume"]) == 475
    expected = statistics.stdev([math.log(1.1), math.log(0.9), math.log(121 / 99)])
    assert float(stats["realized_log_return_volatility"]) == pytest.approx(expected, abs=1e-16)
    assert "not_annualized" in stats["volatility_method"]


def test_verified_calendar_holiday_and_weekends_not_missing(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    doc = analyze(path, calendars=(calendar(),), end=date(2024, 6, 9)).to_dict()
    assert doc["quality"]["calendar_status"] == "verified_original_kind_sessions"
    assert doc["quality"]["missing_sessions"] == []
    assert doc["quality"]["off_session_observations"] == []


def test_missing_session_is_not_a_daily_return(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("DELETE FROM bars WHERE trading_date='2024-06-04'")
    doc = analyze(path, calendars=(calendar(),)).to_dict()
    assert doc["quality"]["missing_sessions"] == ["2024-06-04"]
    assert doc["statistics"]["daily_returns"][0]["return"] is None
    assert (
        doc["statistics"]["daily_returns"][0]["unavailable_reason"]
        == "missing_expected_session_between_observations"
    )


def test_no_calendar_does_not_guess_missing_weekdays(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    doc = analyze(path).to_dict()
    assert doc["quality"]["missing_sessions"] is None
    assert "trusted_calendar_not_supplied" in doc["quality"]["calendar_unavailable_reasons"]


def test_unknown_symbol_and_missing_database(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with pytest.raises(ValueError, match="symbol_not_found"):
        analyze(path, ticker="999999")
    with pytest.raises(ValueError, match="market_database_unavailable"):
        analyze_market_data(
            tmp_path / "missing",
            "005930",
            START,
            END,
            expected_sha256="0" * 64,
            split=SPLIT,
            split_pin=SPLIT.identifier,
            data_origin="synthetic",
        )
    assert not (tmp_path / "missing").exists()


def test_empty_range_and_small_sample_volatility(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    empty = analyze(path, start=date(2024, 6, 10), end=date(2024, 6, 10)).to_dict()
    assert empty["status"] == "empty" and empty["statistics"]["price_change"] is None
    one = analyze(path, start=START, end=START).to_dict()
    assert one["statistics"]["realized_log_return_volatility"] is None
    assert "two_eligible" in one["statistics"]["volatility_unavailable_reason"]


@pytest.mark.parametrize(
    "changes",
    [
        {"start": END, "end": START},
        {"max_rows": 0},
        {"max_rows": 2501},
        {"ticker": "bad"},
        {"start": date(2000, 1, 1)},
        {"split_pin": "0" * 64},
    ],
)
def test_invalid_request_before_database_io(tmp_path: Path, changes: dict[str, Any]) -> None:
    path = tmp_path / "never_created"
    with patch(
        "donghak_stock_vision.storage.market_analytics._file_hash",
        side_effect=AssertionError("source touched"),
    ):
        with pytest.raises(ValueError):
            analyze(path, expected_sha256="0" * 64, **changes)
    assert not path.exists()


def test_protected_final_test_before_source_or_calendar_access(tmp_path: Path) -> None:
    with patch(
        "donghak_stock_vision.storage.market_analytics._file_hash",
        side_effect=AssertionError("source touched"),
    ):
        with pytest.raises(ValueError, match="protected_final_test_or_holdout"):
            analyze(
                tmp_path / "absent",
                start=date(2024, 6, 17),
                end=date(2024, 6, 18),
                expected_sha256="0" * 64,
            )
    # Crossing a protected interval is not silently filtered or treated as development data.
    with pytest.raises(ValueError, match="protected_final_test_or_holdout"):
        analyze(tmp_path / "absent", start=START, end=date(2024, 6, 25), expected_sha256="0" * 64)


def test_duplicate_observations_block_statistics(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path, unique=False)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO bars SELECT * FROM bars WHERE trading_date='2024-06-03'")
    doc = analyze(path).to_dict()
    assert doc["quality"]["duplicate_dates"] == ["2024-06-03"]
    assert doc["statistics"]["price_change"] is None
    assert doc["quality"]["invalid_rows"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("close", -1),
        ("volume", -1),
        ("high", 1),
        ("collected_at", "2024-06-01T00:00:00"),
        ("ticker", "000660"),
        ("market", "KOSDAQ"),
        ("trading_date", "2024-06-04"),
    ],
)
def test_invalid_rows_reported_not_repaired(tmp_path: Path, field: str, value: Any) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        payload = json.loads(
            conn.execute("SELECT payload FROM bars WHERE trading_date='2024-06-03'").fetchone()[0]
        )
        payload[field] = value
        conn.execute(
            "UPDATE bars SET payload=? WHERE trading_date='2024-06-03'", (json.dumps(payload),)
        )
    doc = analyze(path, view="history").to_dict()
    assert doc["quality"]["invalid_rows"] == 1
    assert doc["observations"][0]["ohlcv"] is None
    assert doc["statistics"]["realized_log_return_volatility"] is None


def test_collection_time_regressions_are_not_publication_claims(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        payload = json.loads(
            conn.execute("SELECT payload FROM bars WHERE trading_date='2024-06-04'").fetchone()[0]
        )
        payload["collected_at"] = "2025-12-01T00:00:00+00:00"
        conn.execute(
            "UPDATE bars SET payload=? WHERE trading_date='2024-06-04'", (json.dumps(payload),)
        )
    doc = analyze(path).to_dict()
    assert doc["quality"]["collection_timestamp_regressions"] == ["2024-06-04"]
    assert not doc["access"]["pit_qualified"]


def test_read_only_and_stable_json_decimal_context(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    before = file_hash(path)
    first = analyze(path)
    with localcontext() as context:
        context.prec = 8
        context.rounding = ROUND_UP
        assert analyze(path) == first
    assert file_hash(path) == before
    assert not list(tmp_path.glob("*-wal")) and not list(tmp_path.glob("*-journal"))
    assert first.payload_json == analyze(path).payload_json


def test_query_limits_and_large_other_symbol_data(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        # Outside selected symbol: never deserialize these deliberately invalid payloads.
        conn.executemany(
            "INSERT INTO bars VALUES(?,?,?)",
            ((f"{i:06d}", "2024-06-03", "not-json") for i in range(10000, 30000)),
        )
    doc = analyze(path, max_rows=4).to_dict()
    assert doc["dataset_coverage"]["observations"] == 20004
    assert len(doc["statistics"]["daily_returns"]) == 3
    with pytest.raises(ValueError, match="observation_limit_exceeded"):
        analyze(path, max_rows=3)


def test_latest_outside_range_not_implicitly_loaded(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    doc = analyze(path, end=date(2024, 6, 4)).to_dict()
    assert doc["latest_in_range"]["trading_date"] == "2024-06-04"
    assert doc["latest_available_ohlcv"] is None
    assert doc["symbol_coverage"]["last_date"] == "2024-06-07"


def test_synthetic_label_and_staleness_relative_to_dataset(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        item = bar(10, 100, "000660")
        conn.execute(
            "INSERT INTO bars VALUES(?,?,?)",
            (item.ticker, item.trading_date.isoformat(), json.dumps(item.to_dict())),
        )
    doc = analyze(path).to_dict()
    assert doc["data_origin"] == "synthetic"
    assert doc["quality"]["stale_relative_to_dataset"] and doc["quality"]["lag_calendar_days"] == 3
    assert doc["quality"]["wall_clock_staleness"] is None


def test_calendar_wrong_pin_and_adjustment_mix(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    owner = calendar()
    with pytest.raises(ValueError):
        analyze(path, calendars=(CalendarMonthSource(owner.raw, owner.source, "0" * 64),))
    with closing(sqlite3.connect(path)) as conn, conn:
        payload = json.loads(
            conn.execute("SELECT payload FROM bars WHERE trading_date='2024-06-04'").fetchone()[0]
        )
        payload["adjustment"] = "adjusted"
        conn.execute(
            "UPDATE bars SET payload=? WHERE trading_date='2024-06-04'", (json.dumps(payload),)
        )
    doc = analyze(path).to_dict()
    assert "mixed_adjustment_semantics" in doc["quality"]["warnings"]
    assert doc["statistics"]["price_change"] is None


def test_cli_all_three_views_without_writable_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    split_file = tmp_path / "split.json"
    split_file.write_text(json.dumps(SPLIT.to_dict()))
    common = [
        "--db",
        str(path),
        "--db-sha256",
        file_hash(path),
        "--symbol",
        "005930",
        "--start",
        str(START),
        "--end",
        str(END),
        "--split",
        str(split_file),
        "--split-pin",
        SPLIT.identifier,
        "--data-origin",
        "synthetic",
    ]
    with patch.object(
        SQLiteStore, "__init__", side_effect=AssertionError("writable store forbidden")
    ):
        for view in ("summary", "history", "quality"):
            assert main(["market-data", view, *common]) == 0
            assert json.loads(capsys.readouterr().out)["view"] == view


def test_active_journal_and_wrong_database_pin(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with pytest.raises(ValueError, match="database_fingerprint_mismatch"):
        analyze(path, expected_sha256="0" * 64)
    Path(str(path) + "-wal").write_bytes(b"active")
    with pytest.raises(ValueError, match="active_database_journal"):
        analyze(path)
