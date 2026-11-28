"""Explicit finite multi-session research, with prior PIN references and no activation."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.experimental_skill_study_cli import compare
from donghak_stock_vision.storage.experimental_experience_memory import MAX_SESSIONS
from donghak_stock_vision.storage.memory_guided_research import VERSION, prepare


def campaign(
    request: dict[str, Any],
    research: Path,
    pins: Path,
    previous: list[tuple[Path, Path]],
    sessions: int,
) -> list[dict[str, Any]]:
    if not 1 <= sessions <= 4 or len(previous) + sessions > MAX_SESSIONS:
        raise ValueError("skill_campaign_bound")
    if request["version"] != VERSION:
        raise ValueError("skill_campaign_version")
    sources = list(previous)
    results = []
    for _ in range(sessions):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        root = research / ("skill-experiment-campaign-" + stamp)
        pin_root = pins / root.name
        pin = prepare(request, root, pin_root)
        result = compare(root, pin, skip_legacy_baseline=True, previous=sources)
        skills = result["experimental_skills"]
        item = dict(root=str(root), session_pin_file=str(pin_root / "session.pin"), result=result)
        results.append(item)
        print(json.dumps(item), flush=True)
        sources.append((root, pin_root / "session.pin"))
        if skills["pause_reason"] or skills["completed"] < skills["effective_training_budget"]:
            break
        if skills["effective_training_budget"] < request["budget"]:
            break
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--previous", action="append", nargs=2, type=Path, default=[])
    args = parser.parse_args()
    campaign(
        json.loads(args.request.read_text()),
        args.research,
        args.pins,
        [(root, pin) for root, pin in args.previous],
        args.sessions,
    )


if __name__ == "__main__":
    main()
