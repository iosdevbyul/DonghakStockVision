"""Read-only verification of an explicitly pinned offline operational KRX snapshot."""

import argparse
import json
from pathlib import Path

from donghak_stock_vision.api.ohlcv import PinnedOperationalReader


def verify(
    path: Path, expected_sha256: str, *, expected_tickers: int | None = None
) -> dict[str, object]:
    """Cross-check full catalog, coverage summary and exact OHLCV identity."""
    if expected_tickers is not None and expected_tickers < 1:
        raise ValueError("expected ticker count must be positive")
    reader = PinnedOperationalReader(path, expected_sha256=expected_sha256)
    summary = reader.summary()
    tickers: set[str] = set()
    inspected_bars = 0
    offset = 0
    while offset < summary.tickerCount:
        page = reader.catalog(offset, 100)
        if page.total != summary.tickerCount:
            raise ValueError("catalog count differs from pinned snapshot summary")
        if not page.items:
            raise ValueError("catalog pagination ended before expected ticker count")
        for item in page.items:
            if item.ticker in tickers:
                raise ValueError("duplicate ticker across catalog pages")
            tickers.add(item.ticker)
            series = reader.series(item.ticker)
            if series is None or not series.bars:
                raise ValueError("catalog ticker has no OHLCV bars")
            if series.ticker != item.ticker:
                raise ValueError("OHLCV ticker mismatch")
            if series.bars[-1].date != item.latestDataDate:
                raise ValueError("catalog latest date disagrees with OHLCV")
            if any(
                bar.high < max(bar.open, bar.close, bar.low)
                or bar.low > min(bar.open, bar.close, bar.high)
                for bar in series.bars
            ):
                raise ValueError("invalid OHLCV bounds")
            inspected_bars += len(series.bars)
        offset += len(page.items)
    if len(tickers) != summary.tickerCount:
        raise ValueError("unique catalog tickers differ from summary")
    if expected_tickers is not None and len(tickers) != expected_tickers:
        raise ValueError("actual ticker count differs from operator expectation")
    return {
        "schemaVersion": "operational_snapshot_verification_v1",
        "evidenceStatus": "unreviewed",
        "snapshotSha256": expected_sha256,
        "observationCount": summary.observationCount,
        "tickerCount": summary.tickerCount,
        "tradingDayCount": summary.tradingDayCount,
        "firstTradingDate": summary.firstTradingDate,
        "lastTradingDate": summary.lastTradingDate,
        "ohlcvBarsInspected": inspected_bars,
        "verified": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dsv-operational-verify")
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--expected-tickers", type=int)
    args = parser.parse_args(argv)
    try:
        result = verify(args.snapshot, args.sha256, expected_tickers=args.expected_tickers)
    except (OSError, ValueError):
        parser.exit(2, "operational snapshot verification failed\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
