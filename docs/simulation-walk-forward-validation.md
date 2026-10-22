# Synthetic OOS / walk-forward validation foundation

`backtest/simulation_validation.py` is a separate, explicit temporal-validation
layer over existing pinned Paper Simulation Performance Comparisons. It never
trains, retunes, selects, promotes or approves a strategy. Existing comparison,
valuation, diagnostic, checkpoint, Ledger, API/CLI and governance contracts stay
unchanged.

## What can actually be established

Chronological separation is necessary to evaluate a frozen candidate on unseen
observations, but does not prove information availability, genuine training
lineage or absence of selection bias. Rolling/expanding evaluation can reveal
variation across different periods; it does not authorize optimizing against
those periods or repeatedly calling them an untouched holdout.

Existing PaperScenario provides explicit market frames and action scripts, but
has no authoritative training membership, original strategy artifact custody,
freeze receipt or official calendar. An existing comparison **alone** therefore
returns `temporal_provenance_unavailable`, with no OOS aggregate. The module does
not infer a freeze timestamp from an experiment name, result, file modification
time or today's clock.

This foundation accepts a **new, explicitly pinned synthetic provenance
attestation** together with the original strategy artifact payload, exact input
membership, declared calendar and source owners. It verifies hashes, temporal
consistency and membership against existing scenario evidence. This establishes
only conditional, evidence-backed **synthetic temporal validation**. It does not
prove that a real model was trained, that an attestation was issued by a trusted
timestamp authority, or that all historical input availability was independently
verified. Every report keeps:

```json
{
  "scope": "synthetic_temporal_validation_only",
  "historical_oos_eligible": false,
  "strict_pit_approved": false,
  "operational_eligible": false
}
```

A fold's `complete_oos` flag means complete coverage in this explicit synthetic
scope only. It is not historical/operational approval.

## Reused contracts and dependency direction

The layer reuses `FrozenJSON`, canonical hashes, existing UTC/reference
validation, exchange/native identities and the existing full
`verify_performance_comparison` path. That path already restores Orchestrator
checkpoints and semantically replays Paper Broker execution and valuation.
Economic P&L and returns are not recalculated here.

Existing `ResearchDateRange` / `ExplicitResearchSplit` use inclusive Seoul
calendar dates, and `models.experiment.purged_fold` consumes historical research
samples with date-based labels. `ResearchCalendarPolicy` excludes known
incomplete dates; it is not an exchange session calendar. Those contracts cannot
be repurposed as aware KR/US session windows. Their semantics are unchanged.
Existing strict-PIT signal inference checks original model availability and
quality evidence; locked research qualification does not grant final-test or
operational access. None of those gates is called or relaxed by this module.

New owners are `TimeWindow`, `ValidationFold`, `WalkForwardPlan` and `FoldSource`.
They stay in the Simulation domain, with no database, provider, HTTP, model
loading or application dependency. No architecture exceptions are added.

## Window and purge/embargo semantics

All `TimeWindow` boundaries are explicitly timezone aware and normalized to UTC.
Windows are half-open `[start, end)`: an observation at start belongs; one at end
does not. A fold contains training, optional validation, and OOS testing windows,
a stable fold ID and a declared expected-case count.

For each consecutive phase pair:

```text
prior_window.end + purge_seconds + embargo_seconds <= next_window.start
```

Both nonnegative durations are elapsed calendar seconds, not inferred trading
sessions. The complete declared sample footprint must stay in its assigned
partition: feature start and anchor, plus label availability, are all inside
that window. It must also satisfy:

```text
feature_start <= features_available_at <= anchor_at <= label_end <= label_available_at
label_available_at + purge_seconds + embargo_seconds <= next_phase.start
```

This is deliberately conservative: overlapping lookbacks are not silently
permitted or repaired. Invalid samples invalidate the fold rather than being
removed to achieve a minimum count. Training/validation sample IDs and source
payload hashes must be unique. Test membership cannot reuse those identities.
Label endpoints must fall in a declared session. Minimum training/validation
samples and OOS cases are mandatory; optional validation requires an empty
validation membership when absent.

Freeze time must be at/after the last training/validation label availability,
at/after declared dataset availability, and satisfy:

```text
frozen_at + embargo_seconds < testing.start
```

Equality at that final boundary is rejected. Every nonempty `used_oos_results`
list is unsupported in this foundation, including same-fold/future OOS feedback.
No implicit retuning, hidden optimization or automatic earlier-fold score use is
implemented. Earlier OOS dates can appear in a later fold's declared training
window, but require new explicit membership and an independently pinned freeze
attestation; this module does not train that version.

## Holdout, rolling and expanding plans

`walk_forward_folds` constructs 1 holdout fold, or 2–20 rolling/expanding folds.
It shifts by supplied calendar seconds. Rolling training windows retain duration
and advance start/end; expanding windows retain the original start and advance
end. Optional validation and testing follow the configured gaps. Step must cover
the testing duration. Manual plans are also independently validated for mode,
training progression, chronological order, unique IDs and disjoint test windows.

No weekend, holiday or DST adjustment is inferred. A UTC duration can change its
local wall-clock hour across DST. The supplied market calendar determines actual
membership. The application must explicitly construct desired boundaries instead
of assuming calendar-second stepping means equal trading sessions.

`synthetic_session_calendar_v1` supplies calendar ID, exchange, timezone, synthetic
origin and ordered sessions with IDs, local dates and aware open/close timestamps.
Its full document is part of the trusted plan pin. The timezone must be
`Asia/Seoul` for KOSPI/KOSDAQ or `America/New_York` for NYSE/NASDAQ. ZoneInfo checks
local dates and DST; no official hours or holiday approvals are manufactured.
Session membership is also `[open, close)`, so an event at close is excluded.

This v1 supports one native exchange/session per evaluated case. All original
scenario observation/availability times, frames, account timestamps and order/
fill/cancel timestamps must fit the declared OOS window and test session. Source
session exchanges must match the plan. Overnight or multi-session cases require
a future explicit membership contract. Source observation availability must be
at/before its frame; an unused future quote cannot pass as PIT-safe evidence.

## Original evidence and synthetic provenance

`FoldSource` contains the original comparison, trusted comparison fingerprint,
original `PerformanceInput` tuple, original comparison policy and pin, and optional
provenance plus its independently retained hash. A plan has a separate trusted
full-document hash. Source comparisons are fully verified even when temporal
provenance is missing. Arbitrary summary JSON cannot replace original owners.

`synthetic_walk_forward_provenance_v1` requires exactly:

| Field | Verified meaning / limitation |
| --- | --- |
| `fold_id`, `dataset`, `dataset_available_at` | Exact fold and existing dataset reference, explicit declared availability |
| `train_samples`, `validation_samples` | Original synthetic payloads (`data_origin: synthetic`), unique IDs, feature/label timestamps and session IDs; not a result summary |
| `test_membership` | Every exact comparison pair ID, evaluation-case hash, sample ID, session ID and full information start/end |
| `strategy_freezes` | One declaration per experiment: complete strategy reference, original artifact payload, freeze time, parameter/dataset/execution/valuation hashes, training membership hash and empty OOS feedback list |

The original strategy artifact must hash to the existing strategy reference's
`content_hash`; labels/strategy names alone are not evidence. Full strategy
reference, parameters and dataset reference are bound to the declaration.
Training membership is sealed over the complete training/validation lists.
The source/provenance hash is independently retained by the caller, not newly
invented by validation to approve replacement content.

Test information bounds must match original scenario facts exactly. Both variants
must have the same complete pair-identity set, and the count must equal the fold's
declared cases. Duplicate underlying evaluation cases are rejected even under
new job names: repeated mechanics are not independent OOS samples. Test windows
are disjoint across folds and evaluation-case membership is not counted twice.

Malformed/missing temporal evidence makes a fold `ineligible` with a stable
reason. Bad source comparison/valuation/economic evidence fails the existing
verifier and raises, producing no trusted validation report. A provenance pin
mismatch is explicitly ineligible rather than silently approved. Hashes cannot
prove real historical custody: these declarations are synthetic evidence only.

## Fold states, coverage and aggregation

- `valid`: temporal checks pass and every expected run has an evaluated return.
- `incomplete`: temporal checks pass, but failed/cancelled/interrupted/unexamined
  jobs or unavailable valuations prevent complete coverage.
- `ineligible`: timing, identity, leakage or compatibility cannot be established.
- `skipped`: no source was supplied; no failure or result is fabricated.

Each fold retains source performance summaries and paired comparisons, outcomes,
exclusions, original hashes, calendar/dataset identities and verified synthetic
freeze references. A partial or unmatched cohort never becomes complete OOS.

Aggregate only eligible compatible folds: same native market/currency, dataset,
execution/valuation policies, strategy/code/parameter identities, initial economic
books, seed and resource policy. Time/source IDs naturally differ across folds;
economic starting balances/positions must match. Incompatible folds are excluded
with `incompatible_fold_aggregation`; they remain in the planned denominator.

Available returns are read from the already verified comparison rows. Exact
Fraction arithmetic and standard-library `mean`, `median`, `pvariance` produce
pooled mean/median/population variance, positive/negative/flat counts, fold means
and their sign/range consistency. No Sharpe, annualization, drawdown, significance,
winner or promotion is calculated.

Per-variant expected cases equal **all planned folds' expected cases**, including
skipped, ineligible and incomplete cases. Missing returns never become zero.
Coverage reports the usable count over that denominator. Summary statistics are
conditional on eligible evaluated returns and can be selection-biased.
`min_folds` counts fully valid folds. Until satisfied, headline aggregate mean,
median and variance remain null with `minimum_valid_folds_not_met`; source fold
observations and coverage remain visible. Incomplete folds can contribute only
independently available cases after the minimum is satisfied; overall status
still remains incomplete.

## Worked reproducible example

The controlled development fixture supplies original owners plus independently
pinned synthetic declarations; it neither fits a model nor fetches market data:

```python
from tests.backtest.test_simulation_validation import fixture, run
from donghak_stock_vision.backtest.simulation_validation import verify_walk_forward_report

plan, sources = fixture("rolling", 3)
report = run(plan, sources)
value = report.to_dict()
assert value["status"] == "complete"
assert value["fold_counts"]["valid"] == 3
assert not value["historical_oos_eligible"]
verify_walk_forward_report(
    report, value["content_fingerprint"], plan, plan.document.identifier,
    sources, max_jobs=30,
)
```

The synthetic KOSPI local windows (Asia/Seoul) are:

| Fold | Train `[start,end)` | Validation `[start,end)` | OOS `[start,end)` |
| --- | --- | --- | --- |
| 000 | Jan 5 09:30–Jan 7 09:30 | Jan 7 09:32–Jan 8 09:32 | Jan 8 09:34–09:44 |
| 001 | Jan 12 09:30–Jan 14 09:30 | Jan 14 09:32–Jan 15 09:32 | Jan 15 09:34–09:44 |
| 002 | Jan 19 09:30–Jan 21 09:30 | Jan 21 09:32–Jan 22 09:32 | Jan 22 09:34–09:44 |

All dates are 2026. Purge and embargo are each 60 seconds. Expanding mode keeps
Jan 5 as every training start. Each fold independently verifies the existing
comparison example: 900 opening cash, one versus three shares bought on a 10
quote, explicit 12 terminal mark and zero fixture costs. Original return ratios
are 1/450 and 1/150; paired right-minus-left is 1/225. Three complete folds give
coverage 3/3, unchanged pooled means and zero population variance. That consistency
is a property of repeated controlled fixtures, not evidence of predictability.
The fixture's weekday calendar is explicitly synthetic, not an official KR/US
holiday calendar. Production callers must independently retain all trust pins.

## Verification limits and next requirements

`simulation_walk_forward_validation_v1` is canonical immutable JSON with a content
fingerprint excluding itself. Source/plan pins retain their original meaning.
`verify_walk_forward_report` repeats original economic comparison verification,
temporal checks, membership/freeze checks and aggregation; rehashing changed
metrics or fold identities cannot bypass it. No wall-clock rendering timestamp
is introduced. Bounds are 20 folds, 256 declared sessions, 1,000 training/
validation records per fold, 300 aggregate simulation jobs, and an 8 MiB result.
Existing replay budgets are cooperative, not process isolation. Sources and
reports are materialized; no large-scale streaming/crash-recovery claim is made.

Before genuine historical OOS evaluation or optimization, independent contracts
must establish real training-data subset custody and feature/label construction,
PIT information availability, actual training/build lineage and timestamped
strategy freezes, official exchange calendars, embargo-aware model selection,
untouched holdout governance and Corporate Actions/OOS approvals. Real-time or
live execution requires additional operational gates. This foundation grants
none of those approvals and performs no strategy optimization or activation.
