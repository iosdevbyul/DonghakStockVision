"""Read-only performance review of explicit historical skill sessions."""

import argparse
import json
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.experimental_research_performance import performance
from donghak_stock_vision.models.experimental_research_planner import research_plan
from donghak_stock_vision.storage.autonomous_research import private_root
from donghak_stock_vision.storage.experimental_experience_memory import restore, scope
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.memory_research_evidence import evidence


def review(sources: list[tuple[Path, Path]]) -> dict[str, Any]:
    if not sources:
        raise ValueError("performance_requires_sessions")
    root, pin_file = sources[0]
    private_root(root)
    session = root / "session.json"
    if (
        session.resolve() != session
        or session.stat().st_mode & 0o077
        or session.stat().st_size > 8 * 1024**2
    ):
        raise ValueError("performance_private_session")
    frozen = json.loads(session.read_text())
    if digest(frozen) != read_pin(pin_file):
        raise ValueError("performance_session_pin")
    target = root.parent / (root.name + "-gemma_blind")
    _, data, _ = evidence(target, frozen["plan_id"])
    details: list[dict[str, Any]] = []
    _, _, lineage = restore(sources, frozen, data, session_details=details)
    historical = [row for row in frozen["memory"] if scope(row, frozen["request"])]
    summary = performance(historical, details, frozen["candidates"], frozen["request"]["budget"])
    return summary | {
        "research_planner": research_plan(summary, frozen["request"]["budget"]),
        "dataset_id": frozen["dataset_id"],
        "sources": lineage,
        "new_python_trainings": 0,
        "new_gemma_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", action="append", nargs=2, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(review([(root, pin) for root, pin in args.previous]), indent=2))


if __name__ == "__main__":
    main()
