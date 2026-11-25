"""Synthetic boundary checks for the retained prediction audit."""

from datetime import date, datetime
from pathlib import Path

import pytest

from donghak_stock_vision.data.learning import Sample
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.features.research import FEATURES_V2
from donghak_stock_vision.storage.prediction_audit import _outcomes, _prices

WINDOW = {"start": "2026-10-06", "end": "2026-11-13"}


def test_retained_row_uses_delayed_reference_and_original_neutral_band() -> None:
    raw = Sample(
        "000001",
        date(2026, 10, 6),
        datetime(2026, 10, 7, 19, tzinfo=SEOUL),
        (0.0,) * len(FEATURES_V2),
        "up",
        1,
        date(2026, 10, 8),
        date(2026, 10, 8),
        datetime(2026, 10, 12, 19, tzinfo=SEOUL),
        "labeled",
        "a" * 64,
        (date(2026, 10, 8),),
    ).to_dict()
    source = dict(evaluation_window=WINDOW, goals={"1": dict(samples=[raw])})
    case = dict(
        horizon=1,
        rows=[dict(ticker="000001", day="2026-10-06", label_end="2026-10-08", label=1, old=0.5)],
    )
    record = dict(experiment_id="original", model_sha256="model", result_id="result")
    prices = {
        ("000001", "2026-10-06"): 12_000,
        ("000001", "2026-10-07"): 10_000,
        ("000001", "2026-10-08"): 10_021,
    }
    outcome = _outcomes(case, source, prices, score_key="old", experiment=record, threshold=0.5)[0]
    assert outcome["reference_date"] == "2026-10-07" and outcome["reference_price"] == 10_000
    assert outcome["actual_return"] == pytest.approx(0.0021)
    assert outcome["outcome"] == "correct" and outcome["evaluation_id"] == "result"
    prices["000001", "2026-10-08"] = 10_020
    with pytest.raises(ValueError, match="price_label_mismatch"):
        _outcomes(case, source, prices, score_key="old", experiment=record, threshold=0.5)
    outcome = _outcomes(case, source, {}, score_key="old", experiment=record, threshold=0.5)[0]
    assert outcome["actual_return"] is None and outcome["outcome"] == "correct"


def test_prices_scope_precedes_store_access(tmp_path: Path) -> None:
    proof = dict(day="2026-09-30")
    sources = {"cohort": dict(tickers=["000001"], operational_proofs=[proof])}
    with pytest.raises(ValueError, match="batch_scope"):
        _prices(tmp_path, sources, WINDOW)
    proof["day"] = "2026-10-06"
    assert _prices(tmp_path, sources, WINDOW) == ({}, ["2026-10-06"], ["2026-10-06"])
