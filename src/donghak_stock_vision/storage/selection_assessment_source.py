"""New post-Q3 readout; Q3 prices are warm-up only and never produce labels."""

from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import digest, timestamp
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.research_split import ResearchDateRange
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models import research_v2
from donghak_stock_vision.storage.real_market_dataset import (
    _Panel,
    _protocol,
    verify_operational_window,
)
from donghak_stock_vision.storage.real_market_training import (
    RealMarketTrainingRequest,
    _quiescent,
    _VerifiedMarket,
)


def prepare(value: dict[str, Any], tickers: list[str]) -> dict[str, Any]:
    fields(value, "research protocol operational context_window evaluation_window cutoff")
    protocol, access = _protocol(value)
    cw = value["context_window"]
    ew = value["evaluation_window"]
    start, end = date.fromisoformat(ew["start"]), date.fromisoformat(ew["end"])
    require(
        date.fromisoformat(cw["start"]) > access.final_test.end
        and cw["start"] <= cw["end"] < ew["start"]
        and start >= date(2026, 10, 1)
        and start <= end,
        "selection_q3_or_final_test_forbidden",
    )
    r = value["research"]
    req = RealMarketTrainingRequest(
        Path(r["database"]),
        r["pin"],
        access,
        access.identifier,
        None,
        None,
        tuple(tickers),
        timestamp(value["cutoff"]),
        ResearchCalendarPolicy.from_dict(protocol["research_calendar"]),
        r["source_contract"],
    )
    window = ResearchDateRange(date.fromisoformat(cw["start"]), date.fromisoformat(cw["end"]))
    with closing(_VerifiedMarket(req, source_window=window)) as market:
        rows = {t: market.read(t, window.start, window.end) for t in tickers}
        proof = dict(
            window=cw,
            sources=market.sources,
            rows_verified=market.row_count,
            raw_bytes=market.raw_bytes_verified,
        )
    op, op_proofs = verify_operational_window(
        value["operational"],
        tuple(tickers),
        ResearchDateRange(start, end),
        strict_forward_membership=False,
    )
    seen = {(t, b.trading_date) for t, bars in rows.items() for b in bars}
    for bar in op:
        require((bar.ticker, bar.trading_date) not in seen, "selection_duplicate_source_row")
        seen.add((bar.ticker, bar.trading_date))
        rows[bar.ticker].append(bar)
    require(
        len(seen) <= 20000 and proof["raw_bytes"] + len(op_proofs) * 2 * 1024**2 <= 512 * 1024**2,
        "selection_source_bound",
    )
    for bars in rows.values():
        bars.sort(key=lambda b: b.trading_date)
    snapshot = capture(
        _Panel(rows),
        tickers,
        window.start,
        end,
        req.cutoff,
        "historical_research",
        acknowledge=True,
        research_calendar=req.research_calendar,
    )
    cache = research_v2.prepare_features(snapshot)
    goals = {}
    for h in (1, 5, 20):
        samples, _, exclusions = research_v2.build(
            snapshot, h, feature_cache=cache, label_start=start
        )
        selected = [s.to_dict() for s in samples if s.label_end is not None and s.label_end <= end]
        require(
            all(s["trading_date"] >= ew["start"] for s in selected), "selection_q3_label_forbidden"
        )
        goals[str(h)] = dict(samples=selected, excluded=exclusions)
    _quiescent(req.database, req.database_pin)
    return dict(
        version="selection_assessment_source_v1",
        goals=goals,
        tickers=tickers,
        source_rows=len(seen),
        source_rows_pin=digest(snapshot["rows"]),
        research_proof=proof,
        operational_proofs=op_proofs,
        evaluation_window=ew,
        context_only_window=cw,
        q3_labels_generated=False,
        pit_qualified=False,
        publication_authenticated=False,
        original_validation_read=False,
        protected_final_test_read=False,
    )
