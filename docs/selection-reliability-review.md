# Frozen-study selection reliability review

This consumer diagnoses the completed historical studies without repeating their
72 experiments or fitting a new model. The six independently pinned final reports
must reconstruct exactly from their original study owners. PR #246's read-only
study API and its `UNAVAILABLE` publication states remain unchanged.

The review reuses `stock_research`, Experiment Memory terminal records,
`research_v2`, the existing candidate predictors, `AnalysisStore`, operational
capture verification, and the existing guarded worker. It creates only a separate
private review database and retained references. Source databases, study reports,
checkpoints, Final Test, models, operating policies and scheduling are read-only.

## Registration and selection

A `selection_review_request_v1` contains exactly six `studies` (two cohorts,
Adaptive/Predeclared/Robust), each with root, separate plan PIN file, database
SHA-256, original report and report SHA-256. `assessment` contains the original
research/protocol references, existing operational source references and database
PIN, context window, evaluation window, and cutoff. Paths and PIN values belong
in private files, never in Git or API responses.

Registration verifies original ownership, completed budgets and fixed held-out
stocks. It freezes implementation/runtime identity, source hashes, candidate IDs,
training priors and the complete `selection_stability_v1` policy before additional
assessment labels are prepared. The selector receives only seen-stock CV evidence;
held-out-stock results and deferred 2026-Q3 diagnostics are excluded.

Each horizon and stock policy compares the original robust group choice, highest
search AP candidate and a stability choice. The stability ranking uses worst
baseline AP gain, worst BA, calibration loss, BA dispersion and train/CV gap.
Acceptance requires four folds, 40 dates per fold, 20 nonoverlapping dates,
train gap <= .10, BA dispersion <= .05, mean BA >= .50, median baseline AP gain
>= .005, worst gain >= -.02, Brier gap <= 0, leave-one-period-out agreement >= .75,
and mean AP margin >= .005. Failure is an explicit **ABSTAIN**, not a successful
model selection. Its frozen training-prior fallback is evaluated on the same
population; zero predictive coverage is reported separately.

These are newly preregistered research criteria, not previously validated universal
thresholds. They must not be tuned using the additional readout. The original
2026-Q3 diagnostic may be retained afterward as a separate appendix; it cannot
enter selection, threshold adjustment or training.

## Commands and recovery

Use the existing CLI with private absolute paths:

```sh
dsv autonomous-research selection-register \
  --request "$REQUEST" --request-pin-file "$REQUEST_PIN_FILE" \
  --root "$REVIEW_ROOT" --pins-root "$REVIEW_PINS"
dsv autonomous-research selection-prepare \
  --root "$REVIEW_ROOT" --plan-pin-file "$REVIEW_PINS/plan.pin"
dsv autonomous-research selection-assess \
  --root "$REVIEW_ROOT" --plan-pin-file "$REVIEW_PINS/plan.pin"
```

Registration requires new output directories. Preparation verifies original receipt
and response lineage through existing owners and retains a separately pinned
readout. Context prices supply feature history only: `label_start` prevents
creating context/Q3 labels. Missing sessions are not interpolated; incomplete
horizons and neutral labels retain the existing exclusions. Sources must be
quiescent with unchanged database hashes on both sides of reads; these separate
local reads are not a cross-database atomic snapshot.

Assessment runs only when existing power, memory, disk, thermal, shared lock and
Ollama resident-resource guards permit it. It uses one native thread, the existing
worker timeout/RSS limits and network audit, without stopping other jobs. Input
preparation does no model inference and may proceed separately under host guards.
`BLOCKED` preserves existing work; do not busy-retry or unload another process.

The local checkpoint reader accepts only the original bounded regular file with
matching SHA-256, exact library versions, protocol 5, and an explicit constructor
allowlist. It rejects arbitrary globals, persistent IDs, extensions, extra bytes,
wrong feature/class layouts and unsafe resource settings. This is not a general
pickle import API and does not authorize loading client-supplied artifacts.

Completed output is committed atomically to the review database, retained as
`assessment.json` and independently referenced by `assessment.pin`. Re-execution
validates/reuses it without inference or fitting. Missing retained output can be
recovered from the committed record; conflicting or tampered output fails closed.
A killed worker before commit leaves no apparently complete assessment.

## Interpretation

Additional assessment compares unchanged models on identical post-Q3 target rows
and the same four held-out stocks per cohort. Pooled models explicitly report that
these stocks were seen at fit. Horizon 1/5/20 results remain separate. Calendar
anchors are selected globally across cohorts using the maximum label end, so many
stocks and overlapping 20-session labels are not treated as independent trials.

Paired Brier improvements use a seeded 1,000-replicate moving-block bootstrap with
familywise 95% limits across 18 comparisons. A local gain requires at least 20
nonoverlapping calendar anchors, positive gains in both cohorts and BA
noninferiority >= -.01. Short windows report `IMPROVEMENT_NOT_ESTABLISHED`, even
when a point estimate improves. Calibration, period/regime dispersion and input
shift are diagnostic evidence, not causal proof. Family comparisons from the old
search are descriptive unless masks, cohorts and budgets actually match.

These records are retrospective, selection-disjoint readouts. The models and
source capture were created after historical assessment dates. Neither source
hash integrity nor a local registration PIN authenticates historical availability,
PIT status, future performance or external chronology. AP/BA are conditional on
the existing label eligibility population; scores are not calibrated stock-rise
probabilities. Final Test stays sealed. PIT, performance approval, champion
promotion, trading and unattended activation remain false or disabled.

## Recorded local study (2026-11-17 KST)

The completed run verified six original reports and 72 completed experiments,
prepared 2,697 source rows for two cohorts, and assessed 36 fixed comparison cases
without a new fit or provider request. Readout dates were 2026-10-06 through
2026-11-13, with 2026-08-01 through 2026-10-01 as feature-only context. Full private
records retain source ownership, per-stock/period/regime metrics, calibration bins,
configuration differences and input shifts; those records and PINs are not published.

| Horizon | Seen-stock CV AP range | Additional held-stock rows per case | Global nonoverlap anchors | Original-model additional AP range |
| --- | --- | --- | --- | --- |
| 1 | .4621–.5165 | 91 or 101 | 9 | .3913–.6390 |
| 5 | .5066–.5464 | 83 or 84 | 4 | .1402–.3410 |
| 20 | .4077–.5171 | 28 | 1 | Undefined: single class |

The new gate abstained in 34 of 36 cases and selected in two. Among its proposed
candidates, calibration rejected 26, the AP margin rejected 17, the train/CV gap
rejected 16 and nonoverlapping dates rejected 12 (reasons overlap). Original group
choices differed from maximum search AP in 12 of 36 cases. The 20-session window
cannot provide useful independent evidence from large overlapping row counts.

All **18 comparisons report IMPROVEMENT_NOT_ESTABLISHED**. Training-prior fallback
Brier gains do not demonstrate an improved predictive model, and some BA deltas
were negative. Descriptive family records comprise 16 linear, 48 random-forest and
eight histogram-boosting configurations; they are not a fully matched factorial
complexity experiment. Wide period and regime variation supports an instability
diagnosis, not a proven causal explanation. A new preregistration and genuinely
separate, sufficiently long readout are required for any further policy test.

The inference worker completed in 18.34 seconds with maximum RSS 156,504 KiB.
No original experiment, Final Test, model or operational setting was changed.
