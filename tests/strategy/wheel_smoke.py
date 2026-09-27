"""Installed-wheel CLI/replay checks with explicit synthetic research fixtures only."""

import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import donghak_stock_vision
from donghak_stock_vision.storage.decision import DecisionStore
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy
from donghak_stock_vision.strategy.engine import decide
from tests.strategy.helpers import AT, inputs, policy


def run(*args: str, expected: int = 0) -> Any:
    completed = subprocess.run(
        [sys.executable, "-m", "donghak_stock_vision", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == expected, completed.stderr + completed.stdout
    return json.loads(completed.stdout)


def main() -> None:
    assert "site-packages" in str(donghak_stock_vision.__file__)
    with tempfile.TemporaryDirectory(prefix="dsv-decision-wheel-") as directory:
        root = Path(directory)
        db = root / "decision.db"
        b, p = inputs(held=5), policy()
        immutable, settings = DecisionInput.from_dict(b), DecisionPolicy.from_dict(p)
        with_result = decide(immutable, settings)
        store = DecisionStore(db)
        saved = store.save(immutable, settings, with_result)
        store.close()
        query = ["decisions", "--decision-db", str(db), "--scope", "research"]
        replayed = run(*query, "--decision-id", saved["decision_id"], "--replay")
        assert replayed == saved and replayed["action"] == "HOLD"
        assert replayed["diagnostics"]["safety_assurance"] is False
        blocked = run(
            "decide",
            "--decision-db",
            str(db),
            "--analysis-db",
            str(root / "absent.db"),
            "--scope",
            "operational",
            "--ticker",
            "000001",
            "--as-of",
            AT,
            "--analysis-id",
            "approval_cannot_enable",
            expected=1,
        )
        assert blocked["action"] == "WAIT" and not blocked["executable"]
        if "--learning" in sys.argv:
            from donghak_stock_vision.data.snapshot import capture
            from donghak_stock_vision.models.service import TrainingService
            from donghak_stock_vision.signals.service import SignalService
            from donghak_stock_vision.storage.analysis import AnalysisStore
            from donghak_stock_vision.storage.sqlite import SQLiteStore
            from tests.learning.helpers import CUTOFF, bars, seed

            source = bars()
            market = SQLiteStore(root / "market.db")
            seed(market, source)
            analysis_path = root / "analysis.db"
            analysis = AnalysisStore(analysis_path)
            snap = capture(
                market,
                ["000001", "000002"],
                source[0].trading_date,
                max(bar.trading_date for bar in source),
                CUTOFF,
                "historical_research",
                acknowledge=True,
                synthetic=True,
            )
            sid = analysis.put("snapshot", snap)
            trained = TrainingService(analysis).train(sid)
            row = SignalService(analysis).research(
                trained["model_version"], sid, ["000001"], date(2020, 6, 1)
            )[0]
            analysis.close()
            market.close()
            p["allowed_models"] = [row["model_version"]]
            command = [
                "decide",
                "--decision-db",
                str(db),
                "--analysis-db",
                str(analysis_path),
                "--scope",
                "research",
                "--ticker",
                "000001",
                "--as-of",
                AT,
                "--analysis-id",
                row["analysis_id"],
            ]
            paths = []
            for name, value in [
                ("policy", p),
                ("account", b["account"]),
                ("orders", b["orders"]),
                ("market-context", b["market"]),
            ]:
                path = root / (name + ".json")
                path.write_text(json.dumps(value))
                paths.append(path)
                command += ["--" + name, str(path)]
            saved = run(*command)
            assert (
                saved["status"] == "virtual" and saved["usage_restriction"] == "synthetic_test_only"
            )
            analysis_path.unlink()
            (root / "market.db").unlink()
            for path in paths:
                path.write_text("{}")
            assert run(*query, "--decision-id", saved["decision_id"], "--replay") == saved
            assert any(r["decision_id"] == saved["decision_id"] for r in run(*query))
    print("Installed wheel: virtual decisions, isolation, immutable replay passed.")


if __name__ == "__main__":
    main()
