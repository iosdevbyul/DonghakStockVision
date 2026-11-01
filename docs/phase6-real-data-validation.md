# Phase 6 — Real-data evaluation, Gate 1

This phase measures whether market-signal research improves out-of-sample performance. It does **not** activate production trading, scrape unapproved sources, or authorize extra KRX calls.

## Gate 1: inventory data already collected

Operate on a frozen copy of an existing Phase 1 market SQLite database. Do not provide API credentials or connect to the network.

```bash
python scripts/phase6_real_data_inventory.py --db /path/to/frozen-market-copy.sqlite3
```

The report includes source/provider and adjustment distribution, date coverage, ticker count, ingestion run statuses, raw page count, missing bar provenance, and an SHA-256 fingerprint. It explicitly returns `readyForModelEvaluation=false`: an inventory by itself cannot approve training.

## Gate 2: validate before training

Run `dsv --db /path/to/frozen-market-copy.sqlite3 validate` after verifying the copy is stable, then assess eligible dates and tickers, corporate actions, suspension/holidays, revision availability, genuine collection timestamps, and the approved quality manifest. KRX data and Fake fixture data must remain separate. Failed evidence means stop; never fabricate missing real observations.

## Gate 3: baseline research and iteration

Only after Gate 2, freeze a new immutable training snapshot and predeclare the existing `reversal_barrier_v1` hypothesis and time-based split. Run existing `train` and `evaluate` commands in historical-research mode with disclosure of retrospective limitations. Save version, cutoff, manifest, sample counts, baseline metrics and untouched final holdout. Compare with prior/predeclared baselines; attribute failures to coverage, data quality, market regimes or model errors. Candidate revisions require a new experiment/version and a later untouched or forward period. Do not repeatedly tune to the same final test.

For forward evaluation, preserve every prediction before the market outcome is known and score only after its horizon matures. No PAPER deployment or model promotion follows automatically from a successful research evaluation.

**Current status:** inventory script prepared. No real local database was available to this GitHub-only workflow, so neither data coverage nor actual model accuracy has been measured.
