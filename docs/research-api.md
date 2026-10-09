# Read-only research API

DonghakStockVision exposes an optional FastAPI transport for reviewed research
snapshots. The API layer does not run model inference, generate signals, mutate
evidence, or execute backtests.

## Install

Install the optional API dependencies:

```bash
python -m pip install -e '.[api]'
```

## Snapshot boundary

The API reads one validated JSON snapshot from:

```text
DSV_API_SNAPSHOT_PATH=.data/research-api-snapshot.json
```

The snapshot is validated at process startup and then treated as immutable input.
The API never writes back to that file or to the market, analysis, decision, or
backtest stores.

This first transport step intentionally separates HTTP serving from snapshot
production. A later exporter can assemble the reviewed snapshot from existing
DonghakStockVision artifacts without moving research logic into FastAPI.

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

The API is presentation transport only.

- no model loading
- no inference
- no signal generation
- no evidence recollection
- no missing-bar interpretation
- no backtest execution
- no performance fabrication
- no write access to production research stores

Stock names and sectors are nullable in the backend contract because the current
market database does not prove company-name or sector metadata. A later metadata
adapter must use an approved source rather than filling those fields by guess.
