# Read-only real market-data analytics

## Implementation plan (recorded before implementation)

Base main: `558c5f9db1de4a2b5142c2ae7a3be29b51efba4f`, merged PR #140.

The original `bars(ticker, trading_date, payload)` primary key supports indexed
symbol/date range reads. `SQLiteStore` initializes schema and is unsuitable for
protected read-only inspection. The existing `OhlcvMarketReader` supplies raw bars
but performs whole-database payload scans at startup and is API/Pydantic-specific;
it neither computes these descriptive metrics nor exposes bounded quality results.
Research features and simulation P&L are different calculations and will not be
repurposed as descriptive analytics.

Add one bounded SQLite analytics adapter and a small `dsv market-data` CLI group.
Reuse `DailyBar` validation, original explicit research splits, canonical JSON,
original KIND calendar normalization and the repository's immutable read-only
SQLite/hash pattern. Add no tables, migrations, HTTP routes, collector, custody
system, authorization or strategy execution path.

Global coverage reads **keys/counts only**; original OHLCV payloads are loaded only
inside the explicit bounded request. Before opening the DB, reject any overlap
with the original split's final-test range. Training/tuning or later unassigned
records can be described outside that protected interval; this never reclassifies
them as strategy inputs or changes original split authorization. Global latest
OHLCV is returned only when it falls in the requested authorized window; otherwise
it is explicitly unavailable. This preserves holdout isolation while permitting
inspection of later local records without calling them PIT-qualified.

Implement close-to-close simple returns, exact native-price change and volume
statistics, and sample standard deviation of log returns (not annualized). Avoid
bridging invalid/duplicate observations. Calendar gaps are classified only with
original verified KIND month evidence; weekends/holidays are never assumed missing.
Null metadata names remain null unless an existing independently verified metadata
reader is explicitly supplied; no hardcoded Samsung name or ticker-suffix inference.

## Commands and source selection

All commands print canonical `market_data_analytics_v1` JSON to stdout. Logs and
configuration/storage errors use the existing CLI stderr/exit-code conventions.
No optional API/learning package or HTTP server is needed. The three views share
one query/validation/calculation path; history additionally includes the selected
OHLCV rows. Quality returns structured issues and available descriptive statistics.

Locate the explicit database, inspect its reviewed SHA-256 and the **original**
research split metadata. Do not instantiate the writable store or run collection.
For this repository snapshot:

```sh
db=.data/market.sqlite3
split=.data/phase2-training-20261001/summary.json
# Confirm the actual reviewed snapshot, rather than assuming today's file matches.
shasum -a 256 "$db"
common=(--db "$db"
        --db-sha256 2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42
        --symbol 005930 --split "$split"
        --split-pin 6d4fa516227f13640cdb785f029359134d51b074468be543654c8873f9296150
        --data-origin local_krx)
dsv market-data summary "${common[@]}" --start 2026-09-01 --end 2026-10-01
dsv market-data history "${common[@]}" --start 2024-06-24 --end 2024-06-28
dsv market-data quality "${common[@]}" --start 2024-06-24 --end 2024-06-28 \
  --calendar-source docs/evidence/historical-market-readiness.json \
  --calendar-raw docs/evidence/baseline-official/raw/calendar-2024-06.html.gz \
  --calendar-pin fa247837b50e45617995a99aa58cdea20697a73f470b1a79a7cc2eb7eea4c2cb
```

The root CLI's legacy `--db` is not a default for these commands: use the required
`market-data <view> --db` explicitly. A missing DB is never created. A changed DB
pin fails closed; do not automatically bless a replacement file. Active WAL or
journal sidecars are rejected. A quiescent fixed snapshot is required, verified
by streamed full-file SHA before and after the query, plus `mode=ro&immutable=1`
and `PRAGMA query_only=ON`. No migrations, raw archive reads or network calls occur.

`--split` accepts the existing original summary's `split` object or an original
standalone `ExplicitResearchSplit` JSON. Its independently retained configuration
pin is required. Final-test/holdout overlap is rejected before source/calendar I/O,
including requests crossing that interval. The existing actual split protects
2024-07-01 through 2025-06-30 inclusive. Later unassigned dates are descriptive only,
not newly authorized training, tuning or strategy inputs. Operators must retain
and supply the original applicable split, never invent a replacement to expose
protected evaluation data. Other frozen datasets or strategy/OOS artifacts are
not opened or retuned by this command.

Missing symbol, invalid symbol/date range, protected overlap, bad pins, unavailable
DB/schema, SQL-work limits, excessive requested rows and oversized persisted
payloads produce explicit errors (exit 2). A valid range with no selected rows
returns `status=empty` and unavailable statistics. Detected row quality problems
are returned as diagnostics rather than repaired or silently discarded. JSON
inspection returning 0 is not approval of data quality, trading or PIT safety.

## Calculation conventions

* Close-to-close simple return: `close[t] / close[t-1] - 1`, a fraction rather than
  a percent. The first selected observation has no predecessor in the request;
  no hidden lookback outside the requested window is loaded.
* Period price change: last minus first selected close, in native KRW.
  Percentage change: `(last / first - 1) * 100`. These are price statistics,
  not fee/tax-adjusted strategy returns or total return including distributions.
* Average observed daily volume: selected valid share volume sum divided by
  selected observation count, not an invented complete-session average.
* Realized historical log-return volatility: let `r[t]=ln(close[t]/close[t-1])`.
  Return `sqrt(sum((r-mean(r))^2)/(n-1))`, requiring at least two eligible return
  pairs. No annualization, assumed 252-session year or risk metric is added.
* Exact integer source prices/volumes feed Decimal arithmetic at precision 50,
  ROUND_HALF_EVEN; ratios, percentages and volatility render as deterministic
  18-place decimal strings. Raw integer prices/volumes remain integers.

When the supplied original calendar fully covers the selected date window and
market, missing sessions exclude the crossing return pair, never become zero.
Weekend/holiday gaps are valid adjacency. Without complete calendar evidence,
returns are explicitly consecutive **observed** daily bars with unverified session
spacing; a multi-session gap may affect that statistic. No weekday is silently
called a missing exchange session. The response carries elapsed calendar days,
calendar status/pins, missing-session availability and excluded pair counts.

Duplicates, invalid OHLCV/source/timestamp identities and mixed adjustment
profiles make aggregate price/volume/return statistics unavailable, preserving
issues and valid rows for inspection. No cross-invalid-row return is invented.
Original `DailyBar` validates zero-activity reference-price days without flagging
legitimate zero open/high/low as automatically corrupt. The report exposes
`adjusted`, `unadjusted` or `unknown` from the original source fields. It does not
infer splits, dividends or adjusted closes. Unadjusted jumps can reflect corporate
actions; validity checks alone do not explain their economic cause.

Retrieval-time regressions are reported as warnings, not exchange publication
chronology: backfill/recollection can legitimately produce them. Trading-date and
payload mismatches are invalid. Original physical insertion order is not recorded
in this schema; the query's deterministic date ordering does not prove original
source-order monotonicity. No missing publication timestamp is manufactured.

Staleness compares the symbol's last **key date** with the dataset's last key date;
`lag_calendar_days` is not a verified session count. `requested_end_exceeds_dataset`
is a separate warning. No system-clock timestamp enters the report. A symbol that
ends with the dataset is not claimed fresh relative to today's market.

## Actual local Samsung Electronics inspection

Read-only execution on 2026-10-25 recalculated, rather than hardcoded:

| Metadata | Observed value |
| --- | --- |
| Total stored bar rows | 1,099,255 |
| Distinct symbols | 993 |
| Dataset first / last key dates | 2022-01-03 / 2026-10-01 |
| 005930 rows | 1,160 |
| 005930 first / last key dates | 2022-01-03 / 2026-10-01 |

The 2024-06-24–28 request returned 5 actual local observations, unadjusted KRX
KOSPI/KRW. The retained original June KIND calendar identified all 5 sessions;
selected-window duplicates, invalid rows, retrieval-time regressions and missing
sessions were zero. Selected real result fields:

```json
{
  "selected_range": {"start":"2024-06-24","end":"2024-06-28","rows":5,"valid_rows":5},
  "statistics": {
    "price_change": 900,
    "percentage_change": "1.116625310173697270",
    "average_observed_daily_volume": "14704315.200000000000000000",
    "realized_log_return_volatility": "0.003079093159110504",
    "return_pair_count": 4
  }
}
```

The 2026-09-01–2026-10-01 request returned 21 actual local observations and the
latest available stored 005930 bar (2026-10-01, unadjusted close 276000 KRW).
That range has no supplied independently pinned calendar; missing sessions remain
unavailable and return session spacing is explicitly unverified. These are the
persisted values, not a live quote, a correctness certification or a trading result.

Global symbol/dataset dates/counts are **key metadata only**, including keys across
the protected interval. No protected price payload was read for those aggregates.
`latest_in_range` always refers to the requested range; `latest_available_ohlcv`
is null when the actual last symbol record lies outside it or is invalid. Thus the
June request never secretly loads October prices or calls June the global latest.

Instrument names remain null in this adapter: the existing verified Security
Metadata compiler has no current official security-name authority, and an English
DART corporation name is not automatically a security name. No hardcoded Samsung
name, name translation or ticker-based type inference enters JSON.

`data_origin=local_krx` identifies operator-declared actual local KRX records;
`synthetic` is mandatory for controlled test fixtures. A provider string/file name
or checksum does not independently authenticate provenance. Neither label grants
historical decision-time availability. No strategy optimization or evaluation
partition is used to generate these statistics.

## Query and validation limits

Default 1,250 rows, configurable `--max-rows` from 1 to 2,500. Requests span at most
3,661 inclusive calendar days and cannot cross a protected evaluation interval.
Use smaller explicit windows rather than silently truncated series. Row payloads
are at most 8 KiB; reports at most 4 MiB; DB files at most 2 GiB. SQL work has an
explicit progress-instruction ceiling. Original key indexes support symbol/date
selection and covering-index coverage counts; no million-row Python materialization
or full-database JSON parsing occurs. A nonstandard table without the original
unique index is identified, and selected duplicate dates remain detectable.

Full-file SHA streaming and global key aggregation still cost I/O on a million-row
snapshot; this is not a zero-cost query cache or a whole-dataset quality audit.
Quality assertions apply to the explicitly selected rows. Calendar evidence accepts
up to 24 original month owners through the Python interface; CLI accepts one
bounded original month. The month parser/source hash/pin is reused unchanged.
Calendar evidence is not historical PIT approval or corporate-action completeness.

The installed-wheel smoke uses a small clearly synthetic SQLite database, invokes
all three CLI views, checks statistics, rejects final-test access and compares
source SHA before/after. Unit/integration fixtures also cover duplicates, invalid
bars/timestamps, exact returns/volatility, missing sessions, row/payload limits,
staleness, metadata-only global bounds and deterministic JSON. No real database is
required or loaded by automated tests. The real-data demonstrations above were
separate explicit read-only operator executions.

## Next boundary

These commands prepare an operator to understand available observations and data
quality before forward signal recording. They generate no Buy/Sell/Hold decisions,
strategy parameters, signal history, model inference, custody approvals, orders,
publication, scheduler or network requests. PR #142 is not implemented here.
Existing PIT/OOS, Corporate Actions, frozen holdout and operational gates remain
unchanged; historical price presence is never treated as evidence of availability
to a strategy at a historical decision time.

For bounded retrospective SMA indicator series within an explicit original
training/tuning partition, see [Historical SMA analysis](historical-sma-analysis.md).
This separate mode does not grant forward-signal or historical PIT eligibility.
