"""Read-only whole-database metadata inventory, training-only universe selection."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.real_market_dataset import _protocol
from donghak_stock_vision.storage.real_market_training import _quiescent


def inspect(d: dict[str, Any]) -> dict[str, Any]:
    _, access = _protocol(d)
    path = Path(d["research"]["database"])
    pin = d["research"]["pin"]
    _quiescent(path, pin)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        total = db.execute(
            "SELECT COUNT(*),COUNT(DISTINCT ticker),COUNT(DISTINCT trading_date),"
            "MIN(trading_date),MAX(trading_date) FROM bars"
        ).fetchone()
        # Full inventory is dates/counts only. Protected-period payloads are not queried.
        whole = {
            t: dict(rows=n, first_date=a, last_date=b)
            for t, n, a, b in db.execute(
                "SELECT ticker,COUNT(*),MIN(trading_date),MAX(trading_date) "
                "FROM bars GROUP BY ticker"
            )
        }
        args = (access.training.start.isoformat(), access.training.end.isoformat())
        dates = db.execute(
            "SELECT COUNT(DISTINCT trading_date) FROM bars WHERE trading_date BETWEEN ? AND ?", args
        ).fetchone()[0]
        rows = db.execute(
            """SELECT ticker,COUNT(*),MIN(trading_date),MAX(trading_date),
          SUM(CASE WHEN json_extract(payload,'$.open')>0 AND json_extract(payload,'$.high')>0
          AND json_extract(payload,'$.low')>0 AND json_extract(payload,'$.volume')>0
          AND json_extract(payload,'$.trading_value')>0 THEN 1 ELSE 0 END),
          AVG(json_extract(payload,'$.trading_value')),
          GROUP_CONCAT(DISTINCT json_extract(payload,'$.market')),
          GROUP_CONCAT(DISTINCT json_extract(payload,'$.provider')),
          GROUP_CONCAT(DISTINCT json_extract(payload,'$.adjustment'))
          FROM bars WHERE trading_date BETWEEN ? AND ? GROUP BY ticker ORDER BY ticker""",
            args,
        ).fetchall()
        source_links = db.execute("SELECT COUNT(*) FROM bar_sources").fetchone()[0]
        primary = db.execute("PRAGMA index_list(bars)").fetchall()
    records = {}
    for t, n, a, b, positive, value, market, provider, adjustment in rows:
        reasons = []
        if n != dates:
            reasons.append("incomplete_training_panel")
        if positive < dates * 0.98:
            reasons.append("training_no_trade_or_invalid_fields")
        if market != "KOSPI" or provider != "krx" or adjustment != "unadjusted":
            reasons.append("source_metadata_out_of_scope")
        records[t] = dict(
            ticker=t,
            training_rows=n,
            positive_rows=positive,
            missing_training_panel_dates=dates - n,
            first_date=a,
            last_date=b,
            mean_training_value=value,
            market=market,
            provider=provider,
            adjustment=adjustment,
            metadata_eligible=not reasons,
            reasons=reasons,
            originals_replayed=False,
        )
    for t, v in whole.items():
        if t not in records:
            records[t] = v | dict(
                ticker=t,
                metadata_eligible=False,
                reasons=["no_training_window_rows"],
                originals_replayed=False,
            )
    _quiescent(path, pin)
    return dict(
        version="multi_stock_inventory_v1",
        database_pin=pin,
        split_pin=access.identifier,
        full_metadata=dict(
            zip(("observations", "tickers", "dates", "first_date", "last_date"), total, strict=True)
        ),
        training_window=access.training.to_dict(),
        reference_training_dates=dates,
        inventory=records,
        source_links=source_links,
        unique_bar_primary_key=any(r[2] for r in primary),
        protected_payloads_read=False,
        selection_uses_training_only=True,
        biases=dict(
            survivorship="UNKNOWN",
            historical_listings="UNKNOWN",
            delisted_coverage="UNKNOWN",
            sector="UNKNOWN",
            market_cap="UNKNOWN",
            official_session_completeness="UNKNOWN",
            liquidity="training_value_proxy_not_market_cap",
            publication_chronology="not_authenticated",
        ),
    )


def cohorts(inventory: dict[str, Any], core: list[str]) -> list[list[str]]:
    eligible = [r for r in inventory["inventory"].values() if r["metadata_eligible"]]
    require(
        len(eligible) >= 50 and set(core) <= {r["ticker"] for r in eligible},
        "multi_stock_universe_insufficient",
    )
    ordered = sorted(eligible, key=lambda r: (r["mean_training_value"], r["ticker"]))
    buckets = [ordered[i * len(ordered) // 3 : (i + 1) * len(ordered) // 3] for i in range(3)]
    for bucket in buckets:
        bucket.sort(key=lambda r: digest(r["ticker"]))
    used = set(core)
    selected = list(core)
    for i in range(45):
        bucket = buckets[i % 3]
        r = next(r for r in bucket if r["ticker"] not in used)
        selected.append(r["ticker"])
        used.add(r["ticker"])
    return [sorted(selected[:25]), sorted(selected[25:])]
