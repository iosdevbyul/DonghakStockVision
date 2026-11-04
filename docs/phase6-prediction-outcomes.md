# Frozen validation predictions versus observed outcomes

This executable, read-only workflow connects each eligible validation case to its
original model score, binary observed event and correctness result. It independently
reconstructs features and labels from the frozen price snapshot using the existing
`EvaluationService`, then checks the retained per-case score vector. It does not
accept caller-supplied summary metrics or recompute outcomes from today's market DB.

## Operator command

Use a previously frozen **historical research** evaluation and separately retained
original model/snapshot content IDs. Install the existing learning extra for
training fixtures; this replay does not fit a model. From the repository root:

```bash
PYTHONPATH=src python scripts/phase6_prediction_outcomes.py \
  --analysis-db /path/to/frozen-analysis.sqlite3 \
  --evaluation-id ORIGINAL_EVALUATION_ID \
  --model-version ORIGINAL_MODEL_VERSION \
  --snapshot-id ORIGINAL_SNAPSHOT_ID \
  --direction up --offset 0 --limit 100 --max-source-bars 100000
```

All IDs must be original lowercase 64-character content IDs. Obtain them from the
original experiment records, not a newly received report's claims. Missing files,
wrong owners, malformed inputs, corruption or semantic replay mismatches exit 2.
Success writes versioned JSON to stdout and exits 0; it does not save an evaluation,
signal, decision or execution record. No network, refit or broker is invoked.

`EvaluationService.evaluate(..., persist=False)` preserves the exact legacy
evaluation ID and result while omitting the artifact/audit write. Its default
`persist=True` behavior is unchanged. This is also usable with the existing
`ReadOnlyAnalysisStore` for independent verification.

## What is verified

The original evaluation must be historical, unregistered and research-only. Its
model and snapshot references must match the independently supplied originals.
The normal artifact reader checks content hashes. Existing model validation,
dataset reconstruction and chronological split reconstruction remain authoritative.
The full reconstructed evaluation must match the original evaluation ID, and each
validation score must match the retained original vector. Rehashing altered labels,
aggregate metrics or score vectors alone cannot substitute for original replay.

The existing evaluation verifier reproduces both original validation and frozen
test aggregate metrics as an integrity check. Only **validation cases** are exposed;
there is no test-partition selector or new strategy evaluation against holdouts.
Original explicit validation ranges are respected, including gaps before final
test. Automatic splits use their original half-open validation/test boundaries.
The original split, prices, weights, label rules and artifacts are never rewritten.

These checks establish consistency with original local owners. They do not prove
provider publication times, authenticated training custody, calibration, OOS
approval or profitable forward performance. Existing PIT/OOS/holdout and operational
gates are unchanged. The adapter supports the original linear evaluation contract;
it does not load a new HGB estimator or invent a compatible HGB evaluation.

## Response and coverage

`phase6_prediction_outcomes_v1` includes original evaluation/model/snapshot/dataset
IDs, origin, usage restriction, direction, event horizon, session basis and anchor
policy. `cases` are ordered by UTC anchor instant, then ticker. Each contains the
original sample/input/feature hashes, date, score, thresholded prediction, actual
binary event and correctness. Dates remain dates; all returned instants use UTC.

This is `retrospective_frozen_validation_replay`. A historical nominal anchor is
not a prediction's actual creation timestamp: `predictionRecordedAt` is always null.
`locallyRecordedLabelAvailableAt` retains the original local label-availability
field; it is not an authenticated exchange publication time. `forwardValidated`,
`pitQualified` and `executionAuthorized` remain false.

Every feature-valid sample in the original validation window remains in
`totalCases`, including incompatible contexts, unavailable outcomes and purged
boundary cases. Pre-feature exclusions are **not** prediction cases and are not in
this denominator. `statusCounts` distinguishes `evaluated`, `context_mismatch`,
`unavailable:<original reason>`, `purged_at_split_boundary` and `model_unavailable`.
Purged/context-incompatible cases expose neither scores nor outcomes; unavailable
models can retain known labels but never fabricated zero scores. Ambiguous or
immature labels remain null. There is no missing-return/label substitution.

`metrics` contains the verified direction's original validation metrics, excluding
large per-ticker/date breakdowns. Its `n` equals `evaluatedCases`, rather than the
larger context/coverage denominator. An unavailable model returns null metrics.
The response's content fingerprint binds its owners, page, coverage and contents.
It is a deterministic integrity identifier, not independently authenticated custody.

## Bounds and cost

Pages contain 1–250 cases (default 100). `nextOffset` is null at the end; coverage
and metric totals always describe the full cohort, not only the page. Offset past
the cohort returns an empty page. Full semantic replay is deliberately repeated
per request, so pagination is not a cached or bounded-memory replay interface.

The default source budget is 100,000 bars, explicitly adjustable up to 2,000,000.
It is checked before feature/label reconstruction, but after snapshot JSON decode.
Other artifact JSON and the existing full evaluator also materialize data. These
are cooperative bounds, not hard memory/time isolation. No large-dataset throughput
or crash-recovery claim is made. Use stable offline analysis artifacts.

## Observed synthetic example

The existing two-ticker, 240-session `tests.learning.helpers` fixture was executed
through actual snapshot capture, `TrainingService`, frozen `EvaluationService` and
the new read-only replay. Its up-direction validation result was:

```json
{
  "schemaVersion": "phase6_prediction_outcomes_v1",
  "dataOrigin": "synthetic",
  "mode": "retrospective_frozen_validation_replay",
  "totalCases": 92,
  "evaluatedCases": 49,
  "statusCounts": {
    "context_mismatch": 38,
    "evaluated": 49,
    "purged_at_split_boundary": 5
  }
}
```

For synthetic ticker `000001` on 2020-07-28 the replayed score was approximately
0.513377, predicted label 1 and actual label 1. The outcome window was 2020-07-29
through 2020-08-04; its retained local availability was 2024-01-31 UTC. This timing
demonstrates retrospective reconstruction, **not** a recorded 2020 prediction.
Numerical scores may vary slightly with estimator/library versions. Counts here
describe the executed fixture, not real securities or measured investment accuracy.

Reproduce the offline end-to-end and adversarial scenarios:

```bash
pytest -q tests/learning/test_prediction_outcomes.py
```

No real-data evaluation was manufactured. The reviewed private analysis DB had no
stored evaluation artifacts; this command requires an original evaluation owner.
A future prospective workflow still needs an eligible input cohort, predictions
recorded before outcomes, separately pinned later observations and maturity-aware
matching. Those guarantees cannot be inferred from this historical replay.
