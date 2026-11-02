# Phase 6 — Combined immutable evaluation evidence

Generate one read-only JSON report joining the frozen baseline scorecard and per-ticker error attribution for the same evaluation ID:

```bash
PYTHONPATH=src python scripts/phase6_evaluation_evidence.py \
  --analysis-db /path/to/analysis.sqlite3 \
  --evaluation-id EXISTING_EVALUATION_ID
```

The report verifies evaluation, model, and snapshot identities across both sources. All records come from an existing analysis database. No KRX requests, writes, refits, model promotion, or trading take place.

Missing observations remain explicitly unavailable. These are historical research descriptions, not independently validated forward results; later hypotheses require a new untouched evaluation window.
