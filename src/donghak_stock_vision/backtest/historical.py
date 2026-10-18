"""Latest-only daily data freeze; no inference shortcuts or execution approvals."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cached_property
from typing import Any, cast

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import PROVENANCE, FrozenJSON, FrozenTape, capture_market
from donghak_stock_vision.backtest.hgb_provider import HGBHistoricalProvider
from donghak_stock_vision.backtest.shared_evidence import EvidenceBuilder, is_shared
from donghak_stock_vision.backtest.validation import instant, require, utc
from donghak_stock_vision.data.learning import AnalysisError
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy, calendar_policy
from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_range, validate_ticker
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore, ResearchRunStore
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


def decision_schedule(
    value: Any, start: date, end: date, calendar: ResearchCalendarPolicy | None = None
) -> list[str] | None:
    """None means all available. Explicit dates are sorted, unique ISO Seoul dates.

    Empty schedules observe only. Missing market dates are recorded unavailable,
    never inferred to be holidays or silently replaced by a neighboring date.
    """
    if value == "all_available":
        return None
    require(type(value) is list, "decision_schedule_required")
    require(all(type(day) is str for day in value), "invalid_decision_date")
    require(value == sorted(set(value)), "decision_dates_not_canonical")
    for day in value:
        parsed = date.fromisoformat(day)
        require(parsed.isoformat() == day, "invalid_decision_date")
        require(start <= parsed <= end, "decision_outside_period")
        require(calendar is None or not calendar.excludes(parsed), "known_incomplete_trading_date")
    return list(value)


@dataclass(frozen=True)
class HistoricalBacktestInput:
    document: FrozenJSON

    @property
    def identifier(self) -> str:
        return self.document.historical_identifier

    @cached_property
    def tape(self) -> FrozenTape:
        return FrozenTape.from_json(FrozenJSON.freeze(self.document.to_dict()["tape"]).payload_json)

    @cached_property
    def analyses(self) -> tuple[FrozenAnalysis, ...]:
        tape = self.tape
        if is_shared(self.document.to_dict()):
            return self.document.shared_evidence.analyses(tape.manifest, tape.release_plan)
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
    decision_dates: str | list[str] = "all_available",
    run_output: AnalysisStore | None = None,
    hgb_provider: HGBHistoricalProvider | None = None,
    shared_evidence: bool = False,
    five_ticker: bool = False,
) -> HistoricalBacktestInput:
    """Dates are inclusive Seoul trading dates, not UTC publication timestamps.

    Caller provides quality and all existing manifest/policy inputs. Only read() is
    used on Phase 1; a caller needing cross-ticker atomicity must freeze its source.
    """
    validate_range(start, end)
    schedule = decision_schedule(decision_dates, start, end, research_calendar)
    require(analysis_store is None or run_output is not None, "run_output_required")
    if hgb_provider is not None:
        require(run_output is not None, "run_output_required")
        require(model_id == hgb_provider.model_id, "hgb_model_policy_mismatch")
        require(type(decision_dates) is list, "bounded_hgb_decision_dates_required")
        require(
            len(tickers) == (5 if five_ticker else 1),
            "concurrent_multi_ticker_execution_unsupported",
        )
        if hasattr(market, "connection"):
            # Borrow the existing same-file guard; never write through this wrapper.
            ResearchRunStore(market, run_output)  # type: ignore[arg-type]

    require(
        analysis_store is not None or run_output is None or hgb_provider is not None,
        "run_output_without_source",
    )
    if analysis_store is not None and run_output is not None:
        analysis_store = ResearchRunStore(analysis_store, run_output)
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
    if five_ticker:
        require(len(tickers) == len(set(tickers)) == 5, "exactly_five_unique_tickers_required")
        require(shared_evidence, "shared_evidence_required")
    universe = sorted(set(tickers))
    require(bool(universe), "empty_universe")
    for ticker in universe:
        validate_ticker(ticker)
    require(availability_assumption == ASSUMPTION, "explicit_daily_availability_required")
    m = manifest_template.to_dict()
    require(m["information_mode"] == "historical_research", "pit_history_unavailable")
    require(
        m["data_origin"] == "real"
        or (
            five_ticker
            and m["data_origin"] == "synthetic"
            and m["usage_restriction"] == "synthetic_test_only"
        ),
        "real_historical_input_required",
    )
    require(m["execution_mode"] == "backtest", "backtest_required")
    require(
        hgb_provider is not None or (analysis_store is None) == (model_id is None),
        "analysis_store_and_model_required",
    )
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
            data_origin=m["data_origin"],
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
    invalid_tickers: dict[str, str] = {}
    hgb_bundles: list[dict[str, Any]] = []
    builder = EvidenceBuilder()
    if hgb_provider is None and analysis_store is not None and model_id is not None:
        try:
            model = load_model(analysis_store, model_id, "historical_research")
            snapshot = analysis_store.get("snapshot", model["snapshot_id"])
            require(model["data_origin"] == m["data_origin"], "analysis_origin_mismatch")
            require(calendar_policy(snapshot) == research_calendar, "research_calendar_mismatch")
            # research() must use the training snapshot. Never silently substitute DB revisions.
            for t in universe:
                expected = [b.to_dict() for b in rows if b.ticker == t]
                selected = [
                    b
                    for b in snapshot["rows"].get(t, [])
                    if start.isoformat() <= b["trading_date"] <= end.isoformat()
                ]
                if five_ticker and expected != selected:
                    invalid_tickers[t] = "model_snapshot_market_mismatch"
                else:
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
        if schedule is not None and s["trading_date"] not in schedule:
            continue
        point: dict[str, Any] = {
            "ticker": s["ticker"],
            "trading_date": s["trading_date"],
            "available_at": stamp,
            "sequence": seq,
            "status": "blocked",
            "reason": error,
            "analysis_id": None,
        }
        if s["ticker"] in invalid_tickers:
            point["reason"] = invalid_tickers[s["ticker"]]
        if (
            model is not None
            and snapshot is not None
            and analysis_store is not None
            and model_id
            and s["ticker"] not in invalid_tickers
        ):
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
        if hgb_provider is not None:
            try:
                bundle = hgb_provider.capture(
                    copy,
                    ticker=s["ticker"],
                    as_of=utc(stamp),
                    history_start=start,
                    source_cutoff=utc(cutoff),
                    quality=quality.to_dict(),
                    calendar=research_calendar,
                    synthetic=m["data_origin"] == "synthetic",
                )
                aid, sid = bundle["analysis_id"], bundle["snapshot_id"]
                if shared_evidence:
                    builder.add(bundle)
                else:
                    hgb_bundles.append(bundle)
                ids.append((aid, sid))
                analysis_releases.append(
                    {
                        "model_id": model_id,
                        "snapshot_id": sid,
                        "analysis_id": aid,
                        "available_at": stamp,
                        "sequence": seq,
                    }
                )
                point.update(
                    analysis_id=aid, status="available", reason=bundle["signal"]["input_status"]
                )
                assert run_output is not None
                run_output.put("hgb_analysis", bundle)
            except (AnalysisError, ValueError) as exc:
                point["reason"] = str(exc)
        points.append(point)
    if schedule is not None:
        for day in schedule:
            for ticker in present:
                if not any(p["ticker"] == ticker and p["trading_date"] == day for p in points):
                    points.append(
                        {
                            "ticker": ticker,
                            "trading_date": day,
                            "available_at": release(day),
                            "sequence": None,
                            "status": "blocked",
                            "reason": "missing_decision_bar",
                            "analysis_id": None,
                        }
                    )
        points.sort(key=lambda p: (p["trading_date"], p["ticker"]))
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
    analyses = hgb_bundles
    if hgb_provider is None and analysis_store is not None and model_id:
        analyses = []
        for aid, sid in ids:
            frozen = FrozenAnalysis.capture(
                analysis_store, manifest, plan, model_id=model_id, snapshot_id=sid, analysis_id=aid
            )
            if shared_evidence:
                builder.add(frozen.bundle.to_dict())
            else:
                analyses.append(frozen.bundle.to_dict())
    if shared_evidence:
        analyses = builder.records
    return HistoricalBacktestInput(
        FrozenJSON.freeze(
            {
                "version": 2 if shared_evidence else 1,
                "kind": "historical_input_v2" if shared_evidence else "historical_input_v1",
                **(
                    {"evidence_table": builder.table(manifest.identifier)}
                    if shared_evidence
                    else {}
                ),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "tickers": universe,
                "requested_model_id": model_id,
                **(
                    {"portfolio_contract": "five_ticker_historical_input_v1"} if five_ticker else {}
                ),
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
