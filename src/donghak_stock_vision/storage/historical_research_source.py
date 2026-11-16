"""Disjoint, bounded source windows; original split and protected payloads stay sealed."""

from contextlib import closing
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models import research_v2
from donghak_stock_vision.storage.real_market_training import (
    MAX_RAW_BYTES,
    MAX_ROWS,
    RealMarketTrainingRequest,
    _quiescent,
    _VerifiedMarket,
)


def window(d: dict[str, Any]) -> ResearchDateRange:
    fields(d, "start end")
    return ResearchDateRange(date.fromisoformat(d["start"]), date.fromisoformat(d["end"]))


def validate(spec: dict[str, Any], access: ExplicitResearchSplit) -> list[ResearchDateRange]:
    fields(
        spec,
        "windows search_end evaluations diagnostic policy_version references"
        + (" include_original_internal_folds" if "include_original_internal_folds" in spec else "")
        + (" source_parent" if "source_parent" in spec else ""),
    )
    require(
        type(spec.get("include_original_internal_folds", False)) is bool,
        "historical_internal_fold_flag",
    )
    windows = [window(w) for w in spec["windows"]]
    require(
        len(windows) == 2
        and windows[0] == access.training
        and windows[1].start > access.final_test.end
        and windows[1].start <= date.fromisoformat(spec["search_end"]) < windows[1].end,
        "historical_disjoint_original_roles",
    )
    diagnostic = window(spec["diagnostic"])
    periods = [window(w) for w in spec["evaluations"]]
    require(
        2 <= len(periods) <= 4
        and all(
            windows[1].start < w.start <= w.end <= date.fromisoformat(spec["search_end"])
            for w in periods
        )
        and all(a.end < b.start for a, b in zip(periods, periods[1:], strict=False))
        and date.fromisoformat(spec["search_end"])
        < diagnostic.start
        <= diagnostic.end
        <= windows[1].end
        and spec["policy_version"] == "historical_robust_lexicographic_v1"
        and type(spec["references"]) is list
        and len(spec["references"]) <= 2,
        "historical_predeclared_periods",
    )
    return windows


def snapshot(
    request: RealMarketTrainingRequest, spec: dict[str, Any]
) -> tuple[FrozenJSON, FrozenJSON]:
    windows = validate(spec, request.access_split)
    require("source_parent" not in spec, "historical_reuse_requires_plan_owner")
    parts, proofs = [], []
    raw_bytes = raw_responses = 0
    for span in windows:
        with closing(_VerifiedMarket(request, source_window=span)) as market:
            raw_bytes += market.raw_bytes_verified
            raw_responses += market.raw_responses_verified
            require(
                raw_bytes <= MAX_RAW_BYTES and raw_responses <= 4096,
                "historical_aggregate_raw_bound",
            )
            parts.append(
                capture(
                    market,
                    list(request.tickers),
                    span.start,
                    span.end,
                    request.cutoff,
                    "historical_research",
                    acknowledge=True,
                    research_calendar=request.research_calendar,
                )
            )
            proofs.append(
                dict(window=span.to_dict(), sources=market.sources, rows_verified=market.row_count)
            )
    rows = {t: [r for p in parts for r in p["rows"][t]] for t in request.tickers}
    require(sum(len(v) for v in rows.values()) <= MAX_ROWS, "historical_aggregate_row_bound")
    for values in rows.values():
        require(
            [r["trading_date"] for r in values] == sorted({r["trading_date"] for r in values}),
            "historical_duplicate_observation",
        )
    source = dict(
        version="historical_disjoint_source_v1",
        request=request.document.to_dict(),
        request_pin=request.document.identifier,
        windows=proofs,
        rows_verified=sum(len(v) for v in rows.values()),
        selected_rows_pin=digest(rows),
        local_originals_verified=True,
        original_receipts_verified=request.source_contract == "receipt_archive_v1",
        protected_final_test_read=False,
        original_validation_read=False,
        issuer_authenticated=False,
        publication_authenticated=False,
    )
    value = parts[0] | dict(
        unavailable_dates={
            t: [d for p in parts for d in p["unavailable_dates"][t]]
            + [
                request.access_split.validation.start.isoformat(),
                request.access_split.final_test.end.isoformat(),
            ]
            for t in rows
        },
        rows=rows,
        end=windows[-1].end.isoformat(),
        real_market_source=source,
        historical_windows=[w.to_dict() for w in windows],
    )
    _quiescent(request.database, request.database_pin)
    return FrozenJSON.freeze(value), FrozenJSON.freeze(source)


def features(value: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    cache: dict[str, list[dict[str, Any]]] = {t: [] for t in value["rows"]}
    for window in value["historical_windows"]:
        part = value | dict(
            rows={
                t: [r for r in rows if window["start"] <= r["trading_date"] <= window["end"]]
                for t, rows in value["rows"].items()
            }
        )
        for t, entries in research_v2.prepare_features(part).items():
            cache[t].extend(entries)
    return cache


def inventory(d: dict[str, Any]) -> dict[str, Any]:
    """Payload eligibility is the original training owner; period survey is metadata only."""
    import sqlite3
    from pathlib import Path

    from donghak_stock_vision.storage.real_market_dataset import _protocol
    from donghak_stock_vision.storage.stock_universe import inspect

    result = inspect(d)
    _, access = _protocol(d)
    spans = validate(d["historical"], access)
    path = Path(d["research"]["database"])
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        result["period_metadata"] = [
            dict(
                window=w.to_dict(),
                rows=[
                    dict(ticker=t, observations=n, dates=nd, first_date=a, last_date=b)
                    for t, n, nd, a, b in db.execute(
                        "SELECT ticker,COUNT(*),COUNT(DISTINCT trading_date),"
                        "MIN(trading_date),MAX(trading_date) FROM bars "
                        "WHERE trading_date BETWEEN ? AND ? GROUP BY ticker",
                        (w.start.isoformat(), w.end.isoformat()),
                    )
                ],
            )
            for w in spans
        ]
    _quiescent(path, d["research"]["pin"])
    return result
