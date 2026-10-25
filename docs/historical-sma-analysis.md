# Retrospective historical SMA analysis

## Purpose and implementation boundary

Base main `c89a2ca4f2738c96f5b0aa09a54ddf6cf4e81bd6` contains merged PR #142.
`dsv market-data sma-analysis` reads existing Korean daily OHLCV and returns a
bounded `historical_sma_analysis_v1` JSON projection with analysis mode
`RETROSPECTIVE_HISTORICAL_ANALYSIS`.

This is a usable retrospective indicator series, not authenticated historical
trading decisions, a profitability backtest or an order-generation interface.
There is no journal, artifact store, audit record, API publication or automatic
provider request. The operator may explicitly redirect stdout to a private file.

Repository inspection established this minimal integration:

* Reuse PR #141 `analyze_market_data(view="history")`: original bars schema and
  primary-key index, bounded read-only query, source SHA-256 checks, `DailyBar`
  validation, original split guards and optional original KIND calendar owners.
* Reuse PR #142's **unchanged** `forward_reference.calculate`, parameter validator
  and code fingerprint. Its prospective capture eligibility and journal interfaces
  are not called or relaxed. The retained strategy ID names the shared mathematics;
  the distinct analysis mode/schema identifies this result as retrospective.
* Reuse Trading Core's exchange-qualified `Instrument` and KRW currency validation.
  KOSPI/KOSDAQ are supported; the initial operator demonstration is `005930`.
* Do not instantiate writable `SQLiteStore`, `AnalysisStore` or `DecisionStore`.
  Their audit writes and existing model/account-based decision semantics are not
  appropriate for this read-only projection. No new storage abstraction is needed.

## Explicit source and research scope

Required inputs are `--db`, `--db-sha256`, `--symbol`, `--start`, `--end`, `--fast`,
`--slow`, `--split`, `--split-pin`, `--partition` and `--data-origin`. Use the
subcommand's explicit `--db`; the legacy root flag is not a source default.
Output is always canonical JSON; no additional JSON flag is necessary.

The original split JSON (or existing summary containing its `split` object) and
independently retained pin are mandatory. `--partition` is `training` or `tuning`
(the latter maps to original `split.validation`). The **entire inclusive selected
range** must fit that one partition. Crossing partitions, OOS/final-test/holdout,
invalid parameters and incorrect split pins are rejected before market/calendar
payload I/O. Dates later than final-test are not implicitly authorized by this
command, even where ordinary descriptive analytics can inventory them.

Retain the original reviewed split/pin. Inventing a replacement split does not
authorize reading protected data. Global coverage inside the reused adapter reads
keys/counts only; OHLCV payload reads are limited to the selected authorized range.
There is no implicit warm-up query before `--start` or outside the declared scope.

A fixed, quiescent snapshot is opened with `mode=ro&immutable=1` and `query_only`.
Original streamed source SHA checks before/after the query, active-journal refusal,
SQL work limits and source/output byte limits are preserved. The primary key
`(ticker,trading_date)` provides the indexed bounded selection. No million-row
Python materialization, schema migration, interpolation or database repair occurs.
Full-file hashing and key coverage aggregation still incur bounded I/O.

`--data-origin local_krx` identifies operator-declared local KRX records;
`--data-origin synthetic` labels controlled fixtures. Neither flag authenticates a
publisher or historical availability. A missing/changed database is an error,
never a reason to substitute synthetic prices. Source prices must be uniformly
**unadjusted**; unknown, adjusted or mixed profiles are refused in this version.

## Formula, exactness and warm-up

Parameters follow the unchanged reference contract: integer
`1 <= fast < slow <= 30`. The selected observation count defaults to at most 1,250,
with `--max-rows` capped at 2,500. A reference invocation sees at most `slow+1`
closes (31 at the largest supported period), preserving its original 32-close
input bound while allowing a longer bounded retrospective series.

For each observation `t`, calculate only from its current prefix. The current SMA
of length `n` is the sum of the last `n` observed closes divided by `n`; previous
means end at the preceding observation. Crossing comparisons use exact integer
cross-products, not rounded displayed means:

* BUY: previous fast <= previous slow and current fast > current slow.
* SELL: previous fast >= previous slow and current fast < current slow.
* HOLD: neither crossing occurs, including equal current means.
* INSUFFICIENT_DATA: fewer than `slow+1` selected eligible observations.

Warm-up policy is explicit and strict: until a complete previous/current crossover
window exists, **all indicator fields are null**, `warm_up_only` is true, and the
signal is INSUFFICIENT_DATA. Partial SMAs are not introduced. With slow=20, the
first 20 rows are warm-up and the 21st is the first calculable crossover. Extend
an explicitly permitted input window to supply warm-up; no earlier hidden reads
are performed. Empty ranges return an empty series; an absent symbol is an error.

Indicators preserve exact sums/counts and Decimal mean strings at precision 50,
18 fractional places, half-even rounding. This is deterministic for fixed inputs
and parameters. No current clock value is inserted into the report.

## Source quality and limitations

Invalid/missing/nonpositive OHLCV prices and duplicate dates reject the selected
analysis rather than silently drop observations or bridge corrupted rows. Verified
calendar non-session observations also reject it. Missing expected sessions remain
warnings with their exact dates; no price is synthesized. SMA windows count actual
**observed bars**, even across gaps. Without complete trusted calendar evidence,
consecutive-session coverage remains unavailable, not guessed from weekdays.

Each output row includes date, exact close, current fast/slow sum/count/mean,
previous/current crossover evidence, signal/reason, warm-up status, origin,
original OHLCV fingerprint, stored retrieval time, strategy ID and version.
The result also carries snapshot SHA, original split/partition, bounded counts,
quality warnings, policy, code hash and explicit limitations.

Stored retrieval time is not exchange publication or historical decision-time
availability. `historical_decision_at` and `publication_at` remain null;
`historical_pit_qualified` and `oos_approved` remain false. Present-day calculation
of a 2024 crossing does not establish a 2024 trading decision. Unadjusted prices
may contain corporate-action discontinuities. No corporate-action approval,
account valuation, execution costs, position sizing, P&L or profitability is inferred.

## Actual local Samsung Electronics example

The reviewed local DB was available for the operator demonstration. Use the actual
snapshot hash below only after independently confirming the file still matches:

```sh
db=.data/market.sqlite3
split=.data/phase2-training-20261001/summary.json
shasum -a 256 "$db"
dsv market-data sma-analysis \
  --db "$db" \
  --db-sha256 2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42 \
  --symbol 005930 --market KOSPI --start 2024-01-02 --end 2024-06-28 \
  --fast 5 --slow 20 --partition tuning \
  --split "$split" \
  --split-pin 6d4fa516227f13640cdb785f029359134d51b074468be543654c8873f9296150 \
  --data-origin local_krx > /tmp/005930-historical-sma.json
python - <<'PY'
import json
with open('/tmp/005930-historical-sma.json') as stream:
    report = json.load(stream)
print(report['analysis_mode'])
print(report['observation_count'], report['warm_up_count'], report['signal_counts'])
for row in report['observations']:
    if row['signal'] in ('BUY', 'SELL'):
        print(row['trading_date'], row['close'], row['signal'],
              row['fast_sma']['mean'], row['slow_sma']['mean'])
PY
```

Observed real local result: **121 observations**, 20 warm-up rows, 4 BUY,
3 SELL and 94 HOLD rows. The first stored selected close is 79,600 KRW on
2024-01-02; the last is 81,500 KRW on 2024-06-28. Examples actually calculated:

| Date | Stored close (KRW) | Fast SMA | Slow SMA | Retrospective signal |
|---|---:|---:|---:|---|
| 2024-02-02 | 75,200 | 74,040 | 73,850 | BUY |
| 2024-02-16 | 72,800 | 73,820 | 74,060 | SELL |
| 2024-06-28 | 81,500 | 81,160 | 78,565 | HOLD |

This full January–June example supplied no complete calendar owners; its warning
is `trusted_calendar_not_supplied`. Missing-session coverage is therefore unknown.
The stored retrieval timestamps are in September 2026, not proven 2024 availability.
These figures are computed from the local pinned DB, not CI synthetic fixtures.

A second actually executed example verifies the original June KIND month:

```sh
dsv market-data sma-analysis \
  --db "$db" \
  --db-sha256 2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42 \
  --symbol 005930 --market KOSPI --start 2024-06-03 --end 2024-06-28 \
  --fast 2 --slow 5 --partition tuning --split "$split" \
  --split-pin 6d4fa516227f13640cdb785f029359134d51b074468be543654c8873f9296150 \
  --data-origin local_krx \
  --calendar-source docs/evidence/historical-market-readiness.json \
  --calendar-raw docs/evidence/baseline-official/raw/calendar-2024-06.html.gz \
  --calendar-pin fa247837b50e45617995a99aa58cdea20697a73f470b1a79a7cc2eb7eea4c2cb
```

Observed: 19 rows, 5 warm-up, 2 BUY, 2 SELL and 10 HOLD; verified original calendar,
no missing/off-session dates and no quality warnings. First close 75,700 KRW on
2024-06-03; last close 81,500 KRW on 2024-06-28. Calendar verification does not
establish exchange publication, PIT eligibility or strategy profitability.

## Synthetic tests and the next boundary

Standard CI uses isolated synthetic SQLite fixtures only. For example closes
100,110,99,121 with fast=1/slow=2 give two warm-up rows, SELL (99 vs slow 104.5),
then BUY (121 vs slow 110). Constant closes produce HOLD. Tests cover prefix
invariance, strict partition guards, invalid/duplicate records, hashes, bounds,
read-only operation and forbidden journal/audit/network/order side effects.
No private real dataset is required to run the test suite.

Prospective PR #142 signals require original capture/receipt eligibility and retain
an independent journal. This command cannot write to that journal, grant provider
authorization or supply its warm-up data. Separately approved prospective capture
and verified forward-signal integration with Risk Engine/Paper Broker belong to
PR #144. Neither paper nor real orders are executed here.
