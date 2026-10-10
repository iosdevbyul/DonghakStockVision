# Read-only AI Screener API

`GET /api/v1/analysis/screener` aggregates **existing records from the optional reviewed stock-analysis publication**. It never fits or executes models, generates scores, or manufactures suggestions.

The route returns HTTP 503 unless `--analysis-publication` is configured. A reviewed publication with no records returns `{"entries":[]}` (not proof that inference has executed). Entries preserve `analysisId`, ticker, model identity/version, direction, score, validation, data-through date and timezone-aware analysis timestamp. Names are used only from the optional validated research snapshot's stock mapping; missing names remain null.

This is a transport contract; `validation="verified"` is a publication claim, **not** independent proof of valid trading signals. The separate publication pipeline and research approval gate must validate input snapshot identity, security lifecycle, feature provenance, model execution and the absence of look-ahead leakage before publishing entries. No mock records are returned in HTTP mode.

Frontend source switch: `MARKET_LENS_SCREENER_SOURCE=http` once a real approved publication is supplied. Do not infer future returns or trading readiness from classification scores.
