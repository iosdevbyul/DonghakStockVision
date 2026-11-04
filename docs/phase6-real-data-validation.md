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

**Local inspection, 2026-11-05:** the operator's historical database contains
1,099,255 rows across 993 tickers, from 2022-01-03 through 2026-10-01. These are
read-only SQL counts, not a completed source/PIT/calendar/corporate-action audit.
The existing local analysis database contains snapshots, datasets, a model and a
training failure, but no stored signal or evaluation artifacts. No new training,
inference, provider collection, approval or real prediction accuracy measurement
was performed during this inspection. Private databases and fingerprints are not
included in the repository; CI continues to use synthetic fixtures.

## Evaluation input integrity

All existing linear/HGB evaluation callers reuse `models.metrics.evaluate_metrics`.
It requires aligned binary integer labels and finite numeric scores in [0, 1].
Booleans, fractional labels, unavailable labels, NaN/infinite scores and values
outside the score range raise `ValueError`. No coercion, clipping or substitution
of missing outcomes is performed. `report` also requires one explicitly labeled
sample per score, preserving per-ticker alignment. These are structural checks;
callers must still verify source evidence, horizon maturity and chronological
separation before evaluating anything.

Valid inputs retain the original confusion matrix, precision, recall, average
precision, Brier score, ten calibration bins and undefined-value conventions.
Scores remain uncalibrated conditional research scores. A Brier score or populated
calibration bin does not establish calibrated probabilities, future accuracy or
investment performance.

AP groups tied scores together and now computes the total positive population
once, rather than summing all labels for every distinct score. This removes the
quadratic term: score grouping is linear and sorting distinct scores is
O(n log n). Output values and floating-point operation order within AP are
preserved. The full report still has other per-ticker/date aggregation costs.

A bounded local Python 3.12 benchmark used `random.Random(42)`, binary labels and
uniform synthetic scores; three repetitions per input size, no network or market
files. Complete metric dictionaries matched the previous implementation exactly.

| Cases | Before seconds (three runs) | After seconds (three runs) | Median reduction |
| --- | --- | --- | --- |
| 1,000 | 0.010188, 0.009992, 0.010263 | 0.004152, 0.003892, 0.004608 | 59.2% |
| 10,000 | 0.644401, 0.603999, 0.615136 | 0.036476, 0.037698, 0.040592 | 93.9% |
| 30,000 | 9.018252, 5.361790, 5.325709 | 0.129169, 0.136225, 0.139882 | 97.5% |

Timings are machine-specific and noisy, especially the first 30,000-case baseline.
They measure only the metric function, not end-to-end training or model quality.

## Next dependencies

1. Complete the original-source, session and corporate-action assessment before
   authorizing any real-data experiment; local retrieval times cannot establish
   historical publication or PIT eligibility.
2. Connect an eligible prospective inference record to later, separately pinned
   outcome observations, retaining creation time, horizon, model/feature identity,
   immature/unavailable cases and independent replay. Existing local content hashes
   alone cannot establish independently authenticated contemporaneous prediction.
3. Predeclare later evaluation cohorts and baselines, quantify coverage and
   uncertainty, and compare errors without feeding final-holdout results into tuning.

Existing Phase 6 descriptive evidence gates do not implement these forward
guarantees. No training or operational eligibility is granted by this change.
