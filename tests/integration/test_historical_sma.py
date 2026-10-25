"""Synthetic-only retrospective analysis; private market data is an operator demo only."""

import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_market_analytics import (
    END,
    SPLIT,
    START,
    bar,
    calendar,
    file_hash,
    fixture,
)

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.storage.historical_sma import MODE, analyze_historical_sma
from donghak_stock_vision.strategy.forward_reference import calculate


def analyze(path: Path, **kwargs: Any) -> FrozenJSON:
    return analyze_historical_sma(
        path,
        kwargs.pop("ticker", "005930"),
        kwargs.pop("start", START),
        kwargs.pop("end", END),
        expected_sha256=kwargs.pop("expected_sha256", file_hash(path)),
        split=kwargs.pop("split", SPLIT),
        split_pin=kwargs.pop("split_pin", SPLIT.identifier),
        partition=kwargs.pop("partition", "training"),
        data_origin=kwargs.pop("data_origin", "synthetic"),
        fast=kwargs.pop("fast", 1),
        slow=kwargs.pop("slow", 2),
        **kwargs,
    )


def test_buy_sell_warmup_and_exact_indicators(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    report = analyze(path).to_dict()
    assert report["version"] == "historical_sma_analysis_v1"
    assert report["analysis_mode"] == MODE
    assert report["observation_count"] == 4 and report["warm_up_count"] == 2
    assert report["signal_counts"] == {"BUY": 1, "SELL": 1, "HOLD": 0, "INSUFFICIENT_DATA": 2}
    rows = report["observations"]
    assert [r["signal"] for r in rows] == ["INSUFFICIENT_DATA", "INSUFFICIENT_DATA", "SELL", "BUY"]
    assert rows[0]["fast_sma"] is rows[0]["slow_sma"] is None
    assert rows[2]["fast_sma"] == {"sum": 99, "count": 1, "mean": "99.000000000000000000"}
    assert rows[2]["slow_sma"] == {"sum": 209, "count": 2, "mean": "104.500000000000000000"}
    assert rows[-1]["close"] == 121 and rows[-1]["trading_date"] == "2024-06-07"
    assert (
        rows[-1]["source_ohlcv_fingerprint"] == FrozenJSON.freeze(bar(7, 121).to_dict()).identifier
    )
    assert all(r["historical_decision_at"] is None and r["publication_at"] is None for r in rows)
    assert not report["research_scope"]["historical_pit_qualified"]
    assert not report["research_scope"]["oos_approved"]
    assert report["source"]["classification"] == "synthetic"


def test_hold(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as c, c:
        for day in (3, 4, 5, 7):
            c.execute(
                "UPDATE bars SET payload=? WHERE trading_date=?",
                (json.dumps(bar(day, 100).to_dict()), f"2024-06-{day:02}"),
            )
    report = analyze(path).to_dict()
    assert report["signal_counts"]["HOLD"] == 2
    assert report["signal_counts"]["BUY"] == report["signal_counts"]["SELL"] == 0


def test_insufficient_and_empty_window(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    report = analyze(path, end=date(2024, 6, 4)).to_dict()
    assert report["status"] == "insufficient_data" and report["warm_up_count"] == 2
    assert all(r["warm_up_only"] for r in report["observations"])
    empty = analyze(path, start=date(2024, 6, 6), end=date(2024, 6, 6)).to_dict()
    assert empty["status"] == "empty" and empty["observations"] == []


def test_prefix_no_lookahead_or_hidden_warmup(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    full = analyze(path).to_dict()["observations"]
    prefix = analyze(path, end=date(2024, 6, 5)).to_dict()["observations"]
    assert full[:3] == prefix
    later = analyze(path, start=date(2024, 6, 5)).to_dict()
    assert later["observation_count"] == later["warm_up_count"] == 2
    assert all(r["signal"] == "INSUFFICIENT_DATA" for r in later["observations"])
    with patch("donghak_stock_vision.storage.historical_sma.calculate", wraps=calculate) as calls:
        analyze(path)
    assert [c.args[0] for c in calls.call_args_list] == [
        (100,),
        (100, 110),
        (100, 110, 99),
        (110, 99, 121),
    ]


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"fast": 2, "slow": 2}, "sma_parameters"),
        ({"fast": 0}, "sma_parameters"),
        ({"slow": 31}, "sma_parameters"),
        ({"fast": True}, "sma_parameters"),
        ({"start": END, "end": START}, "start <= end"),
        ({"partition": "final_test"}, "partition_forbidden"),
        ({"partition": "oos"}, "partition_forbidden"),
        ({"partition": "final_holdout"}, "partition_forbidden"),
        ({"partition": "tuning"}, "outside_declared"),
        ({"end": date(2024, 6, 11)}, "outside_declared"),
        ({"start": date(2024, 6, 10), "end": date(2024, 6, 17)}, "protected_final"),
        ({"split_pin": "0" * 64}, "research_split_pin"),
        ({"max_rows": 2501}, "invalid_observation_limit"),
    ],
)
def test_guards_before_source_io(tmp_path: Path, updates: dict[str, Any], reason: str) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with patch(
        "donghak_stock_vision.storage.market_analytics._file_hash",
        side_effect=AssertionError("source I/O"),
    ):
        with pytest.raises(ValueError, match=reason):
            analyze(path, **updates)


@pytest.mark.parametrize(
    "change", [{"close": 0}, {"open": None}, {"volume": -1}, {"market": "KOSDAQ"}]
)
def test_invalid_ohlcv_rejected_not_dropped(tmp_path: Path, change: dict[str, Any]) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as c, c:
        d = json.loads(c.execute("SELECT payload FROM bars LIMIT 1").fetchone()[0])
        d.update(change)
        c.execute(
            "UPDATE bars SET payload=? WHERE trading_date=?", (json.dumps(d), d["trading_date"])
        )
    with patch(
        "donghak_stock_vision.storage.historical_sma.calculate",
        side_effect=AssertionError("calculator"),
    ):
        with pytest.raises(ValueError, match="invalid_or_duplicate"):
            analyze(path)


def test_duplicate_dates_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path, unique=False)
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("INSERT INTO bars SELECT * FROM bars LIMIT 1")
    with pytest.raises(ValueError, match="invalid_or_duplicate"):
        analyze(path)


def test_adjustment_ambiguity_is_not_silently_adjusted(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as c, c:
        for day in (3, 4, 5, 7):
            d = bar(day, 100).to_dict() | {"adjustment": "unknown"}
            c.execute(
                "UPDATE bars SET payload=? WHERE trading_date=?", (json.dumps(d), d["trading_date"])
            )
    with pytest.raises(ValueError, match="unadjusted_prices"):
        analyze(path)


def test_missing_symbol_database_and_bad_hash(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with pytest.raises(ValueError, match="symbol_not_found"):
        analyze(path, ticker="000660")
    with pytest.raises(ValueError, match="fingerprint_mismatch"):
        analyze(path, expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="market_database_unavailable"):
        analyze_historical_sma(
            tmp_path / "absent",
            "005930",
            START,
            END,
            expected_sha256="0" * 64,
            split=SPLIT,
            split_pin=SPLIT.identifier,
            partition="training",
            data_origin="synthetic",
            fast=1,
            slow=2,
        )


def test_no_interpolation_with_calendar_gap(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("DELETE FROM bars WHERE trading_date='2024-06-04'")
    d = analyze(path, calendars=(calendar(),)).to_dict()
    assert d["quality"]["missing_sessions"] == ["2024-06-04"]
    assert [r["trading_date"] for r in d["observations"]] == [
        "2024-06-03",
        "2024-06-05",
        "2024-06-07",
    ]
    assert "missing_expected_sessions" in d["quality"]["warnings"]
    assert d["warm_up_count"] == 2


def test_stable_json_and_no_writes_or_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from donghak_stock_vision.storage.analysis import AnalysisStore
    from donghak_stock_vision.storage.decision import DecisionStore

    path = tmp_path / "market.db"
    fixture(path)
    audit = tmp_path / "analysis.db"
    decisions = tmp_path / "decisions.db"
    AnalysisStore(audit).close()
    DecisionStore(decisions).close()
    journal = tmp_path / "forward-journal"
    journal.write_bytes(b"untouched forward journal sentinel")
    protected = {p: p.read_bytes() for p in (path, audit, decisions, journal)}
    split_file = tmp_path / "split.json"
    split_file.write_text(json.dumps(SPLIT.to_dict()))
    command = [
        "market-data",
        "sma-analysis",
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
        "--fast",
        "1",
        "--slow",
        "2",
        "--partition",
        "training",
        "--split",
        str(split_file),
        "--split-pin",
        SPLIT.identifier,
        "--data-origin",
        "synthetic",
    ]
    with (
        patch("httpx.Client.__init__", side_effect=AssertionError("network")),
        patch(
            "donghak_stock_vision.storage.analysis.AnalysisStore.__init__",
            side_effect=AssertionError("audit"),
        ),
        patch(
            "donghak_stock_vision.storage.decision.DecisionStore.__init__",
            side_effect=AssertionError("audit"),
        ),
        patch(
            "donghak_stock_vision.storage.sqlite.SQLiteStore.__init__",
            side_effect=AssertionError("writer"),
        ),
        patch(
            "donghak_stock_vision.storage.forward_signal_journal.append_signal",
            side_effect=AssertionError("journal"),
        ),
        patch(
            "donghak_stock_vision.trading.paper.DeterministicPaperBroker.submit",
            side_effect=AssertionError("order"),
        ),
    ):
        assert main(command) == 0
        first = capsys.readouterr().out
        assert main(command) == 0
        assert capsys.readouterr().out == first
    assert json.loads(first)["analysis_mode"] == MODE
    for p, before in protected.items():
        assert p.read_bytes() == before
    assert not Path(str(path) + "-journal").exists()


def test_large_window_reuses_bounded_original_calculator(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    fixture(path)
    start = date(2024, 1, 2)
    dates: list[date] = []
    day = start
    while len(dates) < 80:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    split = ExplicitResearchSplit(
        ResearchDateRange(start, date(2024, 6, 10)), SPLIT.validation, SPLIT.final_test
    )
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("DELETE FROM bars")
        for day in dates:
            d = bar(3, 100).to_dict() | {"trading_date": day.isoformat()}
            c.execute("INSERT INTO bars VALUES(?,?,?)", ("005930", day.isoformat(), json.dumps(d)))
        for i in range(20000):
            c.execute(
                "INSERT INTO bars VALUES(?,?,?)",
                (
                    "000660",
                    (date(2000, 1, 1) + timedelta(days=i)).isoformat(),
                    "invalid unrelated payload",
                ),
            )
    d = analyze(
        path, start=start, end=dates[-1], split=split, split_pin=split.identifier, max_rows=80
    ).to_dict()
    assert d["observation_count"] == 80 and d["signal_counts"]["HOLD"] == 78
    with pytest.raises(ValueError, match="observation_limit"):
        analyze(
            path, start=start, end=dates[-1], split=split, split_pin=split.identifier, max_rows=79
        )
