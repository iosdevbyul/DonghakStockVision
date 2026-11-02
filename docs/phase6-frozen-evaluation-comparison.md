# Phase 6 — Frozen evaluation comparison

Compare two existing immutable historical-research evaluation artifacts produced for the **same snapshot**. This is a read-only, descriptive audit, not a new holdout evaluation.

```bash
PYTHONPATH=src python scripts/phase6_compare_evaluations.py \
  --analysis-db /path/to/analysis.sqlite3 \
  --reference-id EXISTING_REFERENCE_EVALUATION_ID \
  --candidate-id EXISTING_CANDIDATE_EVALUATION_ID
```

The command refuses identical evaluation IDs, different snapshot IDs, or different usage restrictions. For each direction it reports frozen test average-precision values and their difference only when both stored entries were evaluated. Missing results are marked `not_comparable`.

**Important:** Repeatedly comparing candidates on a shared test period risks overfitting. Even a positive delta is not evidence of forward accuracy or authorization for model registration, paper deployment, or live trading. Before adopting a model revision, predeclare a new evaluation and use an untouched later period. No market API, brokerage API, or network request is used.
