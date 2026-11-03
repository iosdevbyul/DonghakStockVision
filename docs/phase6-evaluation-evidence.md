# Phase 6 — Combined immutable evaluation evidence

Generate one read-only JSON report joining the frozen baseline scorecard and per-ticker error attribution for the same evaluation ID:

```bash
PYTHONPATH=src python scripts/phase6_evaluation_evidence.py \
  --analysis-db /path/to/analysis.sqlite3 \
  --evaluation-id EXISTING_EVALUATION_ID
```

The report verifies evaluation, model, and snapshot identities across both sources. All records come from an existing analysis database. No KRX requests, writes, refits, model promotion, or trading take place.

Missing observations remain explicitly unavailable. These are historical research descriptions, not independently validated forward results; later hypotheses require a new untouched evaluation window.

## Evidence completeness checks

The `evidenceCompleteness` section marks missing or invalid frozen test AP, missing baseline AP, un-evaluated directions, and unavailable error attribution as blockers. Both directions must have complete descriptive evidence for `complete=true`. This check never certifies an investment strategy, independent holdout, or model promotion. Incomplete evidence remains visible rather than being treated as success.

The completeness check also verifies that the reported test AP margin equals the difference from the best frozen baseline (within floating-point tolerance) and rejects non-finite validation margins. These checks detect inconsistent metrics; they do not independently validate the underlying outcomes.

## Strict research evidence gate

For unattended offline checks, append `--require-complete` to the command above. This preserves the JSON report on standard output but exits with status `3` when either direction is missing the required frozen descriptive metrics; an ordinary complete report exits `0`. Input errors continue to exit `2`. Without the flag, incomplete reports remain viewable with exit `0` as before.

A passing strict check means **only** that the historical evidence fields are populated and internally consistent. It never qualifies a model for deployment, independent forward validation, or trading. No live provider requests or database writes occur.
