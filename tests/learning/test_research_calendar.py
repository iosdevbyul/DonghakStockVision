from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.data.learning import AnalysisError, digest
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy, excluded_dates
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.snapshot import capture, snapshot_bars
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.service import EvaluationService, TrainingService
from donghak_stock_vision.signals.dataset import build_dataset, split_dataset
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, quality, seed


def test_canonical_contract() -> None:
    p = ResearchCalendarPolicy((date(2025, 3, 21), date(2024, 3, 28), date(2025, 3, 21)))
    assert p == ResearchCalendarPolicy.from_dict(p.to_dict())
    assert p.to_dict()["reason"] == "known_incomplete_trading_date"
    assert p.incomplete_dates == (date(2024, 3, 28), date(2025, 3, 21))
    assert digest(p.to_dict()) == p.identifier
    with pytest.raises(FrozenInstanceError):
        p.incomplete_dates = ()  # type: ignore[misc]


@pytest.mark.parametrize("bad", [None, {}, {"version": 2}, {"reason": "holiday"}])
def test_bad_contract(bad: Any) -> None:
    with pytest.raises(AnalysisError):
        ResearchCalendarPolicy.from_dict(bad)


def test_timestamp_not_date() -> None:
    with pytest.raises(AnalysisError):
        ResearchCalendarPolicy((datetime(2025, 3, 21, tzinfo=UTC),))


def test_sessions_labels_split_and_original_db(store: SQLiteStore) -> None:
    source = bars(count=180)
    seed(store, source)
    days = sorted({b.trading_date for b in source})
    policy = ResearchCalendarPolicy((days[20], days[90]))
    kwargs: dict[str, Any] = dict(
        store=store,
        tickers=["000001", "000002"],
        start=days[0],
        end=days[-1],
        cutoff=CUTOFF,
        mode="historical_research",
        quality=quality(source),
        synthetic=True,
    )
    original = capture(**kwargs)
    snapshot = capture(**kwargs, research_calendar=policy)
    assert digest(original) != digest(snapshot)
    assert capture(**kwargs, research_calendar=policy) == snapshot
    assert snapshot["quality"]["sessions"] == original["quality"]["sessions"]  # Not holidays.
    for ticker in ("000001", "000002"):
        series = snapshot_bars(snapshot, ticker)
        assert all(b.trading_date not in policy.incomplete_dates for b in series)
        assert len(series) == len(snapshot_bars(original, ticker)) - 2
        # Expected causal feature equals manually filtered original history.
        expected = [
            b
            for b in snapshot_bars(original, ticker)
            if b.trading_date not in policy.incomplete_dates
        ]
        assert feature_vector(series[:25], snapshot) == feature_vector(expected[:25], snapshot)
        assert any(b.trading_date == days[20] for b in store.read(ticker, days[0], days[-1]))
    samples, _ = build_dataset(snapshot)
    assert all(s.trading_date not in policy.incomplete_dates for s in samples)
    s = next(s for s in samples if s.ticker == "000001" and s.trading_date == days[19])
    assert s.label_end == days[25]  # Five retained sessions; day 20 is not counted.
    assert days[20] not in s.label_sessions
    c = ExplicitResearchSplit(
        ResearchDateRange(days[0], days[69]),
        ResearchDateRange(days[70], days[119]),
        ResearchDateRange(days[120], days[-1]),
    )
    explicit = split_dataset(samples, "historical_research", c)
    for part, rows in explicit["partitions"].items():
        for r in rows:
            assert r["trading_date"] not in excluded_dates(snapshot)
            if part != "test":
                assert r["label_end"] < (days[70] if part == "train" else days[120]).isoformat()
            else:
                assert r["label_end"] <= days[-1].isoformat()
    assert split_dataset(samples, "historical_research")["partitions"]["train"]


def test_pit_forbidden(store: SQLiteStore) -> None:
    with pytest.raises(AnalysisError, match="historical_research"):
        capture(
            store,
            ["000001"],
            date(2020, 1, 2),
            date(2020, 2, 1),
            CUTOFF,
            "point_in_time",
            research_calendar=ResearchCalendarPolicy(()),
        )


def test_model_reproduction_and_excluded_inference(
    snapshot: tuple[str, dict[str, Any]],
    analysis: AnalysisStore,
    tmp_path: Path,
) -> None:
    snap = deepcopy(snapshot[1])
    day = date.fromisoformat(snap["rows"]["000001"][20]["trading_date"])
    snap["research_calendar"] = ResearchCalendarPolicy((day,)).to_dict()
    # Defensive snapshot_bars filtering also covers rows retained in an imported snapshot.
    sid = analysis.put("snapshot", snap)
    result = TrainingService(analysis).train(sid)
    assert result["status"] == "trained"
    evaluated = EvaluationService(analysis).evaluate(result["model_version"], "historical_research")
    assert evaluated["snapshot_id"] == sid
    with pytest.raises(AnalysisError, match="known_incomplete"):
        SignalService(analysis).research(result["model_version"], sid, ["000001"], day)


def test_cli_exposes_explicit_calendar_file() -> None:
    import argparse

    from donghak_stock_vision.learning_cli import add_commands

    parser = argparse.ArgumentParser()
    add_commands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "train",
            "--analysis-db",
            "unused.db",
            "--mode",
            "historical-research",
            "--tickers",
            "005930",
            "--start",
            "2022-01-03",
            "--end",
            "2025-06-30",
            "--research-calendar",
            "research-calendar.json",
        ]
    )
    assert args.research_calendar == Path("research-calendar.json")
