"""Source/output routing must preserve immutable artifact provenance."""

import sqlite3
from pathlib import Path

import pytest

from donghak_stock_vision.data.learning import AnalysisError
from donghak_stock_vision.storage.analysis import (
    AnalysisStore,
    ReadOnlyAnalysisStore,
    ResearchRunStore,
)


def test_routing_original_timestamps_checksums_and_isolation(tmp_path: Path) -> None:
    path = tmp_path / "source.db"
    source = AnalysisStore(path)
    mid = source.put("model", {"fixture": "not-an-operational-model"})
    stamp = source.created_at("model", mid)
    source.close()
    before = path.read_bytes()
    reader = ReadOnlyAnalysisStore(path)
    output = AnalysisStore(tmp_path / "output.db")
    # A shadowed source artifact in output never replaces source provenance.
    output.put("model", {"untrusted": "output cannot supply source models"})
    run = ResearchRunStore(reader, output)
    assert run.get("model", mid) == {"fixture": "not-an-operational-model"}
    assert run.created_at("model", mid) == stamp
    sid = run.put("signal", {"fixture": "generated-analysis"})
    assert run.get("signal", sid) == output.get("signal", sid)
    assert run.created_at("signal", sid) == output.created_at("signal", sid)
    with pytest.raises(AnalysisError, match="missing_signal"):
        reader.get("signal", sid)
    with pytest.raises(AnalysisError, match="run_output_signal_only"):
        run.put("model", {})
    with pytest.raises(sqlite3.OperationalError):
        reader.put("signal", {})
    with pytest.raises(AnalysisError, match="missing_model"):
        run.get("model", "0" * 64)
    # Corruption is checked at the original source; the output cannot repair it.
    reader.close()
    assert path.read_bytes() == before
    output.close()


def test_source_output_aliases_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source.db"
    source = AnalysisStore(path)
    reader = ReadOnlyAnalysisStore(path)
    with pytest.raises(AnalysisError, match="run_output_must_be_separate"):
        ResearchRunStore(source, source)
    with pytest.raises(AnalysisError, match="run_output_must_be_separate"):
        ResearchRunStore(reader, source)
    alias = tmp_path / "alias.db"
    alias.hardlink_to(path)
    other = ReadOnlyAnalysisStore(alias)
    with pytest.raises(AnalysisError, match="run_output_must_be_separate"):
        ResearchRunStore(reader, other)
    source.close()
    reader.close()
    other.close()
