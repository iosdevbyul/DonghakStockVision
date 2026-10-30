# Pinned operational-market read API (KOSPI)

This optional contract reads an **offline snapshot copy** of the private operational SQLite, not the mutable collection database and not the research snapshot.

## Scope

- `GET /api/v1/operational/stocks?offset=0&limit=25`: paginated KOSPI ticker catalog with `latestDataDate`, `total`, `offset`, `limit`, `schemaVersion=operational_market_v1`, and `evidenceStatus=unreviewed`
- `GET /api/v1/operational/stocks/{ticker}/ohlcv`: up to 2500 historical unadjusted KRX daily bars, chronological, using existing `OhlcvSeries`
- 503 when no operator-approved copy was configured; 404 for no observed ticker; 422 for invalid ticker

No raw provider responses, capture receipts or source credentials are exposed. The API **does not** mark observations eligible for PIT training, backtests, inference, signals, or trading. The catalog is distinct from the existing research `/api/v1/stocks`.

## Startup preconditions

An operator must obtain a consistent offline SQLite copy via SQLite's backup mechanism while respecting existing operational-store permissions and locks. The API never copies, checkpoints or modifies the active operational store. Name the independently reviewed artifact `*.snapshot.sqlite3`, calculate its full-file SHA-256, and keep it off public storage.

Start an already-approved research API snapshot with both optional flags:

```bash
dsv-api --snapshot .data/research-api-snapshot.json \
  --operational-snapshot /absolute/private/operational.snapshot.sqlite3 \
  --operational-snapshot-sha256 <reviewed-lowercase-sha256> \
  --host 127.0.0.1
```

The adapter verifies the SHA-256, SQLite integrity, operational application ID/version and expected observation columns before exposing data; it checks SHA-256 again around every query. Only read-only SQLite `immutable=1` connections are used. Do not pass the currently written `database/operational.sqlite3` as an API input.

## Validation before web adoption

Verify the real snapshot's observed ticker count and scope directly through this read-only endpoint. **Do not assume 940** from an earlier collection statement; historical and latest-date counts may differ. Verify exact ticker OHLCV bars and missing/invalid cases. Existing `/api/v1/stocks/{ticker}/ohlcv` continues to use the separate immutable research market DB and is not redirected to operational data.

The web client must explicitly opt in to these operational routes and label them **unreviewed operational observations**, not reviewed research, forecasts or validated market completeness. No actual local SQLite or HTTP verification was performed by this PR.
