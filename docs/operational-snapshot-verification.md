# PR #162: verify an offline KRX operational snapshot

This manual verification is strictly read-only. It never contacts KRX, opens
the live operational database or upgrades observed rows into research/PAPER data.

After independent operator approval, create a consistent snapshot using the
existing `dsv market-storage backup` CLI. Review and independently preserve
the reported lowercase SHA-256. Run:

```sh
dsv-operational-verify \
  --snapshot /absolute/private/krx.snapshot.sqlite3 \
  --sha256 <independently-reviewed-sha256> \
  --expected-tickers 940
```

The 940 count is an **optional operator expectation** from an earlier capture,
not a guarantee or exchange-wide completeness claim. Omit the flag when
verifying snapshots expected to contain other ticker counts.

The verifier checks the pinned file, operational identity and integrity,
reported snapshot coverage, every catalog page, unique ticker identity,
observed latest-date alignment, OHLCV availability and price bounds.
It returns a JSON report including the observed row count, distinct ticker
count, trading-day count, date bounds and number of OHLCV bars inspected.
Nonzero exit and no successful report indicate a failure.

This verification does **not** prove completeness of KRX source collections,
eligibility for AI training, backtesting or PAPER execution. Its results may
differ from the earlier 940-row collection if the approved snapshot contains
additional trading days. The snapshot itself must remain in private storage
and must not be committed to Git.
