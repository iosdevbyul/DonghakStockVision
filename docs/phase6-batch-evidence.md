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
