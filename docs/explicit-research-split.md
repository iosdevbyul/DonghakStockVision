# Explicit research split

Phase 2 retains its automatic common-date 60/20/20 split. Omitting the new
configuration produces the original split document and uses the original behavior.
The CLI currently retains automatic splitting; explicit splitting is available
through the public Python service, without changing existing command semantics.

## Public contract

`ResearchDateRange(start: date, end: date)` and
`ExplicitResearchSplit(training, validation, final_test)` live in
`donghak_stock_vision.data.research_split`. Both are frozen dataclasses.
Dates are inclusive Asia/Seoul session dates, not timestamps.
Ranges must be ordered and disjoint; gaps are permitted. Datetimes, reversed
ranges, overlapping endpoints, malformed documents and unknown schema versions
raise `AnalysisError`. Configuration JSON round-trips through
`to_dict()/from_dict()`; `identifier` is its canonical SHA-256.

Future research usage (not executed against actual data in this change):

```python
from datetime import date
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.models.service import TrainingService

configuration = ExplicitResearchSplit(
    ResearchDateRange(date(2022, 1, 3), date(2023, 12, 28)),
    ResearchDateRange(date(2024, 1, 2), date(2024, 6, 28)),
    ResearchDateRange(date(2024, 7, 1), date(2025, 6, 30)),
)
# analysis_store and snapshot_id must be explicitly provided by the caller.
# TrainingService(analysis_store).train(snapshot_id, split_configuration=configuration)
```

The snapshot must encompass all three ranges and be historical_research.
The pure `split_dataset(samples, mode, configuration=None)` function is also public.
Explicit configuration in PIT mode is rejected. Partitions retain existing keys
`train`, `validation`, `test` for compatibility; `test` means Final Test.

Samples are assigned using their timezone-aware anchor converted to Seoul,
which must agree with trading_date. Outside-range samples are counted and excluded.
Unlabeled/context-free samples do not enter labeled partitions.
Input order does not affect explicit partition order.

## Purge and information boundaries

Training label_end must be strictly before validation.start.
Validation label_end must be strictly before final_test.start.
Final Test label_end must be on or before final_test.end. A missing label_end is
purged. Equality at the next partition start is purged; equality at the Final
Test end is allowed. No calendar +5-day approximation is used.

An anchor must be inside its configured range. A label may observe a gap after
that range, provided it ends before the following partition starts: this preserves
the existing next-boundary purge semantics. Feature warm-up can use prior snapshot
rows, never future rows. Final Test tail anchors with immature labels remain
unavailable for label evaluation; this does not manufacture future observations.

The example's final period is 2024-07-01 through 2025-06-30: 365 calendar days.
The eventual KRW 100,000,000 initial cash belongs to the backtest configuration,
not the split contract. A 10% annual target is an evaluation goal, not a fitting
objective or a reason to adjust policies after looking at Final Test.

## Final Test protection and limits

Existing fitting uses training only; C selection and qualification use validation
AP only. Parameters are frozen before test predictions/metrics. No new fitting,
threshold-selection or hyperparameter-selection path receives Final Test samples.
Existing minimum-data checks still inspect test class/sample availability.

Training currently computes test metrics after selection and stores them. This
feature is **not** an access-control or one-time-unseal system. It cannot prevent
a human from reading metrics and repeatedly changing configuration. Final Test
must be reserved for the final evaluation operationally; policies, universe and
model-selection rules must be fixed before inspection. This is a chronological
holdout, not a claim of verified PIT/OOS evidence from retrospectively collected
latest-only data.

## Snapshot and artifact reproduction

The immutable snapshot remains unchanged and can contain all partitions plus
past warm-up rows. A snapshot containing future holdout prices is not permission
to use them for fitting or causal features. Dataset split metadata contains the
versioned configuration, configuration_hash, partitions and purge audit.
Dataset content hash therefore includes exact ranges; the model references
snapshot_id, dataset_id and split_hash as before.

Evaluation rebuilds the dataset from the original snapshot with the saved
configuration. Old datasets without configuration rebuild automatically using
60/20/20. No SQL schema migration is needed. Research inference's exact
snapshot_id requirement is preserved; the mutable latest market DB cannot
replace that evidence.

## Known incomplete sessions: separate quality follow-up

2024-03-28 and 2025-03-21 are known incomplete market dates, not holidays.
This split feature neither removes them from the calendar nor repairs them.
Existing verified-session / excluded-date quality checks remain unchanged.
Unverified observed-bar mode alone does not establish completeness; a five-bar
label can otherwise span a missing real session.

Before the real experiment, resolve source anomalies and explicitly validate or
block feature/label windows intersecting these dates through the quality layer.
Do not silently skip dates, interpolate prices, or call the snapshot PIT verified.
Corporate actions, survivorship and source revision history also remain separate
limitations. No actual DB, training run, strategy run or backtest is changed here.
