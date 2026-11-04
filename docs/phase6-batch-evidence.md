# Phase 6 — Batch research evidence audit

Audit **explicitly selected** historical evaluation artifacts from an existing local analysis database. The script does not contact a provider, train, update the database, or place trades.

```bash
PYTHONPATH=src python scripts/phase6_batch_evidence.py \
  --analysis-db /path/to/analysis.sqlite3 \
  --evaluation-id EXISTING_EVALUATION_ID_A \
  --evaluation-id EXISTING_EVALUATION_ID_B \
  --require-complete
```

The JSON report preserves the requested evaluation order and includes the existing individual evidence reports, each one's blockers, and the complete/incomplete totals. Without `--require-complete`, incomplete evidence is reported with exit code 0; with the option, incomplete evidence returns 3 while retaining JSON output. Invalid inputs or missing artifacts return 2. Duplicate evaluation IDs are rejected.

Different snapshots can appear in one inventory, but their scores are **not** compared or ranked. The audit is a descriptive integrity check, not independent forward validation or approval to promote models or trade.

## Aggregated blockers

`blockerCounts` records how many selected evaluation reports contain each descriptive completeness blocker, with keys sorted for reproducible JSON output. `evaluationsWithBlockers` counts evaluations with at least one blocker; one evaluation may contribute more than one blocker. The counts do not estimate predictive accuracy or establish a causal explanation.

## Evaluation traceability

`blockerEvaluationIds` maps each blocker code to the selected evaluation IDs that contain it, preserving request order. `incompleteEvaluationIds` provides a direct review queue of evaluations with at least one blocker, also in request order. These are identifiers for offline investigation, not a ranking of model quality or an authorization to use the models.

## Descriptive review priority

`reviewQueue` identifies incomplete evaluations and orders them for manual evidence review. Metric integrity blockers ending in `_invalid` or `_inconsistent` are assigned `integrity` priority ahead of `missing_evidence` blockers. Evaluations with equal priority preserve input order. Each entry retains the evaluation and model IDs with its full blocker list. Priority describes evidence investigation order only; it is **not** a model performance ranking, prediction confidence, or authorization to trade.
