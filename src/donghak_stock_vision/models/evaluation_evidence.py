"""Evidence counts, never model selection or performance approval."""

from collections import Counter
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import timestamp

MINIMUM_ANCHORS = 20
MINIMUM_CLASS_ROWS = 10  # Existing historical research summary requirement.


def evidence_policy() -> dict[str, Any]:
    return dict(
        version="calendar_evidence_v1",
        minimum_calendar_anchors=MINIMUM_ANCHORS,
        minimum_class_rows=MINIMUM_CLASS_ROWS,
        anchor_rule="union_dates_purged_through_maximum_closed_outcome_interval",
    )


def calendar_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Union tickers before greedily purging complete outcome intervals.

    The input owner supplies validated labels and exact sessions. No weekday
    calendar, imputation, new label definition or stock-as-time replication.
    """
    require(len(rows) <= 100_000, "evidence_row_bound")
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        require(
            date.fromisoformat(r["day"]).isoformat() == r["day"]
            and date.fromisoformat(r["end"]).isoformat() == r["end"]
            and r["day"] <= r["end"]
            and type(r["label"]) is int
            and r["label"] in (0, 1),
            "evidence_invalid_interval_or_label",
        )
        key = r["ticker"], r["day"]
        require(key not in unique or unique[key] == r, "evidence_conflicting_observation")
        unique[key] = r
    ordered = sorted(unique.values(), key=lambda r: (r["day"], r["ticker"]))
    ends: dict[str, str] = {}
    for r in ordered:
        ends[r["day"]] = max(ends.get(r["day"], ""), r["end"])
    require(len({r["ticker"] for r in ordered}) <= 50, "evidence_ticker_bound")
    days: list[str] = []
    end = ""
    for day in sorted(ends):
        if day > end:
            days.append(day)
            end = ends[day]
    selected = [r for r in ordered if r["day"] in set(days)]
    classes = Counter(r["label"] for r in selected)
    both = classes[0] > 0 and classes[1] > 0
    enough = len(days) >= MINIMUM_ANCHORS and min(classes[0], classes[1]) >= MINIMUM_CLASS_ROWS
    return dict(
        valid_rows=len(ordered),
        duplicate_rows_collapsed=len(rows) - len(ordered),
        nonoverlapping_calendar_anchors=len(days),
        calendar_dates=days,
        minimum_calendar_anchors=MINIMUM_ANCHORS,
        criteria=evidence_policy(),
        missing_calendar_anchors=max(0, MINIMUM_ANCHORS - len(days)),
        positive_rows=sum(r["label"] == 1 for r in ordered),
        negative_rows=sum(r["label"] == 0 for r in ordered),
        anchored_positive_rows=classes[1],
        anchored_negative_rows=classes[0],
        both_classes_present=both,
        minimum_class_rows=MINIMUM_CLASS_ROWS,
        missing_positive_rows=max(0, MINIMUM_CLASS_ROWS - classes[1]),
        missing_negative_rows=max(0, MINIMUM_CLASS_ROWS - classes[0]),
        excluded_overlap_rows=len(ordered) - len(selected),
        status="READY_FOR_EXISTING_EVALUATOR" if enough else "INSUFFICIENT_EVIDENCE",
        periods={
            month: dict(
                rows=sum(r["day"].startswith(month) for r in ordered),
                global_anchors=sum(d.startswith(month) for d in days),
            )
            for month in sorted({r["day"][:7] for r in ordered})
        },
        by_ticker={
            t: _ticker([r for r in ordered if r["ticker"] == t])
            for t in sorted({r["ticker"] for r in ordered})
        },
        earliest_guaranteed_evaluation_at=None,
        next_evidence_condition="verified_outcomes_and_20_disjoint_anchors_with_both_classes",
        independent_future_predictive_power_established=False,
        performance_approved=False,
        pit_qualified=False,
        execution_authorized=False,
    )


def _ticker(rows: list[dict[str, Any]]) -> dict[str, int]:
    last = ""
    count = 0
    for r in rows:
        if r["day"] > last:
            count += 1
            last = r["end"]
    return dict(
        valid_rows=len(rows),
        nonoverlapping_anchors=count,
        positive=sum(r["label"] == 1 for r in rows),
        negative=sum(r["label"] == 0 for r in rows),
    )


def shadow_rows(
    entries: list[dict[str, Any]], registered_at: str, baseline_count: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Caller verifies complete journal/source replay and frozen model binding."""
    withdrawn = {e["payload"]["prediction_id"] for e in entries if e["kind"] == "correction"}
    latest = {
        e["payload"]["prediction_id"]: e["payload"] for e in entries if e["kind"] == "outcome"
    }
    counts: Counter[str] = Counter(
        dict.fromkeys(
            (
                "pending",
                "evaluated",
                "pre_registration_predictions",
                "historical_replay_excluded",
                "fixture_excluded",
                "withdrawn",
                "unavailable",
            ),
            0,
        )
    )
    rows = []
    for i, e in enumerate(entries):
        if e["kind"] != "prediction":
            continue
        p = e["payload"]
        if i < baseline_count or timestamp(p["generated_at"]) < timestamp(registered_at):
            counts["pre_registration_predictions"] += 1
            continue
        if p["mode"] != "research_forward_candidate":
            counts["historical_replay_excluded"] += 1
            continue
        if p["source_classification"] != "local_provider_capture":
            counts["fixture_excluded"] += 1
            continue
        if p["prediction_id"] in withdrawn:
            counts["withdrawn"] += 1
            continue
        outcome = latest.get(p["prediction_id"], {})
        state = outcome.get("state", "pending")
        counts[state] += 1
        if state == "evaluated":
            rows.append(
                dict(
                    ticker=p["ticker"],
                    day=p["target_trading_date"],
                    end=p["outcome_sessions"][-1],
                    label=outcome["actual_label"],
                )
            )
    return rows, dict(counts)
