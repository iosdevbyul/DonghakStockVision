# Read-only Operations API v1

The existing FastAPI application serves three GET endpoints. Configure the DART
reader explicitly with `dsv-api --snapshot <snapshot> --operations-root <repository>`.
The optional existing `--market-db` / `--market-db-sha256` pair enables recorded
SQLite ingestion outcomes. No new database, scheduler or execution interface is added.

## Contract

All responses use `schemaVersion: "operations_v1"`, camelCase properties, explicit
nullable values and UTC ISO 8601 timestamps ending in `Z`. OpenAPI provides the
complete typed schema. Examples illustrate item contracts and omit additional
sources/alerts for brevity; JSON arrays can be decoded by Swift Codable. Unknown times
are `null`, never filesystem modification times, approval times or request times.

| GET endpoint | Query parameters | Response |
| --- | --- | --- |
| `/api/v1/operations/runs` | `offset` 0–100000 (default 0), `limit` 1–100 (default 25) | schemaVersion, items, total, offset, limit, evidenceStatus, diagnostic |
| `/api/v1/operations/freshness` | none | schemaVersion, items |
| `/api/v1/operations/alerts` | same bounded pagination as runs | schemaVersion, items, total, offset, limit |

Invalid pagination returns the existing FastAPI 422 response. Missing optional
sources return HTTP 200 with unavailable/unknown evidence, not invented empty success.
Corrupt required DART evidence suppresses DART run projections and returns `invalid`
with a fixed diagnostic. Independently available SQLite audit rows may still be shown
as partial evidence. A pinned database validation failure suppresses its rows.

### Runs

Each item has `id`, `operationType` (`dart_batch_acquisition`, `dart_session`,
`market_ingestion`), nullable `startedAt` / `completedAt`, `executionStatus`
(`completed`, `incomplete`, `failed`, `interrupted`, `not_started`), `evidenceStatus`
(`validated`, `partial`, `invalid`, `unavailable`), nullable `recordCount`, `sourceType`
(`dart_ledger`, `sqlite_collection_audit`), `semanticApproval: false`, and nullable
safe `diagnostic` and nullable `blockingStatus` (`blocked`, `unknown`). Validated means local contractual integrity, **not independent
source authentication**. SQLite recordCount is the recorded row count, not a newly
verified ingestion count. Its successful recorded outcome remains partial evidence.

Order: newest known completion/start timestamp first, then descending stable ID;
untimed DART records follow timestamped records. DART markers do not record execution
start/end times, so those fields remain null. Approval timestamps are not execution
start timestamps. Unapproved session Drafts are excluded. Counts describe preserved
acquisition evidence, never corporate-action coverage or semantic approval.

Illustrative synthetic payload (not a real execution claim):

```json
{"schemaVersion":"operations_v1","items":[{"id":"batch-0001","operationType":"dart_batch_acquisition","startedAt":null,"completedAt":null,"executionStatus":"completed","evidenceStatus":"validated","recordCount":25,"sourceType":"dart_ledger","semanticApproval":false,"blockingStatus":null,"diagnostic":null}],"total":1,"offset":0,"limit":25,"evidenceStatus":"validated","diagnostic":null}
```

### Freshness

Items have `sourceId`, nullable `latestVerifiedDataAt`,
`latestSuccessfulIngestionAt`, `latestDataDate`, `recordCount`, `thresholdSeconds`,
`blockingReason`, `freshnessBasis` (`acquisition_age`, `unavailable`),
`freshnessStatus` (`fresh`, `stale`, `unknown`, `blocked`), and `evidenceStatus`.

* `market_snapshot`: validated snapshot coverage provides date-only latest data and
  bar count. No verified ingestion timestamp or market-calendar freshness policy
  exists in that snapshot. Timestamp fields are null; freshness is unknown.
* `dart_acquisition`: local ZIP/receipt replay provides verified acquisition count
  and latest receipt `retrieved_at` among Verified Local documents. This is collection
  time, not DART publication time or historical point-in-time knowledge.
* No freshness threshold is assumed by default. An operator may explicitly configure
  `--dart-acquisition-freshness-seconds <positive integer>` for **acquisition age only**.
  Age ≤ threshold is fresh; age > threshold is stale. Missing/naive/invalid/future
  timestamps remain unknown. Required evidence failure is blocked. This optional
  policy says nothing about publication completeness, market sessions or holidays.

```json
{"schemaVersion":"operations_v1","items":[{"sourceId":"market_snapshot","latestVerifiedDataAt":null,"latestSuccessfulIngestionAt":null,"latestDataDate":"2026-10-01","freshnessStatus":"unknown","evidenceStatus":"partial","recordCount":1099255,"thresholdSeconds":null,"freshnessBasis":"unavailable","blockingReason":"timestamp_and_freshness_policy_unavailable"}]}
```

### Alerts

Items have stable `id`, `category`, `severity` (`warning`, `error`), `basis`
(`recorded`, `advisory`), `sourceId`, fixed safe `diagnostic`, nullable `occurredAt`,
`verifiedCount`, `requiredCount`, and nullable `blockingStatus` (`blocked`, `unknown`). IDs sort ascending, pagination does not suppress
alert derivation. Historical incident times are not synthesized.

Recorded error categories: `pipeline_blocked`, `validation_failure`,
`execution_interruption`, `acquisition_failure`, `unresolved_reservation`.
Advisory warning categories: `missing_evidence`, `freshness_unknown`,
`freshness_stale`. Missing evidence is an advisory, not a fabricated past incident.
Corporate actions / Runner tail integration / Run input freeze states come from
snapshot readiness labels; absent labels produce unavailable evidence advisories.
Corporate coverage counts come only from a recognized snapshot `Complete coverage`
metric, not acquired document counts. Unknown counts are null. No zero/993 value is
hardcoded in the API.

```json
{"schemaVersion":"operations_v1","items":[{"id":"corporate-actions","category":"pipeline_blocked","severity":"error","basis":"recorded","sourceId":"corporate-actions","diagnostic":"snapshot_pipeline_blocked","occurredAt":null,"blockingStatus":"blocked","verifiedCount":0,"requiredCount":993}],"total":1,"offset":0,"limit":25}
```

## Evidence mapping and investigation

| Existing source | Available evidence | Limits |
| --- | --- | --- |
| `operational_registry.py` / `operational_artifact_preflight.py` | model proposal and structural provenance contracts | not an execution registry; no successful runs inferred |
| DART document plan + `dart_document_batch.inventory` | request ownership, scope, SHA-256, bounded ZIP/CRC validation, claims, events, completion markers | acquisition only; whole-ledger validation fails closed |
| `dart_multi_batch_session.checked_records` | Draft/approval/binding fingerprint consistency, interruption/resume history, child completion links | private approval contents never returned; Draft is not execution |
| Research `SnapshotReader` | frozen validated coverage/readiness snapshot | export date/ingestion instant and independently approved freshness policy unavailable |
| Pinned `OhlcvMarketReader.collection_audit` | actual `collection_runs` outcomes, finished_at, recorded rows | recorded success does not prove complete raw provenance; partial status |
| AnalysisStore `analysis_executions` / `operational_registry` | artifact persistence audit / model registration metadata | persistence timestamps are not pipeline completion; no read-only independently pinned analysis adapter yet; not projected as successful runs |
| DecisionStore `decision_executions` | computed decision audit linked to immutable bundles | no operational execution approval inferred; not exposed by this API |
| Pipeline / backtest runner | in-memory execution contracts and research artifacts | no reviewed durable generic operational history adapter; no fabricated executions |

The SQLiteStore constructor creates schema and is never used in GET. The existing
pinned market reader uses `mode=ro&immutable=1`, query_only, integrity/scope validation
at startup and file hash checks around audit reads. Active databases are not supported.
SnapshotReader is loaded once; replacing its JSON on disk does not combine old/new
fields in an HTTP response. Reload requires an explicit application restart.

DART readers reuse the existing non-writing shared kernel lock and validate inventory
and all parent records in one evidence capture per response. An active exclusive writer
returns invalid/unavailable projections rather than partially successful records.
Readers do not fix missing receipts or bindings. External writers bypassing the kernel
lock are outside the existing evidence contract. Validation replays all local ZIPs and
may be expensive as the archive grows; no unvalidated cache is introduced.

## Security and operational boundaries

Existing backend has no authentication middleware; CORS is not authentication.
Operations uses the same read-only transport boundary. Keep the existing localhost
binding or deploy behind an authenticated gateway before exposing administrative
metadata. This PR does not add deployment/auth configuration or claim public access
is authorized. Evidence root and database pins are operator configuration, not query
parameters. No absolute paths, raw exception messages, approval JSON, request URLs,
API keys, source bodies or headers are returned.

GET performs no external HTTP, key loading, approval, reservation, evidence repair,
collection, session resume, snapshot regeneration or file/database writes. Acquisition
and approval behavior is unchanged. Test fixtures are synthetic and do not constitute
real official evidence.

## Known limitations / iOS integration

Codable clients should allow nullable timestamps/counts and handle unavailable/partial
sources independently from execution status. Use stable IDs, not timestamps, as item
identity. There is no push alert feed, session approval endpoint or live execution UI.
A generic reviewed pipeline/backtest run store, UTC source publication timestamps,
independently approved freshness policies and ingestion provenance verification are
still needed for richer operational dashboards. Absence of failures is never success.
Corporate-action semantic review, historical identity/PIT validation, Runner Tail and
Run Input Freeze remain independently gated. This API does not authorize model use,
backtesting or a live DART session.

## Local investigation result (2026-10-15)

Read-only inspection recognized completed batch-0001 and batch-0002, 51 locally
verified DART documents, and the unapproved session Draft. The standalone Pilot
contributes to verified acquisition counts but is not a batch/session history item.
Legacy status-800 cache and previous failed Pilot audit are preserved outside the
managed batch ledger; this API does not reinterpret those responses as documents.
The analysis database contains six artifact persistence audit rows and zero model
registrations; those rows cannot independently establish successful pipeline runs.
The existing Research Snapshot reports corporate coverage 0/993 and Corporate Actions,
Runner Tail and Run Input Freeze blocked. These are snapshot assertions, not new live
research validation. Actual OpenDART requests during this implementation: zero.

For example, a Swift consumer can keep timestamps as nullable ISO strings and decode
without inventing dates (no iOS source is changed by this PR):

```swift
struct OperationsRuns: Decodable {
    let schemaVersion: String
    let items: [OperationRun]
    let total: Int
    let offset: Int
    let limit: Int
    let evidenceStatus: String
    let diagnostic: String?
}
struct OperationRun: Decodable {
    let id: String
    let operationType: String
    let startedAt: String?
    let completedAt: String?
    let executionStatus: String
    let evidenceStatus: String
    let recordCount: Int?
    let sourceType: String
    let semanticApproval: Bool
    let blockingStatus: String?
    let diagnostic: String?
}
```

If converting timestamp strings to Swift Date, support ISO 8601 both with and without
fractional seconds; null must remain absent rather than becoming Date().
