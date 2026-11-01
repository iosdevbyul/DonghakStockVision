# Phase 6: preliminary sample coverage gate

Run this **only on a frozen local copy** after the read-only inventory:

```bash
python scripts/phase6_evaluation_readiness.py --db /path/to/frozen-market-copy.sqlite3
```

The script checks whether at least five KRX/unadjusted tickers each have 504 distinct observed dates and whether their union spans at least 504 observed dates. It reports exact blockers and the source SHA-256. These thresholds are preliminary and **must not** be interpreted as passing the research training contract: the learning engine still needs direction-specific train/validation/test minima, a verified quality manifest, provenance, sessions, corporate action review, and PIT evidence.

No network, KRX API call, trading action, data modification, model training, or model promotion is performed. A successful script run never sets `readyForModelEvaluation` to true. This gate intentionally separates observed raw coverage from fully eligible supervised labels.

The next operator step is running the script against an existing approved local database copy, preserving the output and verifying quality before baseline learning.
