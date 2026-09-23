import json
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.cli import main
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, seed


def test_learning_cli_train_evaluate_infer_without_latest_db(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    market_path, analysis_path = tmp_path / "market.db", tmp_path / "analysis.db"
    market = SQLiteStore(market_path)
    source = bars()
    seed(market, source)
    market.close()
    assert (
        main(
            [
                "--db",
                str(market_path),
                "train",
                "--mode",
                "historical-research",
                "--analysis-db",
                str(analysis_path),
                "--tickers",
                "000001",
                "000002",
                "--start",
                source[0].trading_date.isoformat(),
                "--end",
                max(b.trading_date for b in source).isoformat(),
                "--snapshot-as-of",
                CUTOFF.isoformat(),
                "--acknowledge-research-limitations",
                "--synthetic-test",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    version, sid = result["model_version"], result["snapshot_id"]
    assert result["usage_restriction"] == "synthetic_test_only"
    # The supplied market DB no longer even exists. Research replay still works.
    market_path.unlink()
    shared = ["--analysis-db", str(analysis_path), "--model-version", version]
    assert main(["evaluate", "--mode", "historical-research", *shared]) == 0
    assert json.loads(capsys.readouterr().out)["snapshot_id"] == sid
    assert (
        main(
            [
                "--db",
                str(market_path),
                "infer",
                "--mode",
                "historical-research",
                *shared,
                "--snapshot-id",
                sid,
                "--anchor-date",
                "2020-06-01",
                "--tickers",
                "000001",
            ]
        )
        == 0
    )
    inferred = json.loads(capsys.readouterr().out)[0]
    assert inferred["up_score"] is None or inferred["down_score"] is None
    assert main(["signals", "--scope", "research", *shared, "--snapshot-id", sid]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert (
        main(["signals", "--analysis-db", str(analysis_path), "--as-of", CUTOFF.isoformat()]) == 0
    )
    assert json.loads(capsys.readouterr().out)["up"]["reason"] == "no_registered_model"
    assert main(["signals", *shared, "--as-of", CUTOFF.isoformat()]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "registration_forbidden"


def test_cli_missing_model_invalid_mode_and_insufficient_history(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ap = tmp_path / "analysis.db"
    analysis = AnalysisStore(ap)
    analysis.close()
    assert (
        main(
            [
                "evaluate",
                "--analysis-db",
                str(ap),
                "--mode",
                "historical-research",
                "--model-version",
                "missing",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "missing_model"
    with pytest.raises(SystemExit):
        main(["evaluate", "--analysis-db", str(ap), "--model-version", "missing"])
    capsys.readouterr()
    mp = tmp_path / "small.db"
    market = SQLiteStore(mp)
    source = bars(8, 1)
    seed(market, source)
    market.close()
    assert (
        main(
            [
                "--db",
                str(mp),
                "train",
                "--mode",
                "historical-research",
                "--analysis-db",
                str(ap),
                "--tickers",
                "000001",
                "--start",
                "2020-01-02",
                "--end",
                source[-1].trading_date.isoformat(),
                "--snapshot-as-of",
                CUTOFF.isoformat(),
                "--acknowledge-research-limitations",
                "--synthetic-test",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["model_version"] is None


def test_cli_mode_gate(
    trained: dict[str, Any], analysis: AnalysisStore, capsys: pytest.CaptureFixture[str]
) -> None:
    db = analysis.connection.execute("PRAGMA database_list").fetchone()[2]
    assert (
        main(
            [
                "evaluate",
                "--mode",
                "point-in-time",
                "--analysis-db",
                db,
                "--model-version",
                trained["model_version"],
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "mode_mismatch"
