"""Explicit synthetic observation schedules, not approved execution policies."""

from typing import Any

from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from tests.backtest.helpers import T, manifest, payload, policy

LATER = "2026-01-05T01:00:00Z"
CORRECTION = "2026-01-05T02:00:00Z"


def source(revision: str = "r1", received: str = T) -> dict[str, Any]:
    return {
        "kind": "synthetic_event",
        "ticker": "005930",
        "trading_date": "2026-01-05",
        "revision": revision,
        "source_received_at": received,
        "data_origin": "synthetic",
        "adjustment": "unadjusted",
        "market": "KOSPI",
        "fields": {
            "open": "100",
            "high": "110",
            "low": "90",
            "close": "105",
            "volume": 100,
            "trading_value": "10000",
        },
        "original": {"fixture": True, "future_labels": [1, 0]},
    }


def quality() -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "schema_version": 1,
            "source": "synthetic-fixture",
            "verified_at": T,
            "sessions": ["2026-01-05"],
            "coverage": {
                "005930": {
                    "start": "2026-01-05",
                    "end": "2026-01-05",
                    "adjustment": "unadjusted",
                    "excluded_dates": [],
                }
            },
        }
    )


def tape_parts(
    sources: list[dict[str, Any]] | None = None, mode: str = "historical_research"
) -> dict[str, Any]:
    values = sources if sources is not None else [source()]
    frozen = tuple(FrozenJSON.freeze(s) for s in values)
    q = quality()
    provenance = {
        "information_mode": mode,
        "data_origin": "synthetic",
        "usage_restriction": "synthetic_test_only",
    }
    releases = []
    for i, s in enumerate(values):
        releases.append(
            {
                "sequence": i + 1,
                "previous_revision": None if i == 0 else values[i - 1]["revision"],
                "field_times": {
                    key: (T if key == "open" else LATER) if i == 0 else CORRECTION
                    for key in s["fields"]
                },
            }
        )
    plan = FrozenJSON.freeze({**provenance, "releases": releases, "analysis_releases": []})
    p = policy().to_dict()
    p["availability_policy"]["content_hash"] = plan.identifier
    selected = ExecutionPolicy.from_dict(p)
    m = manifest(selected).to_dict()
    m.update(provenance)
    m["availability_assumption_id"] = plan.identifier if mode == "historical_research" else None
    m["quality_manifest"] = {**m["quality_manifest"], **provenance, "content_hash": q.identifier}
    for key in ("models", "analyses"):
        m[key] = [{**r, **provenance} for r in m[key]]
    for key in ("calendar", "universe"):
        m[key].update(provenance)
    m["market_snapshots"] = [
        {
            "artifact_id": s.identifier,
            "version": "fixture-v1",
            "content_hash": s.identifier,
            **provenance,
        }
        for s in frozen
    ]
    run = RunManifest.from_dict(m)
    events = []
    for i, (s, f) in enumerate(zip(values, frozen, strict=True)):
        e = payload(MarketEvent)
        e.update(provenance)
        e.update(
            run_id=run.identifier,
            sequence=i + 1,
            revision=s["revision"],
            source_received_at=s["source_received_at"],
            public_fields=s["fields"],
            source={
                "artifact_id": f.identifier,
                "content_hash": f.identifier,
                "version": "fixture-v1",
                **provenance,
            },
            quality_evidence_ids=[q.identifier],
            availability_assumption_id=m["availability_assumption_id"],
            available_at=T if i == 0 else CORRECTION,
        )
        events.append(MarketEvent.from_dict(e))
    return {
        "manifest": run,
        "policy": selected,
        "events": tuple(events),
        "sources": frozen,
        "release_plan": plan,
        "quality": q,
    }


def tape(
    sources: list[dict[str, Any]] | None = None, mode: str = "historical_research"
) -> FrozenTape:
    return FrozenTape(**tape_parts(sources, mode))


def daily_tape(captured: tuple[FrozenJSON, ...], q: FrozenJSON) -> FrozenTape:
    """Explicit retrospective release of a complete day, no inferred session/tradability."""
    selected_source = captured[0]
    s = selected_source.to_dict()
    stamp = s["trading_date"] + "T16:00:00+09:00"
    provenance = {
        "information_mode": "historical_research",
        "data_origin": "synthetic",
        "usage_restriction": "synthetic_test_only",
    }
    plan = FrozenJSON.freeze(
        {
            **provenance,
            "releases": [
                {
                    "sequence": 1,
                    "previous_revision": None,
                    "field_times": {k: stamp for k in s["fields"]},
                }
            ],
            "analysis_releases": [],
        }
    )
    p = policy().to_dict()
    p["availability_policy"]["content_hash"] = plan.identifier
    selected = ExecutionPolicy.from_dict(p)
    m = manifest(selected).to_dict()
    m.update(start_at=stamp, end_at=stamp, availability_assumption_id=plan.identifier)
    m["initial_account"].update(observed_at=stamp, received_at=stamp)
    m["market_snapshots"] = [
        {
            **provenance,
            "artifact_id": selected_source.identifier,
            "version": "captured-clean-bar",
            "content_hash": selected_source.identifier,
        }
    ]
    m["quality_manifest"]["content_hash"] = q.identifier
    run = RunManifest.from_dict(m)
    e = payload(MarketEvent)
    for key in ("ticker", "trading_date", "revision", "source_received_at", "adjustment", "market"):
        e[key] = s[key]
    e.update(
        run_id=run.identifier,
        event_at=stamp,
        available_at=stamp,
        quality_available_at=stamp,
        source=m["market_snapshots"][0],
        public_fields=s["fields"],
        quality_evidence_ids=[q.identifier],
        availability_assumption_id=plan.identifier,
        session="unknown",
        halt_status="unknown",
        listing_status="unknown",
    )
    return FrozenTape(run, selected, (MarketEvent.from_dict(e),), (selected_source,), plan, q)
