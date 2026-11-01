# Phase 6 baseline evaluation scorecard

The scorecard reads a pre-existing immutable `evaluation` artifact from an existing analysis database, without network access or changing records.

After approved data review and a completed research-only `dsv train` / `dsv evaluate` workflow, retain the exact `evaluation_id` from the evaluate JSON output and run:

```bash
PYTHONPATH=src python scripts/phase6_baseline_scorecard.py \
  --analysis-db .data/analysis.sqlite3 \
  --evaluation-id YOUR_EXISTING_EVALUATION_ID
```

The scorecard reports frozen validation and test average precision plus test margins against stored baselines for both reversal directions. It preserves the model and snapshot identifiers, limitations, and research-only status. Missing test scores appear as unavailable, not fabricated.

A positive test margin is descriptive, *not* permission to select parameters on the test partition, promote a model, or trade. Before attempting independent improvements, pre-register changes and assess them on a later untouched forward window.

No real-data scorecard has been generated yet: the GitHub environment does not contain a user-authorized analysis database.
