# Phase 3: optional read-only OHLCV endpoint

`GET /api/v1/stocks/{ticker}/ohlcv` exposes at most the latest **2,500 actual daily observations** from the original unadjusted KOSPI market database, returned oldest-first. Dates and OHLCV are untouched; no corporate-action adjustments, interpolation or synthetic bars occur. Original KRX no-trade bars may contain zero open/high/low and nonzero reference close.

This endpoint is **disabled** unless the API process explicitly supplies `--market-db` and `--market-db-sha256`. Use the exact fingerprint from an approved stable market DB; never serve mutable production storage. It validates the file's SHA-256, SQLite integrity, KOSPI/unadjusted/krx metadata, and checks the fingerprint again before each request. This incurs hashing overhead; use a published, immutable artifact, not a live mutable DB.

Start with a previously reviewed immutable research API snapshot:

```bash
dsv-api --snapshot .data/research-api-snapshot.json \
  --market-db .data/market.sqlite3 \
  --market-db-sha256 <reviewed-lowercase-64-character-sha256>
```

The market DB is optional. Without opt-in, the route returns 503. An unknown ticker returns 404; an invalid ticker returns 422. Responses match `TrisMarketLens/src/domain/ohlcv.ts` (with explicit no-trade support). This route **does not generate AI inference or trading advice**.
