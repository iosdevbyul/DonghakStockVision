"""Latest-only daily data freeze; no inference shortcuts or execution approvals."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, cast

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import PROVENANCE, FrozenJSON, FrozenTape, capture_market
from donghak_stock_vision.backtest.validation import instant, require, utc
from donghak_stock_vision.data.learning import AnalysisError
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy, calendar_policy
from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_range, validate_ticker
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.base import MarketDataStore

ASSUMPTION = "after_trading_date_seoul_midnight"
LIMITATIONS = [
    "latest_known_historical_snapshot",
    "source_revision_history_unavailable",
    "cross_ticker_capture_not_transactional",
    "not_pit_verified",
    "not_oos_evidence",
    "model_training_cutoff_unverified",
    "market_state_and_liquidity_unverified",
    "real_runner_unsupported",
    "concurrent_reservation_unsupported",
    "missing_sessions_not_calendar_verified",
]


@dataclass(frozen=True)
class HistoricalBacktestInput:
    document: FrozenJSON

    @property
    def identifier(self) -> str:
        value = self.document.to_dict()
        for artifact in value["analyses"]:
            artifact["created_at"].pop("signal", None)
        return FrozenJSON.freeze(value).identifier

    @property
    def tape(self) -> FrozenTape:
        return FrozenTape.from_json(FrozenJSON.freeze(self.document.to_dict()["tape"]).payload_json)

    @property
    def analyses(self) -> tuple[FrozenAnalysis, ...]:
        tape = self.tape
        return tuple(
            FrozenAnalysis(tape.manifest, tape.release_plan, FrozenJSON.freeze(b))
            for b in self.document.to_dict()["analyses"]
        )


@dataclass(frozen=True)
class _ReadCopy:
    rows: tuple[DailyBar, ...]

    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        return [b for b in self.rows if b.ticker == ticker and start <= b.trading_date <= end]


def build_historical_input(
    market: MarketDataStore,
    *,
    start: date,
    end: date,
    tickers: list[str],
    captured_at: str,
    quality: FrozenJSON,
    manifest_template: RunManifest,
    policy: ExecutionPolicy,
    availability_assumption: str,
    analysis_store: AnalysisStore | None = None,
    model_id: str | None = None,
    research_calendar: ResearchCalendarPolicy | None = None,
) -> HistoricalBacktestInput:
    """Dates are inclusive Seoul trading dates, not UTC publication timestamps.

    Caller provides quality and all existing manifest/policy inputs. Only read() is
    used on Phase 1; a caller needing cross-ticker atomicity must freeze its source.
    """
    validate_range(start, end)
    if research_calendar is not None:
        require(
            not research_calendar.excludes(start) and not research_calendar.excludes(end),
            "research_boundary_on_incomplete_date",
        )
        q = quality.to_dict()
        require(calendar_policy(q) in (None, research_calendar), "research_calendar_mismatch")
        q["research_calendar"] = research_calendar.to_dict()
        quality = FrozenJSON.freeze(q)
    else:
        require(calendar_policy(quality.to_dict()) is None, "explicit_research_calendar_required")
    universe = sorted(set(tickers))
    require(bool(universe), "empty_universe")
    for ticker in universe:
        validate_ticker(ticker)
    require(availability_assumption == ASSUMPTION, "explicit_daily_availability_required")
    m = manifest_template.to_dict()
    require(m["information_mode"] == "historical_research", "pit_history_unavailable")
    require(m["data_origin"] == "real", "real_historical_input_required")
    require(m["execution_mode"] == "backtest", "backtest_required")
    require((analysis_store is None) == (model_id is None), "analysis_store_and_model_required")
    cutoff = instant(captured_at)
    require(utc(cutoff).astimezone(SEOUL).date() > end, "capture_before_period_end")
    # Materialize each requested series once, then use the existing public adapter.
    rows = tuple(b for t in universe for b in market.read(t, start, end))
    for bar in rows:
        require(
            bar.ticker in universe and start <= bar.trading_date <= end, "source_range_mismatch"
        )
    if research_calendar is not None:
        rows = tuple(b for b in rows if not research_calendar.excludes(b.trading_date))
    copy = cast(MarketDataStore, _ReadCopy(rows))  # capture_market consumes only read().
    present = [t for t in universe if any(b.ticker == t for b in rows)]
    sources = (
        capture_market(
            copy,
            present,
            start,
            end,
            information_mode="historical_research",
            data_origin="real",
            captured_at=cutoff,
            quality=quality,
        )
        if present
        else ()
    )
    sources = tuple(
        sorted(sources, key=lambda s: (s.to_dict()["trading_date"], s.to_dict()["ticker"]))
    )
    provenance = {k: m[k] for k in PROVENANCE}

    def ref(identifier: str) -> dict[str, Any]:
        return {
            **provenance,
            "artifact_id": identifier,
            "content_hash": identifier,
            "version": "historical-input-v1",
        }

    def release(day: str) -> str:
        return instant(datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(), SEOUL))

    releases, analysis_releases, points = [], [], []
    ids: list[tuple[str, str]] = []
    model = snapshot = None
    error = "analysis_artifact_unavailable"
    if analysis_store is not None and model_id is not None:
        try:
            model = load_model(analysis_store, model_id, "historical_research")
            snapshot = analysis_store.get("snapshot", model["snapshot_id"])
            require(model["data_origin"] == "real", "analysis_origin_mismatch")
            require(calendar_policy(snapshot) == research_calendar, "research_calendar_mismatch")
            # research() must use the training snapshot. Never silently substitute DB revisions.
            for t in universe:
                expected = [b.to_dict() for b in rows if b.ticker == t]
                selected = [
                    b
                    for b in snapshot["rows"].get(t, [])
                    if start.isoformat() <= b["trading_date"] <= end.isoformat()
                ]
                require(expected == selected, "model_snapshot_market_mismatch")
        except (AnalysisError, ValueError) as exc:
            model = snapshot = None
            error = str(exc)
    for seq, source in enumerate(sources, 1):
        s = source.to_dict()
        stamp = release(s["trading_date"])
        releases.append(
            {
                "sequence": seq,
                "previous_revision": None,
                "field_times": {k: stamp for k in s["fields"]},
            }
        )
        point: dict[str, Any] = {
            "ticker": s["ticker"],
            "trading_date": s["trading_date"],
            "available_at": stamp,
            "sequence": seq,
            "status": "blocked",
            "reason": error,
            "analysis_id": None,
        }
        if model is not None and snapshot is not None and analysis_store is not None and model_id:
            try:
                result = SignalService(analysis_store).research(
                    model_id,
                    model["snapshot_id"],
                    [s["ticker"]],
                    date.fromisoformat(s["trading_date"]),
                )[0]
                aid = result["analysis_id"]
                ids.append((aid, model["snapshot_id"]))
                analysis_releases.append(
                    {
                        "model_id": model_id,
                        "snapshot_id": model["snapshot_id"],
                        "analysis_id": aid,
                        "available_at": stamp,
                        "sequence": seq,
                    }
                )
                point.update(
                    analysis_id=aid,
                    status="available" if result["input_status"] == "ok" else "blocked",
                    reason=result["input_status"],
                )
            except (AnalysisError, ValueError) as exc:
                point["reason"] = str(exc)
        points.append(point)
    plan = FrozenJSON.freeze(
        {**provenance, "releases": releases, "analysis_releases": analysis_releases}
    )
    p = policy.to_dict()
    p["availability_policy"] = {
        k: v for k, v in ref(plan.identifier).items() if k not in PROVENANCE
    }
    selected_policy = ExecutionPolicy.from_dict(p)
    m.update(
        start_at=instant(datetime.combine(start, time(), SEOUL)),
        end_at=release(end.isoformat()),
        execution_policy_id=selected_policy.identifier,
        availability_assumption_id=plan.identifier,
        market_snapshots=[ref(s.identifier) for s in sources]
        + [ref(sid) for sid in sorted({sid for _, sid in ids})]
        or m["market_snapshots"],
        analyses=[ref(aid) for aid, _ in ids] or m["analyses"],
        models=[ref(model_id)] if ids and model_id else m["models"],
        quality_manifest=ref(quality.identifier),
        universe=ref(FrozenJSON.freeze({"tickers": universe}).identifier),
    )
    manifest = RunManifest.from_dict(m)
    events = []
    for seq, source in enumerate(sources, 1):
        s = source.to_dict()
        stamp = release(s["trading_date"])
        events.append(
            MarketEvent.from_dict(
                {
                    "schema_version": 1,
                    "scope": "research",
                    "executable": False,
                    "operational_eligible": False,
                    **provenance,
                    "run_id": manifest.identifier,
                    "sequence": seq,
                    **{
                        k: s[k]
                        for k in (
                            "ticker",
                            "trading_date",
                            "revision",
                            "source_received_at",
                            "adjustment",
                            "market",
                        )
                    },
                    "event_at": stamp,
                    "available_at": stamp,
                    "source": ref(source.identifier),
                    "public_fields": s["fields"],
                    "quality_status": "verified",
                    "quality_flags": [],
                    "quality_available_at": stamp,
                    "quality_evidence_ids": [quality.identifier],
                    "session": "closed",
                    "listing_status": "unknown",
                    "halt_status": "unknown",
                    "availability_assumption_id": plan.identifier,
                }
            )
        )
    tape = FrozenTape(manifest, selected_policy, tuple(events), sources, plan, quality)
    analyses = (
        [
            FrozenAnalysis.capture(
                analysis_store, manifest, plan, model_id=model_id, snapshot_id=sid, analysis_id=aid
            ).bundle.to_dict()
            for aid, sid in ids
        ]
        if analysis_store is not None and model_id
        else []
    )
    return HistoricalBacktestInput(
        FrozenJSON.freeze(
            {
                "version": 1,
                "kind": "historical_input_v1",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "tickers": universe,
                "requested_model_id": model_id,
                "mode": "historical_research",
                "tape": json.loads(tape.to_json()),
                "analyses": analyses,
                "decision_points": points,
                "missing_tickers": sorted(set(universe) - set(present)),
                "source_metadata": {
                    "captured_at": cutoff,
                    "source_hashes": [s.identifier for s in sources],
                    "availability_assumption": ASSUMPTION,
                    "quality": quality.to_dict(),
                },
                "limitations": LIMITATIONS
                + (["research_sessions_excluded_not_holidays"] if research_calendar else []),
                "execution_status": "blocked",
                "execution_reason": "real_runner_unsupported",
            }
        )
    )
