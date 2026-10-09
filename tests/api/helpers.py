"""Small validated research snapshot fixture for API transport tests."""

import json
from pathlib import Path
from typing import Any


def snapshot_payload() -> dict[str, Any]:
    return {
        "projectStatus": {
            "productName": "Tris Market Lens",
            "sourceLabel": "test snapshot",
            "metrics": [],
            "evidence": [],
            "baseline": [],
        },
        "coverage": {
            "universe": 993,
            "totalBars": 1_099_255,
            "startDate": "2022-01-03",
            "endDate": "2026-10-01",
            "currentIdentityVerified": 871,
            "historicalIdentityVerified": 0,
            "unresolvedSecurities": 43,
            "unexplainedTickerSessions": 8_118,
        },
        "models": [],
        "evidence": [],
        "baselineBacktest": {
            "id": "baseline",
            "title": "Locked historical baseline",
            "state": "blocked",
            "summary": "Blocked before execution.",
            "readiness": [],
            "timeline": [],
            "policyGroups": [],
            "fingerprints": [],
            "performanceAvailable": False,
        },
        "stocks": [],
    }


def write_snapshot(path: Path, payload: dict[str, Any] | None = None) -> Path:
    path.write_text(
        json.dumps(snapshot_payload() if payload is None else payload),
        encoding="utf-8",
    )
    return path
