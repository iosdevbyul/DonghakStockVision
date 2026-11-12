# Daily research results API and TrisMarketLens contract

## Baseline and existing web behavior

This is based on main `3fe03b4904b29dc92657bc31fc2041dc700d7149`, merged PR #227.
The separate model-readiness PR #228 was open at baseline inspection; this work
neither changes that branch nor depends on its new training modules.

Existing `GET /api/v1/stocks/{ticker}/analysis` serves a separately reviewed
`published_stock_analysis_v1` publication. TrisMarketLens already fetches that
route through `src/data/StockAnalysisDataSource.ts` and validates it through
`src/domain/stockAnalysis.ts`. That contract admits a numeric score only for a
record labelled `verified`. It must not be used to relabel ordinary daily research
as approved inference. Its response, default 503 behavior and publication reader
are unchanged. Aggregate model metrics never become ticker scores.

The missing functionality is a read-only view of PR #227's retained per-ticker
research artifacts, including blocked/failed/pending work and source references.
No new model, collector, journal, artifact schema or web dashboard is introduced.

## Endpoint and queries

```text
GET /api/v1/stocks/{ticker}/research-analysis
```

| Query | Meaning |
|---|---|
| `tradingDate=YYYY-MM-DD` | Exact analysis input/anchor trading date |
| `fromDate=YYYY-MM-DD`, `toDate=YYYY-MM-DD` | Inclusive range; either bound may be omitted |
| `limit` | Page size, default 50, range 1–200 |
| `offset` | Matching records to skip, default 0, maximum 100,000 |

Exact-date and range filters are mutually exclusive. A reversed range returns
400; malformed dates/tickers and invalid pagination return 422. Unknown ticker or
no matching stored job returns an empty series, never a synthetic replacement.
Without an explicitly configured reader the route returns 503. POST is 405.
Newest trading dates come first. Only the latest committed attempt for each
job/ticker is returned; earlier failures remain in the source artifact store.

The OpenAPI schema exposes `DailyResearchSeries`, `DailyResearchRecord`,
`ResearchPeriod` and `ResearchSourceReference`. Their exact camelCase JSON is the
frontend contract. An empty response is:

```json
{
  "schemaVersion": "daily_research_series_v1",
  "ticker": "005930",
  "availability": "no_recorded_analysis",
  "records": [],
  "totalRecords": 0,
  "offset": 0,
  "limit": 50,
  "lastSuccessfulAnalyzedAt": null,
  "lastSuccessfulTradingDate": null,
  "lastSuccessfulDataOrigin": null,
  "timestampAuthority": "local_operational_clock_not_authenticated"
}
```

Nonempty `records` have these fields:

| Field | Interpretation |
|---|---|
| `analysisId` | Existing immutable result artifact identifier; null for unexecuted/pending work |
| `jobReference` | Domain-separated public reference to the retained job |
| `ticker`, `tradingDate` | Exact retained owner identity |
| `analyzedAt` | Retained local `checked_at`; null when no attempt is recorded |
| `modelId`, `modelVersion` | Both identify the existing content-addressed Phase 2 model artifact; no registry identity is invented |
| `status` | `completed`, `blocked`, `failed` or `pending` |
| `reasonCode` | Allowlisted fixed reason, or a generic redacted failure code; null for completion |
| `attempt` | Retained attempt ordinal; null for pending work |
| `dataOrigin` | `real` or `synthetic_fixture`, checked against original completion origins |
| `usageRestriction` | `research_only` or `synthetic_test_only`; never an operational approval |
| `requestedPeriod` | The original bounded input window, not an assertion every day had observations |
| `observedPeriod` | Actual expected/reproduced range under the completed v1 owner; null for incomplete work |
| `inputFingerprint` | Original input hash; `inputFingerprintScope` is `selected_job_universe`, not a fabricated ticker-only hash |
| `sources` | Date/origin and public reference for each original source completion |
| `researchScore` | Exact retained conditional research score, or null; never a calibrated probability |
| `researchContext` | `prior_decline`, `prior_rise`, `flat`, or null for incomplete work |
| `researchSignal` | Display-only `up`, `down`, `neutral`, or `unavailable` |
| `displayThreshold` | Existing Phase 2 `THRESHOLD` (0.5), used for the display projection only |
| `scoreMeaning` | Always `uncalibrated_conditional_research_score` |
| `verification` | Always `retained_artifact_integrity_not_fresh_source_replay` |

The signal uses the existing Phase 2 convention: the applicable up/down context
is displayed when its score meets the threshold, otherwise neutral. Completed
flat-context results have a null score and neutral signal. Incomplete work always
has a null score and unavailable signal. This does not record a prediction, change
a strategy, create a contemporaneous BUY/SELL decision or authorize an order.

`lastSuccessfulAnalyzedAt`, its trading date and its **data origin** refer to the
latest completed result across all retained history for this ticker, independently
of date filters/pagination. A fixture completion is therefore never presented as a
real-market success without its classification. An empty page is not an analysis
execution success. Source/model failures that occurred before PR #227 created a
frozen daily job leave no per-ticker artifact: this API cannot reconstruct their
missing reason or execution time. Use existing pipeline diagnostics/operator
reports for those global pre-job failures. A pending row represents an existing frozen job without a
committed ticker result; its time/analysis ID are not fabricated.

Every record retains false values for `independentlyAuthenticatedChronology`,
`independentlyAuthenticatedCustody`, `forwardPredictionEligible`, `pitQualified`,
`performanceApproved` and `executionAuthorized`; `performanceMetrics` is null.
No future-price probability, accuracy, return or independently authenticated origin
is supplied by this endpoint.

## Read-only evidence boundary and private information

`DailyResearchReader` reuses `ReadOnlyAnalysisStore.get` content-address checks and
`ForwardCapturePlan` parsing. It validates daily owner versions, model IDs, source/
plan/authorization/batch bindings, origin classification, expected source coverage,
features/context, scores, attempt ownership/order and local timestamp ordering.
Contradictory, orphaned, unsupported, incomplete-score or rehashed inconsistent
artifacts fail closed. It never calls inference or the capture/provider/broker APIs.

The snapshot SHA must be independently retained by the operator, not obtained from
an untrusted client or merely trusted because it accompanies an untrusted file.
A hash proves integrity relative to that input, not issuer/custodian authority.
Original capture/receipt verification remains owned by collection and the daily
runner. Serving these retained findings does **not** replay original archive bytes
or revalidate the current model/source/custody state on every request.

Only explicit public DTO fields are retained/serialized. Policy/config/plan/
capture/batch PIN values, authorization roots, filenames, credentials, arbitrary
exception text and model feature internals do not leave the adapter. Public source
references are domain-separated hashes of retained owner identities, not the
private owner PINs. An authorized offline operator can map an `analysisId` back
through `AnalysisStore.get` to its exact job and original source references.
No public raw-source download or private-path resolver is added.

The loader uses `mode=ro&immutable=1` and `query_only`, verifies SQLite integrity,
bounded payloads and the complete file pin before/after loading, then closes it.
No schema initialization, SHM/WAL creation, audit writes or source updates occur.
Only immutable safe projections survive startup. Disk changes do not silently
refresh the served view: publish/review a new snapshot/pin and restart explicitly.
This avoids serving a mixture of different live writer transactions. Keep the
source quiescent while loading, preferably an operator-prepared offline copy.
A matching before/after hash is not a cross-filesystem atomicity or authentication
service. Active WAL/journal sidecars and symlinks are refused.

Limits are 256 MiB per snapshot, 366 daily jobs, 100,000 attempts, 2 MiB per job and
64 KiB per attempt, in addition to original job/source bounds. Unknown unrelated
AnalysisStore artifact kinds are ignored; aggregate model/evaluation artifacts
never become security-level rows.

## Explicit server configuration and web integration

```sh
dsv-api --snapshot "$EXISTING_RESEARCH_API_SNAPSHOT" \
  --daily-research-snapshot "$OFFLINE_DAILY_ANALYSIS_STORE" \
  --daily-research-snapshot-sha256 "$RETAINED_DAILY_STORE_SHA256" \
  --host 127.0.0.1 --port 8000

curl 'http://127.0.0.1:8000/api/v1/stocks/005930/research-analysis?tradingDate=2025-07-21'
```

These are operator instructions, not deployment performed by this task. Both
snapshot arguments are required together. A configured research snapshot reader
uses loopback binding and disabled forwarded-header trust, consistent with existing
private operational/portfolio snapshot serving. `--operational-only` continues to
forbid research/publication inputs. No real server or scheduler was activated here.

TrisMarketLens can reuse its existing `DONGHAK_API_BASE_URL`, HTTP error handling
and fetch architecture. In a separately scoped web change, extend its existing
analysis adapter with an explicit research mode and a validator for this schema;
do not send this body through `isStockAnalysisSnapshot`, mark it `verified`, or
reuse the old numeric score as a rise-probability field. Preserve `dataOrigin`,
`usageRestriction`, `scoreMeaning`, status and source attribution in display state.
Handle 503 and empty/pending/blocked/failed separately; never substitute mock rows
for a failed real-source request. No web files or UI were changed in this task.

## Actual local readiness and verification

At inspection, main ends at merged #227. #228 remains separate from this feature.
The inspected retained AnalysisStores contain training/model/snapshot artifacts,
but no `daily_research_job`, `daily_research_result` or per-ticker `signal` artifacts.
Consequently there are no actual retained daily research scores to publish yet.
This endpoint's implementation does not turn training metrics into such records.
Actual operation needs completed verified collection history, a compatible explicit
research model, a successful daily run and an operator-reviewed serving snapshot.
Provider-backed prospective recording and independent authority remain separate gaps.

```sh
pytest -q tests/api/test_daily_research.py tests/integration/test_daily_research_api.py
pytest -q tests/integration/test_daily_research.py tests/api/test_stock_analysis.py \
  tests/api/test_app.py tests/api/test_operational_market_read.py \
  tests/api/test_krx_scheduler_diagnostics.py tests/api/test_analysis_publisher.py
ruff check src tests
ruff format --check src tests
mypy
python -m build --no-isolation
```

The end-to-end test runs original MockTransport capture, ingestion, verification,
existing model scoring and immutable result storage before HTTP readback. It forbids
new inference during GET and compares original source/output bytes. Contract
fixtures also exercise real-vs-fixture labels, filters/pagination, absent/incomplete
results, failure redaction, forged bindings/attempts/times/scores, modified hashes,
read-only startup and existing publication compatibility. Fixture values are not
claims of actual KRX acquisition, calibrated predictions or validated performance.
