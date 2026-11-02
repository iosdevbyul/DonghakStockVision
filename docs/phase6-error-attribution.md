# Phase 6 — Frozen error attribution

After producing a historical-research evaluation artifact, inspect the test-set confusion matrix and which tickers contributed false positives and false negatives.

```bash
PYTHONPATH=src python scripts/phase6_error_attribution.py \
  --analysis-db /path/to/analysis.sqlite3 \
  --evaluation-id EXISTING_EVALUATION_ID
```

The script opens the existing analysis SQLite database read-only, uses stored immutable evaluation metrics, orders tickers by recorded mistakes, and rejects ticker counts that cannot be reconciled with the aggregate test confusion matrix. An unavailable direction is reported as unavailable rather than estimated. No data collection, model retraining, model promotion, or trading is performed.

False positives and false negatives are classifications under the frozen threshold, **not evidence of why an error occurred**. Repeated investigation of the same test set can bias future model selection; confirm any new hypothesis in an independently held-out later period. No real-market test scores were computed in this GitHub-only development environment.
