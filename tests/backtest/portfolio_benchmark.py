"""Bounded offline measurement of 1–3 saved five-ticker result artifacts.

Run: python -m tests.backtest.portfolio_benchmark result.json --output metrics.json
No acquisition, training or network. Timings are deliberately outside report hashes.
"""

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.portfolio_report import replay_five_ticker


def measure(path: Path) -> dict[str, Any]:
    original = FrozenJSON(path.read_text())
    d = original.to_dict()
    frozen = FrozenJSON.freeze(d["frozen_input"])
    report = FrozenJSON.freeze(d["detailed_report"])
    start = time.perf_counter()
    result = replay_five_ticker(frozen)
    elapsed = time.perf_counter() - start
    if result != original:
        raise ValueError("benchmark_result_replay_conflict")
    return {
        "family": d["frozen_input"]["request"]["model_family"],
        "scope": report.to_dict()["scope"],
        "signal_scopes": sorted(
            {
                r["signal_evidence_scope"]
                for r in report.to_dict()["timeline"]
                if r["signal_evidence_scope"] is not None
            }
        ),
        "securities": len(d["frozen_input"]["request"]["tickers"]),
        "decision_dates": len(d["frozen_input"]["request"]["config"]["decision_dates"]),
        "analysis_records": len(d["frozen_input"]["historical_input"]["analyses"]),
        "virtual_orders": len(d["run"]["final_account"]["orders"]),
        "fills": d["performance"]["fill_count"],
        "frozen_bytes": len(frozen.payload_json.encode()),
        "report_bytes": len(report.payload_json.encode()),
        "result_bytes": len(original.payload_json.encode()),
        "replay_seconds": elapsed,
        "replay_exact": True,
        "run_hash": d["run_hash"],
        "report_hash": report.identifier,
        "python_peak_bytes": None,
        "memory_note": "unmeasured; no Python/native peak or RSS guarantee",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= len(args.results) <= 3:
        parser.error("provide 1–3 bounded result artifacts")
    if args.output.resolve() in {p.resolve() for p in args.results}:
        parser.error("measurement output must not overwrite source evidence")
    output = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "runs": [measure(p) for p in args.results],
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
