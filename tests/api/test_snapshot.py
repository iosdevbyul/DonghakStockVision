from pathlib import Path

import pytest

from donghak_stock_vision.api.snapshot import SnapshotReader, SnapshotValidationError
from tests.api.helpers import snapshot_payload, write_snapshot


def test_snapshot_reader_loads_validated_read_only_payload(tmp_path: Path) -> None:
    reader = SnapshotReader.from_path(write_snapshot(tmp_path / "snapshot.json"))

    assert reader.project_status().productName == "Tris Market Lens"
    assert reader.coverage_summary().totalBars == 1_099_255
    assert reader.model_summaries() == ()
    assert reader.evidence_layers() == ()
    assert reader.baseline_backtest().performanceAvailable is False
    assert reader.stocks() == ()


def test_snapshot_reader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SnapshotValidationError, match="does not exist"):
        SnapshotReader.from_path(tmp_path / "missing.json")


def test_snapshot_reader_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = snapshot_payload()
    payload["unexpected"] = True
    path = write_snapshot(tmp_path / "invalid.json", payload)

    with pytest.raises(SnapshotValidationError, match="invalid"):
        SnapshotReader.from_path(path)
