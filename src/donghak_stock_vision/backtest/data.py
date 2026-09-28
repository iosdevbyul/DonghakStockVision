"""Small immutable input tapes and causal views, with no persistent storage."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.validation import (
    fields,
    instant,
    integer,
    money,
    require,
    sha,
    text,
    utc,
    validate_run,
)
from donghak_stock_vision.data.learning import canonical, digest
from donghak_stock_vision.data.schema import DailyBar, validate_range, validate_ticker
from donghak_stock_vision.data.snapshot import covered, validate_quality
from donghak_stock_vision.storage.base import MarketDataStore

PROVENANCE = ("information_mode", "data_origin", "usage_restriction")
MARKET_FIELDS = {"open", "high", "low", "close", "price", "volume", "trading_value"}


@dataclass(frozen=True)
class FrozenJSON:
    """Detached JSON evidence, not an approval or authenticity assertion."""

    payload_json: str

    def __post_init__(self) -> None:
        def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                require(key not in result, "duplicate_evidence_key")
                result[key] = item
            return result

        value = json.loads(self.payload_json, object_pairs_hook=unique_pairs)
        require(isinstance(value, dict), "evidence_object_required")
        object.__setattr__(self, "payload_json", canonical(value))

    @classmethod
    def freeze(cls, value: dict[str, Any]) -> "FrozenJSON":
        return cls(canonical(value))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.payload_json)
        return result

    @property
    def identifier(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class PublicView:
    """Only payload goes to strategy; full-run lineage is a separate audit envelope."""

    payload: FrozenJSON
    lineage: FrozenJSON

    @property
    def identifier(self) -> str:
        return self.payload.identifier

    def to_dict(self) -> dict[str, Any]:
        return self.payload.to_dict()


def quality_check(quality: dict[str, Any], ticker: str, day: str, adjustment: str) -> None:
    validate_quality(quality)
    d = date.fromisoformat(day)
    require(covered(quality, ticker, d, d), "quality_coverage_missing")
    entry = quality["coverage"][ticker]
    require(day in quality["sessions"] and day not in entry["excluded_dates"], "quality_excluded")
    require(entry["adjustment"] == adjustment, "adjustment_mismatch")
    require(adjustment in {"unadjusted", "adjusted"}, "unknown_adjustment")
    require(
        adjustment != "adjusted" or bool(entry.get("adjustment_method")), "unverified_adjustment"
    )


def capture_market(
    store: MarketDataStore,
    tickers: Sequence[str],
    start: date,
    end: date,
    *,
    information_mode: str,
    data_origin: str,
    captured_at: str | datetime,
    quality: FrozenJSON,
) -> tuple[FrozenJSON, ...]:
    """Only read(); latest-only bars cannot prove a historical PIT revision archive."""
    require(information_mode in {"historical_research", "point_in_time"}, "invalid_mode")
    require(information_mode != "point_in_time", "pit_history_unavailable")
    require(data_origin in {"real", "synthetic"}, "invalid_origin")
    validate_range(start, end)
    cutoff = utc(captured_at)
    q = quality.to_dict()
    validate_quality(q)
    require(utc(q["verified_at"]) <= cutoff, "quality_not_available_at_capture")
    require(bool(tickers) and len(set(tickers)) == len(tickers), "invalid_tickers")
    output: list[FrozenJSON] = []
    profiles: set[tuple[str, str, str]] = set()
    for ticker in sorted(tickers):
        validate_ticker(ticker)
        rows = store.read(ticker, start, end)
        require(bool(rows), "market_data_missing")
        require(
            [b.trading_date for b in rows] == sorted({b.trading_date for b in rows}),
            "invalid_market_order_or_duplicates",
        )
        for bar in rows:
            bar.__post_init__()
            require(
                bar.ticker == ticker and start <= bar.trading_date <= end, "invalid_market_range"
            )
            require(utc(bar.collected_at) <= cutoff, "future_market_receipt")
            origin = "synthetic" if bar.provider == "fake" else "real"
            require(origin == data_origin, "mixed_data_origin")
            quality_check(q, ticker, bar.trading_date.isoformat(), bar.adjustment)
            profiles.add((bar.provider, bar.market, bar.adjustment))
            original = bar.to_dict()
            output.append(
                FrozenJSON.freeze(
                    {
                        "kind": "latest_daily",
                        "ticker": ticker,
                        "trading_date": bar.trading_date.isoformat(),
                        "revision": digest(original),
                        "source_received_at": instant(bar.collected_at),
                        "data_origin": origin,
                        "adjustment": bar.adjustment,
                        "market": bar.market,
                        "fields": {
                            k: original[k] if k == "volume" else str(original[k])
                            for k in sorted(MARKET_FIELDS - {"price"})
                        },
                        "original": original,
                    }
                )
            )
    require(len(profiles) == 1, "mixed_price_basis")
    return tuple(output)


def source_check(source: FrozenJSON, event: dict[str, Any]) -> None:
    s = source.to_dict()
    fields(
        s,
        "kind ticker trading_date revision source_received_at data_origin adjustment market "
        "fields original",
    )
    require(s["kind"] in {"latest_daily", "synthetic_event"}, "unsupported_source_kind")
    if s["kind"] == "synthetic_event":
        require(s["data_origin"] == "synthetic", "synthetic_source_required")
    for key in ("ticker", "trading_date", "revision", "data_origin", "adjustment", "market"):
        require(s[key] == event[key], "source_metadata_mismatch")
    require(utc(s["source_received_at"]) == utc(event["source_received_at"]), "receipt_mismatch")
    require(isinstance(s["fields"], dict) and bool(s["fields"]), "source_fields_missing")
    require(set(s["fields"]) <= MARKET_FIELDS, "unsupported_source_field")
    normalized = {k: integer(v) if k == "volume" else money(v) for k, v in s["fields"].items()}
    for key, value in event["public_fields"].items():
        require(key in normalized and normalized[key] == value, "source_value_mismatch")
    if s["kind"] == "latest_daily":
        bar = DailyBar.from_dict(s["original"])
        require(digest(bar.to_dict()) == s["revision"], "daily_revision_mismatch")
        for key in ("ticker", "trading_date", "market", "adjustment"):
            require(bar.to_dict()[key] == s[key], "daily_metadata_mismatch")
        require(instant(bar.collected_at) == instant(s["source_received_at"]), "receipt_mismatch")
        require(
            ("synthetic" if bar.provider == "fake" else "real") == s["data_origin"],
            "mixed_data_origin",
        )
        for key, value in normalized.items():
            require(
                key != "price" and str(bar.to_dict()[key]) == str(value), "daily_value_mismatch"
            )


@dataclass(frozen=True)
class FrozenTape:
    manifest: RunManifest
    policy: ExecutionPolicy
    events: tuple[MarketEvent, ...]
    sources: tuple[FrozenJSON, ...]
    release_plan: FrozenJSON
    quality: FrozenJSON

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "sources", tuple(self.sources))
        validate_run(self.manifest, self.policy, self.events)
        m, plan, q = self.manifest.to_dict(), self.release_plan.to_dict(), self.quality.to_dict()
        fields(plan, "information_mode data_origin usage_restriction releases analysis_releases")
        require(isinstance(plan["analysis_releases"], list), "analysis_release_evidence_missing")
        for key in PROVENANCE:
            require(plan[key] == m[key], "plan_provenance_mismatch")
        require(
            self.policy.to_dict()["availability_policy"]["content_hash"]
            == self.release_plan.identifier,
            "availability_policy_mismatch",
        )
        if m["information_mode"] == "historical_research":
            require(
                m["availability_assumption_id"] == self.release_plan.identifier,
                "assumption_mismatch",
            )
        require(
            m["quality_manifest"]["content_hash"] == self.quality.identifier,
            "quality_hash_mismatch",
        )
        validate_quality(q)
        require(isinstance(plan["releases"], list), "release_evidence_missing")
        require(len(plan["releases"]) == len(self.events), "release_evidence_missing")
        sources = {s.identifier: s for s in self.sources}
        require(len(sources) == len(self.sources), "duplicate_source")
        declared = {r["content_hash"] for r in m["market_snapshots"]}
        require(set(sources) <= declared, "undeclared_source")
        prior: dict[tuple[str, str], tuple[str, datetime]] = {}
        revisions: set[tuple[str, str, str]] = set()
        ids: set[str] = set()
        used: set[str] = set()
        for event, release in zip(self.events, plan["releases"], strict=True):
            e = event.to_dict()
            fields(release, "sequence previous_revision field_times")
            require(integer(release["sequence"]) == e["sequence"], "release_sequence_mismatch")
            require(event.event_id not in ids, "duplicate_event")
            ids.add(event.event_id)
            source_id = sha(e["source"]["content_hash"])
            require(source_id in sources, "source_evidence_missing")
            used.add(source_id)
            source_check(sources[source_id], e)
            quality_check(q, e["ticker"], e["trading_date"], e["adjustment"])
            require(
                self.quality.identifier in e["quality_evidence_ids"], "quality_evidence_missing"
            )
            require(e["quality_status"] == "verified", "quality_unverified")
            if m["information_mode"] == "point_in_time":
                require(
                    sources[source_id].to_dict()["kind"] != "latest_daily",
                    "pit_history_unavailable",
                )
                require(m["data_origin"] == "synthetic", "pit_receipt_evidence_unavailable")
                require(
                    utc(q["verified_at"]) <= utc(e["quality_available_at"]),
                    "future_quality_evidence",
                )
            series_key = (e["ticker"], e["trading_date"])
            revision_key = (*series_key, e["revision"])
            require(revision_key not in revisions, "revision_conflict")
            revisions.add(revision_key)
            previous = prior.get(series_key)
            if previous is None:
                require(release["previous_revision"] is None, "missing_previous_revision")
            else:
                require(
                    release["previous_revision"] == previous[0] and e["revision"] != previous[0],
                    "revision_conflict",
                )
                require(utc(e["source_received_at"]) > previous[1], "revision_receipt_reversed")
            prior[series_key] = (text(e["revision"]), utc(e["source_received_at"]))
            times = release["field_times"]
            require(
                isinstance(times, dict) and set(times) == set(e["public_fields"]),
                "field_evidence_missing",
            )
            for stamp in times.values():
                require(utc(stamp) >= utc(e["available_at"]), "field_before_event_availability")
        require(used == set(sources), "unused_source")

    def to_json(self) -> str:
        return canonical(
            {
                "manifest": self.manifest.to_dict(),
                "policy": self.policy.to_dict(),
                "events": [e.to_dict() for e in self.events],
                "sources": sorted((s.to_dict() for s in self.sources), key=digest),
                "release_plan": self.release_plan.to_dict(),
                "quality": self.quality.to_dict(),
            }
        )

    @classmethod
    def from_json(cls, wire: str) -> "FrozenTape":
        v = fields(
            FrozenJSON(wire).to_dict(), "manifest policy events sources release_plan quality"
        )
        return cls(
            RunManifest.from_dict(v["manifest"]),
            ExecutionPolicy.from_dict(v["policy"]),
            tuple(MarketEvent.from_dict(e) for e in v["events"]),
            tuple(FrozenJSON.freeze(s) for s in v["sources"]),
            FrozenJSON.freeze(v["release_plan"]),
            FrozenJSON.freeze(v["quality"]),
        )

    @property
    def identifier(self) -> str:
        return FrozenJSON(self.to_json()).identifier

    def view(self, clock: VirtualClock) -> PublicView:
        m = self.manifest.to_dict()
        cutoff = utc(clock.cutoff)
        start, end = utc(m["start_at"]), utc(m["end_at"])
        require(
            start <= cutoff <= end
            if m["time_basis"]["boundary"] == "inclusive"
            else start < cutoff < end,
            "outside_run_period",
        )
        items: list[dict[str, Any]] = []
        for event, release in zip(
            self.events, self.release_plan.to_dict()["releases"], strict=True
        ):
            e = event.to_dict()
            if e["sequence"] > clock.sequence or utc(e["available_at"]) > cutoff:
                continue
            public = {
                k: v
                for k, v in e["public_fields"].items()
                if utc(release["field_times"][k]) <= cutoff
            }
            if not public:
                continue
            # Never include aggregate source/run/event IDs: they can hash hidden future values.
            items.append(
                {
                    k: e[k]
                    for k in (
                        "sequence",
                        "ticker",
                        "trading_date",
                        "event_at",
                        "source_received_at",
                        "available_at",
                        "adjustment",
                        "quality_flags",
                    )
                }
                | {"public_fields": public, "market_state": "not_execution_evidence"}
            )
        payload = {
            **{k: m[k] for k in PROVENANCE},
            "execution_mode": m["execution_mode"],
            "scope": "research",
            "executable": False,
            "operational_eligible": False,
            "cutoff": clock.cutoff,
            "sequence": clock.sequence,
            "items": items,
        }
        return PublicView(
            FrozenJSON.freeze(payload),
            FrozenJSON.freeze(
                {
                    "run_id": self.manifest.identifier,
                    "tape_id": self.identifier,
                    "revision_metadata": [
                        {"event_id": e.event_id, "revision": e.to_dict()["revision"]}
                        for e in self.events
                        if e.to_dict()["sequence"] <= clock.sequence
                        and utc(e.to_dict()["available_at"]) <= cutoff
                    ],
                    "event_ids": [
                        e.event_id
                        for e in self.events
                        if e.to_dict()["sequence"] <= clock.sequence
                        and utc(e.to_dict()["available_at"]) <= cutoff
                    ],
                }
            ),
        )
