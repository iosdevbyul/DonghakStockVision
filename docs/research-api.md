# Read-only research API

DonghakStockVision exposes an optional FastAPI transport for reviewed research
snapshots. The API layer does not run model inference, generate signals, mutate
evidence, or execute backtests.

## Install

Install the optional API dependencies:

```bash
python -m pip install -e '.[api]'
```

## Deterministic snapshot export

The API snapshot is generated explicitly. It is not assembled on HTTP request.

```bash
dsv-api-export \
  --root . \
  --market-db .data/market.sqlite3 \
  --output .data/research-api-snapshot.json
```

The exporter reads the market database with SQLite `mode=ro&immutable=1` and
`query_only=ON`, rejects active WAL/journal sidecars, verifies database integrity,
and compares the file SHA-256 before and after the export.

It also validates the committed official evidence artifact fingerprints and the
reviewed model publication record at
`docs/research-api/model-publication.json`. The market ticker universe must match
the exact DART mapping universe. Mismatches fail closed rather than being filled,
mapped, or dropped.

The exporter derives:

- market row count and date coverage from persisted KRX bars
- per-ticker first and latest stored dates
- current English company names only from exact verified DART stock-code mappings
- evidence readiness and blocker counts from preserved official artifacts
- locked baseline policy and chronology from existing code/evidence contracts
- qualified model metrics from the reviewed publication record

Sector metadata remains null because no approved sector source is currently part
of this export contract. Unresolved company names also remain null.

Output is deterministic canonical JSON with no export timestamp. Existing output
files are never overwritten.

## Snapshot boundary

The API reads the validated JSON snapshot from:

```text
DSV_API_SNAPSHOT_PATH=.data/research-api-snapshot.json
```

The snapshot is validated at process startup and then treated as immutable input.
The API never writes back to that file or to the market, analysis, decision, or
backtest stores.

## Run

```bash
dsv-api --snapshot .data/research-api-snapshot.json
```

The default bind is local only:

```text
http://127.0.0.1:8000
```

Health:

```text
GET /api/v1/health
```

The local Tris Market Lens development origins are allowed by CORS:

```text
http://localhost:3000
http://127.0.0.1:3000
```

## Read endpoints

```text
GET /api/v1/project/status
GET /api/v1/research/coverage
GET /api/v1/models
GET /api/v1/models/{direction}
GET /api/v1/evidence
GET /api/v1/evidence/{layer}
GET /api/v1/backtests/baseline
GET /api/v1/stocks
GET /api/v1/stocks/{ticker}
```

Detail endpoints return 404 when the requested reviewed item does not exist.
Malformed stock codes are rejected before reader lookup. Data endpoints return
503 when no snapshot reader is configured.

## Research integrity

The publication/export/API path is read-only presentation transport.

- no model loading or refitting
- no inference
- no signal generation
- no evidence recollection
- no missing-bar interpretation
- no backtest execution
- no performance fabrication
- no market-row repair, ticker remapping, or survivor filtering
- no write access to production research stores

The committed model publication record is presentation metadata for already
completed research. Updating it is a reviewable source change; the exporter does
not calculate new model performance.

Stock names are nullable and use only exact current English DART corporation names.
Current identity does not prove full-period historical identity.
