# Track C: operator runtime smoke test

This gate is **not** an offline CI pass and does not authorize KRX collection, scheduler changes, broker operations, or production publication. Run only against an existing **loopback** DonghakStockVision API serving an approved immutable offline snapshot. No API keys are needed.

## Before testing

1. Select a reviewed local `.snapshot.sqlite3` *copy*, not a writable source or production database. Record its independently approved SHA-256 digest and an existing six-digit ticker.
2. In the approved operator environment, run `dsv-operational-verify --snapshot <approved-copy> --sha256 <approved-digest>` to check all catalog and OHLCV rows.
3. Start the read-only API in another terminal using `dsv-api --operational-only --operational-snapshot <approved-copy> --operational-snapshot-sha256 <approved-digest> --host 127.0.0.1 --port 8000`. This mode deliberately does not inject scheduler diagnostics.
4. Run:

```bash
python scripts/verify_track_c_runtime.py \
  --base-url http://127.0.0.1:8000 \
  --snapshot /path/to/approved.snapshot.sqlite3 \
  --sha256 YOUR_APPROVED_64_CHARACTER_SHA256 \
  --ticker 005930
```

The script requires successful health, operational summary, stock catalog and OHLCV requests; 404 for an unknown ticker; 405 for a POST to the summary; `unreviewed` evidence status; and an unchanged snapshot digest. Scheduler diagnostics return either 503 (not injected by the standard CLI) or HTTP 200 with safety flags indicating no requests, no mutations, and no PAPER execution eligibility. A 503 is explicitly **not** evidence that scheduler diagnostics were validated.

Only report **operational HTTP smoke passed** after recording the actual operator-run command, timestamp, exact commit, approved snapshot digest, and output. CI passing alone does not satisfy this gate. Real KRX, broker, scheduler, and deployed public-server checks remain separate approval-dependent work.
