# Pinned operational-market read API (KOSPI)

This optional contract reads an **offline snapshot copy** of the private operational SQLite, not the mutable collection database and not the research snapshot.

## Scope

- `GET /api/v1/operational/summary`: observed KOSPI row count, distinct ticker count, distinct trading days and first/last trading date; all values derived from the SHA-pinned offline copy, not assumed exchange completeness
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

## Operational-only local startup

After an explicit operator-reviewed backup and SHA-256 pin, the API can expose
the operational catalog without requiring an unrelated research snapshot:

```bash
dsv-api --operational-only \
  --operational-snapshot /absolute/private/operational.snapshot.sqlite3 \
  --operational-snapshot-sha256 <reviewed-lowercase-sha256> \
  --host 127.0.0.1
```

The operational-only mode rejects simultaneous research snapshots, market DB,
analysis publication, security metadata, portfolio publication and DART
operations inputs. Research endpoints remain unavailable (503). No KRX requests
or active SQLite writes occur. The CLI does not create a backup or automatically
refresh a pinned snapshot. The existing standard research API startup contract
remains unchanged.

Before using with real data, the operator must explicitly approve the offline
backup and startup, review disk permissions and compare the observed ticker
count against the original captured data. A pinned operational observation
remains **unreviewed**, not research-eligible or PAPER-authorized.


The summary endpoint is also available in operational-only mode. Its empty
snapshot result has zero counts and null date bounds. The summary is explicitly
marked `evidenceStatus=unreviewed` and makes no inference about missing KRX
sessions or authorization for PAPER trading. No live database is queried.
