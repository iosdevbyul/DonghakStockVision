from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import TrainingService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, seed


@pytest.fixture
def analysis(tmp_path: Path) -> Iterator[AnalysisStore]:
    instance = AnalysisStore(tmp_path / "analysis.db")
    yield instance
    instance.close()


@pytest.fixture
def snapshot(store: SQLiteStore, analysis: AnalysisStore) -> tuple[str, dict[str, Any]]:
    source = bars()
    seed(store, source)
    snap = capture(
        store,
        ["000001", "000002"],
        source[0].trading_date,
        max(b.trading_date for b in source),
        CUTOFF,
        "historical_research",
        acknowledge=True,
        synthetic=True,
    )
    sid = analysis.put("snapshot", snap)
    return sid, analysis.get("snapshot", sid)


@pytest.fixture
def trained(snapshot: tuple[str, dict[str, Any]], analysis: AnalysisStore) -> dict[str, Any]:
    result = TrainingService(analysis).train(snapshot[0])
    assert result["status"] == "trained", result
    return result
