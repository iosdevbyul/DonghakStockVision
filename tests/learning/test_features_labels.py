import math
import statistics
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from donghak_stock_vision.data.learning import AnalysisError
from donghak_stock_vision.data.snapshot import snapshot_bars
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.signals.dataset import build_dataset, context, label_event


def test_manual_features_and_actual_value(snapshot: tuple[str, dict[str, Any]]) -> None:
    snap = snapshot[1]
    bars = snapshot_bars(snap, "000001")[:11]
    result = feature_vector(bars, snap)
    returns = [math.log(b.close / a.close) for a, b in zip(bars, bars[1:], strict=False)]
    assert result == pytest.approx(
        (
            returns[-1],
            math.log(bars[-1].close / bars[-4].close),
            math.log(bars[-1].close / bars[-6].close),
            bars[-1].close / statistics.mean(b.close for b in bars[-10:]) - 1,
            statistics.stdev(returns),
            (bars[-1].high - bars[-1].low) / bars[-1].close,
            math.log(bars[-1].volume / statistics.mean(b.volume for b in bars[:-1])),
            math.log(bars[-1].trading_value / statistics.mean(b.trading_value for b in bars[:-1])),
        )
    )
    changed = [*bars[:-1], replace(bars[-1], trading_value=bars[-1].trading_value * 2)]
    new = feature_vector(changed, snap)
    assert new[:-1] == result[:-1]
    assert new[-1] == pytest.approx(result[-1] + math.log(2))


def test_future_mutation_does_not_change_past_features(
    snapshot: tuple[str, dict[str, Any]],
) -> None:
    snap = snapshot[1]
    original, _ = build_dataset(snap)
    altered = deepcopy(snap)
    altered["rows"]["000001"][100]["volume"] *= 10
    changed, _ = build_dataset(altered)
    before = [(s.trading_date, s.values) for s in original if s.trading_date < date(2020, 4, 1)]
    after = [(s.trading_date, s.values) for s in changed if s.trading_date < date(2020, 4, 1)]
    assert after == before


def test_warmup_unavailable_and_no_trade(snapshot: tuple[str, dict[str, Any]]) -> None:
    snap = snapshot[1]
    bars = snapshot_bars(snap, "000001")[:11]
    with pytest.raises(AnalysisError, match="insufficient_history"):
        feature_vector(bars[:10], snap)
    with pytest.raises(AnalysisError, match="point_in_time"):
        feature_vector(bars, snap, datetime(2020, 1, 20, tzinfo=UTC))
    no_trade = [*bars[:-1], replace(bars[-1], volume=0, trading_value=0)]
    with pytest.raises(AnalysisError, match="no_trade"):
        feature_vector(no_trade, snap)


@pytest.mark.parametrize(
    "prior,outcome,expected",
    [
        (-0.1, "upper", 1),
        (-0.1, "lower", 0),
        (-0.1, "none", 0),
        (0.1, "upper", 0),
        (0.1, "lower", 1),
        (0.1, "none", 0),
        (0.0, "upper", None),
        (-0.1, "both", None),
    ],
)
def test_conditional_barrier_definition(
    snapshot: tuple[str, dict[str, Any]], prior: float, outcome: str, expected: int | None
) -> None:
    snap = snapshot[1]
    source = snapshot_bars(snap, "000001")[:16]
    source = [replace(b, open=10000, high=10050, low=9950, close=10000) for b in source]
    if outcome in {"upper", "both"}:
        source[11] = replace(source[11], high=10300)
    if outcome in {"lower", "both"}:
        source[11] = replace(source[11], low=9700)
    values = (0.0, 0.0, prior, 0.0, 0.005, 0.01, 0.0, 0.0)
    anchor = datetime.combine(source[10].trading_date, datetime.min.time(), UTC)
    label, start, end, _, status = label_event(source, 10, anchor, values, snap)
    assert label == expected
    if prior != 0:
        assert end == source[15].trading_date
        assert start == source[11].trading_date
    if outcome == "both":
        assert status == "ambiguous"
    if prior == 0:
        assert context(values) is None and status == "no_context"


def test_immature_not_negative(snapshot: tuple[str, dict[str, Any]]) -> None:
    snap = snapshot[1]
    source = snapshot_bars(snap, "000001")[:15]
    values = (0.0, 0.0, -0.1, 0.0, 0.01, 0.01, 0.0, 0.0)
    anchor = datetime.combine(source[10].trading_date, datetime.min.time(), UTC)
    assert label_event(source, 10, anchor, values, snap)[4] == "immature_label"


def test_event_and_discontinuity_excluded(snapshot: tuple[str, dict[str, Any]]) -> None:
    snap = deepcopy(snapshot[1])
    source = snapshot_bars(snap, "000001")[:12]
    snap["quality"] = {
        "coverage": {"000001": {"excluded_dates": [source[5].trading_date.isoformat()]}}
    }
    with pytest.raises(AnalysisError, match="corporate_action"):
        feature_vector(source, snap)
    snap["quality"] = None
    source[-1] = replace(source[-1], open=50000, low=49000, high=51000, close=50000)
    with pytest.raises(AnalysisError, match="discontinuity"):
        feature_vector(source, snap)
