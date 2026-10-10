# Published stock-analysis API (Phase 3)

The separate optional publication file supplies **real reviewed, existing** inference records to `GET /api/v1/stocks/{ticker}/analysis`. This is a read-only transport, not an inference engine. The global Phase 2 `model-publication.json` contains **aggregate evaluation metrics only** and must **never** be projected into individual stock probabilities or directional recommendations.

The route defaults to **503** unless explicitly configured using `dsv-api --analysis-publication /path/to/reviewed.json`. The publisher and review process that creates this file are **not** implemented in this PR. No approved stock-level prediction publication is currently supplied.

JSON layout:

```json
{"schemaVersion":"published_stock_analysis_v1","sources":[{"ticker":"005930","records":[]}]}
```

Each optional record contains `id`, `ticker`, `analyzedAt` (timezone aware), `dataThrough` (YYYY-MM-DD), `modelId`, `modelVersion`, `direction` (up/down/neutral), `score` (0..1 or null), `validation` (verified/pending/blocked), `explanation` (string or null). Pending and blocked records may not have numeric scores. Maximum 500 records per ticker, duplicate IDs/tickers rejected. Unknown ticker returns an empty record list, not an invented prediction.

Before adopting a real publisher, add provenance ties to immutable approved model artifacts, ticker identity, input data snapshot, inference execution time, look-ahead/leakage controls, and an explicit approval gate. This transport alone cannot prove the semantic validity of a record labeled verified. Do not turn on this endpoint using unreviewed external data.
