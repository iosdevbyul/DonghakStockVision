import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.cli import main
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import TrainingService
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from donghak_stock_vision.strategy.adapter import ReadOnlyAnalysisStore, assemble
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from donghak_stock_vision.strategy.engine import decide
from tests.learning.helpers import CUTOFF, bars, seed
from tests.strategy.helpers import AT, inputs, policy


def create_analysis(directory: Path) -> tuple[Path, dict[str, Any]]:
    market = SQLiteStore(directory / "market.db")
    analysis_path = directory / "analysis.db"
    analysis = AnalysisStore(analysis_path)
    try:
        source = bars()
        seed(market, source)
        snap = capture(
            market,
            ["000001", "000002"],
            source[0].trading_date,
            max(b.trading_date for b in source),
            CUTOFF,
            "historical_research",
            acknowledge=True,
            synthetic=True,
        )
        sid = analysis.put("snapshot", snap)
        model = TrainingService(analysis).train(sid)
        row = SignalService(analysis).research(
            model["model_version"], sid, ["000001"], date(2020, 6, 1)
        )[0]
        return analysis_path, row
    finally:
        analysis.close()
        market.close()


def test_adapter_cli_readonly_and_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, row = create_analysis(tmp_path)
    b, p = inputs(held=5), policy()
    b["request"]["analysis_id"] = row["analysis_id"]
    p["allowed_models"] = [row["model_version"]]
    before = path.read_bytes()
    readonly = ReadOnlyAnalysisStore(path)
    with pytest.raises(sqlite3.OperationalError):
        readonly.put("signal", {"illegal": True})
    readonly.close()
    original = assemble(b["request"], path, b["account"], b["orders"], b["market"])
    output = decide(original, DecisionPolicy.from_dict(p)).to_dict()
    assert output["status"] == "virtual", output
    assert output["usage_restriction"] == "synthetic_test_only"
    assert path.read_bytes() == before
    files = {}
    for name, value in [
        ("policy", p),
        ("account", b["account"]),
        ("orders", b["orders"]),
        ("market-context", b["market"]),
    ]:
        file = tmp_path / (name + ".json")
        file.write_text(json.dumps(value))
        files[name] = str(file)
    target = tmp_path / "decisions.db"
    args = [
        "decide",
        "--decision-db",
        str(target),
        "--analysis-db",
        str(path),
        "--scope",
        "research",
        "--ticker",
        "000001",
        "--as-of",
        AT,
        "--analysis-id",
        row["analysis_id"],
    ]
    for name, filename in files.items():
        args.extend(["--" + name, filename])
    assert main(args) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["diagnostics"] == output["diagnostics"]
    assert path.read_bytes() == before
    path.unlink()
    (tmp_path / "market.db").unlink()
    for filename in files.values():
        Path(filename).write_text("{}")
    query = ["decisions", "--decision-db", str(target)]
    assert main(query) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert (
        main([*query, "--scope", "research", "--decision-id", saved["decision_id"], "--replay"])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == saved


def test_operational_cli_and_missing_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "decision.db"
    args = [
        "decide",
        "--decision-db",
        str(db),
        "--analysis-db",
        str(tmp_path / "missing.db"),
        "--ticker",
        "000001",
        "--as-of",
        AT,
        "--analysis-id",
        "forged_registry_approval",
    ]
    assert main([*args, "--scope", "operational", "--policy", str(tmp_path / "absent.json")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["blocking_reasons"] == ["operational_unavailable"]
    assert not result["executable"] and not result["operational_eligible"]
    assert main([*args, "--scope", "research"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert not (tmp_path / "missing.db").exists()


def test_adapter_missing_artifact_is_blocked(tmp_path: Path) -> None:
    b = inputs()
    result = decide(
        assemble(b["request"], tmp_path / "absent.db", b["account"], b["orders"], b["market"]),
        DecisionPolicy.from_dict(policy()),
    ).to_dict()
    assert result["blocking_reasons"] == ["analysis_storage_unavailable"]
