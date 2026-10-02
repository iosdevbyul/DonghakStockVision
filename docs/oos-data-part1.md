# OOS data preparation, Part 1

Part 1 collects and validates source market data only. It does not freeze an OOS
period, build features/labels, evaluate models, or run strategies/backtests.

## Fixed collection universe

`KRXMarketPipeline.collect(start, end, tickers=approved_tickers)` optionally limits
cleaned writes to an explicitly supplied, nonempty universe. Omitting `tickers`
preserves whole-market collection and the existing `dsv collect --all` behavior.
The existing ticker-oriented pipeline and CLI are unchanged.

The market pipeline fetches and archives one complete response per weekday,
validates **all** response rows and duplicate keys using the existing provider and
normalizer, and only then filters to the requested universe. Invalid rows outside
the universe still reject the date. Retries and the persistent request budget are
unchanged. The existing SQLite atomic save and provenance rules remain in force.
Raw audit pages necessarily retain the full market response, including tickers not
retained in cleaned storage. A zero retained-row count is not a holiday assertion.

For append-only preparation, record the existing universe, schema and data hashes,
check that the requested range starts strictly after the existing maximum date,
and refuse a run if any requested key already exists. The storage API continues to
support revisions for other callers; this preparation does not invoke overlapping
ranges. Compare original rows (including their stored timestamps) after collection.
Do not delete, correct, or backfill source bars to make checks pass.

## Quality and availability

Use existing DailyBar validation and storage integrity checks. Record failed dates,
empty responses, per-ticker coverage, no-trade bars, and out-of-universe source codes
separately. A missing bar alone does not prove delisting, suspension or a holiday.
Do not map new ticker codes to old ones without evidence and separate approval.

Known incomplete dates 2024-03-28 and 2025-03-21 remain research exclusions, not
exchange holidays. Part 1 does not approve any new exclusions. New collection
failures or unexplained gaps must remain explicit for review before Part 2.

Collected historical data are latest-known source records, not reconstructed PIT
revisions. Data availability checks do not establish OOS model performance.
Credentials come from the existing local environment configuration and must not be
included in reports, errors or committed files. Local source/audit files stay under
Git-ignored `.data/`; no source market data is committed.
