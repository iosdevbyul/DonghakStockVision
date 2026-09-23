"""End-to-end installed-wheel check; fixtures are synthetic, never performance evidence."""

import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import donghak_stock_vision
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, seed


def run(*args: str) -> Any:
    result = subprocess.run(
        [sys.executable, "-m", "donghak_stock_vision", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    assert "site-packages" in str(donghak_stock_vision.__file__), "must test installed wheel"
    with tempfile.TemporaryDirectory(prefix="dsv-learning-wheel-") as directory:
        market_path = Path(directory) / "market.sqlite3"
        analysis_path = str(Path(directory) / "analysis.sqlite3")
        market = SQLiteStore(market_path)
        source = bars()
        seed(market, source)
        market.close()
        result = run(
            "--db",
            str(market_path),
            "train",
            "--mode",
            "historical-research",
            "--tickers",
            "000001",
            "000002",
            "--start",
            source[0].trading_date.isoformat(),
            "--end",
            max(b.trading_date for b in source).isoformat(),
            "--snapshot-as-of",
            CUTOFF.isoformat(),
            "--analysis-db",
            analysis_path,
            "--acknowledge-research-limitations",
            "--synthetic-test",
        )
        assert result["usage_restriction"] == "synthetic_test_only"
        version, snapshot_id = result["model_version"], result["snapshot_id"]
        assert version
        market_path.unlink()  # Research replay must not depend on the mutable market DB.
        evaluation = run(
            "evaluate",
            "--mode",
            "historical-research",
            "--model-version",
            version,
            "--analysis-db",
            analysis_path,
        )
        assert evaluation["operational_status"] == "unregistered"
        for direction in ("up", "down"):
            assert evaluation["directions"][direction]["test_status"] == "evaluated"
        outputs = run(
            "infer",
            "--mode",
            "historical-research",
            "--model-version",
            version,
            "--snapshot-id",
            snapshot_id,
            "--anchor-date",
            date(2020, 6, 1).isoformat(),
            "--tickers",
            "000001",
            "000002",
            "--analysis-db",
            analysis_path,
        )
        assert all(r["input_status"] == "ok" for r in outputs)
        assert all((r["up_score"] is None) != (r["down_score"] is None) for r in outputs)
        queried = run(
            "signals",
            "--scope",
            "research",
            "--model-version",
            version,
            "--snapshot-id",
            snapshot_id,
            "--analysis-db",
            analysis_path,
        )
        assert len(queried) == 2
        operational = run("signals", "--as-of", CUTOFF.isoformat(), "--analysis-db", analysis_path)
        assert all(not row["items"] for row in operational.values())
    print("Installed wheel: synthetic train/evaluate/infer/query passed; no operational signals.")


if __name__ == "__main__":
    main()
