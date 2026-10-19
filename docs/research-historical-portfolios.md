# Verified historical portfolio research access v1

This optional FastAPI feature serves **previously executed** five-ticker results.
No GET loads a model, runs inference, acquires data or replays a backtest. Existing
Stock, OHLCV, Security Metadata, analysis and Operations contracts are unchanged.
No evidence-download endpoint or HTTP publication mutation is provided.

## Publication is an explicit offline decision

The states are distinct:

1. A generated `five_ticker_historical_result_v1` is local research evidence.
2. `shared_trading_receipts_v1` integrity establishes transport structure only.
3. `expand_validated()` reconstructs original evidence and performs authoritative
   replay, preserving the exact result/report hashes and economic calculations.
4. A trusted operator registers that validated result in a separate private local
   publication root. Only index entries explicitly registered this way are visible.
5. Missing/corrupt evidence, a changed index or revocation blocks access.

The minimal store contains flat files, not another DB or economic ledger:

```text
<private-publication-root>/
  input.json                         # operator-staged compact archive, not accessible
  <report-id>.archive.json            # byte-identical compact source, never served
  <report-id>.projection.json         # allowlisted, bounded read models
  <report-id>.manifest.json           # immutable publication/validation binding
  index.json                         # atomic sorted publication/revocation index
  writer.lock                        # offline writers only
```

The root must be an existing **canonical path without symlink components**, owned
by the operator, separate from `.data`, original evidence, model and Operations
stores. Create it privately (0700) and stage synthetic or authorized compact files
there. Committed examples contain synthetic facts only. No original archive or
private locator is returned over HTTP.

Offline CLI (use the installed entry point or `python -m
 donghak_stock_vision.portfolio_publication_cli`):

```sh
dsv-portfolio-publication --root <canonical-private-root> publish \
  --archive input.json --result-hash <original-result-sha256> \
  --report-hash <original-detailed-report-sha256>
# Prints reportId and indexSha256; no secrets, paths or raw evidence.

# Subsequent registration must pin the CURRENT index explicitly.
dsv-portfolio-publication --root <canonical-private-root> \
  --index-sha256 <current-index-sha256> publish \
  --archive another-input.json --result-hash <result-sha256> --report-hash <report-sha256>

dsv-portfolio-publication --root <canonical-private-root> \
  --index-sha256 <current-index-sha256> revoke --report-id <report-id>
```

No missing/corrupt existing index is silently initialized. Duplicate registration
is idempotent and verifies the existing publication; revocation does not silently
republish. Writers use a kernel lock, same-directory temporary writes, fsync,
exclusive immutable file creation and atomic index replacement. A crash before
index replacement leaves invisible orphan files; the operator must investigate
rather than silently repair conflicting immutable files. There is no automatic
orphan scan or collection. Maximum index size is 128 KiB and 128 registrations,
including revoked entries. The root has no unlimited discovery operation.

The opaque report ID hashes the archive **raw-byte** SHA-256, canonical projection
SHA-256 and original result/report hashes. This is distinct from canonical archive
transport identity and the original result/run/report/performance identities.
Publication time is a timezone-aware operator registration timestamp in the
manifest, never a synthetic decision, observation or execution timestamp. Repeated
registration preserves the original publication time and ID.

## Online trust and local exposure

Configure the expected index SHA **outside the publication root** from the offline
command's reviewed output. That external operator pin is the publication trust
anchor. A self-consistent hash or arbitrary file is not authorization. This is not
a signature authority or protection against an operator who controls both server
configuration and storage. Do not automatically adopt a new hash from disk.

```sh
dsv-api --snapshot <existing-research-snapshot> \
  --portfolio-publications <canonical-private-root> \
  --portfolio-index-sha256 <approved-index-sha256> --host 127.0.0.1
```

Without both options the feature is unavailable. The application also accepts an
explicit `PortfolioPublicationReader` dependency, matching existing reader patterns.
The CLI rejects a non-loopback bind when this reader is enabled and disables
Uvicorn proxy-header interpretation. Every new route checks the actual client
address, Host hostname and optional Origin against loopback names/addresses.
Forwarded headers do not authorize access. No new user identity or token system
is invented. **Local trusted personal/development use only: never expose these
routes through a public listener, reverse proxy, tunnel or shared production API.**
This is not per-user authorization; other trusted local processes can read reports.
Existing CORS behavior is retained; CORS alone is not the access boundary.

Every request rechecks the externally pinned index and selected immutable manifest,
archive raw bytes and projection hashes. Archive hashing streams in 1 MiB chunks;
the HTTP path never parses/expands an archive or imports its replay entry point.
There is **no application verification cache** and no mtime/size-only trust shortcut.
A byte change invalidates the next GET, including after a previously successful
GET. Publication/revocation updates invalidate old readers immediately via index
mismatch; restart/reconfigure with the new reviewed pin to observe the updated
index. An in-flight response may use the previously verified byte snapshot.
Missing files fail closed. Invalid storage requires offline revalidation; HTTP
never repairs it. No stale response is used on verification failure.

All filesystem components are opened relative to directory descriptors with
`O_NOFOLLOW`; flat locators reject traversal. Regular-file checks reject devices,
directories and FIFOs. This filesystem implementation targets the project's local
macOS/Linux deployment, not Windows. Offline full replay retains existing bounded
archive limits and can be expensive; it is not a GET responsibility.

## HTTP contract

All payloads use `published_historical_portfolio_v1` and the existing HTTP error
shape `{"detail": {"code": "stable_reason"}}`. Top-level fields use the API's
camelCase convention. Nested source evidence retains original snake_case names,
values, decimals-as-strings, nulls and stable hashes. Original calculations are
copied, never recomputed. Pydantic response schemas appear in OpenAPI.

| GET route | Payload |
| --- | --- |
| `/api/v1/research/historical-portfolios` | Published collection, summary metadata only |
| `.../{report_id}` | Shared-account performance, provenance and valuation summary |
| `.../{report_id}/tickers/{ticker}` | Exact ticker, position episodes and paginated decisions |
| `.../{report_id}/trades` | Paginated opportunity/order/fill timeline, optional exact `ticker` |

Collection and timeline pagination uses `offset=0&limit=10`, max offset 100,000,
max page 20. Collections sort by opaque report ID; trade rows preserve the original
validated chronological/opportunity order, including explicit priority within a
date. Ticker strings preserve leading zeroes and alphabetic characters; no fuzzy
matching, name-derived identity or case conversion occurs. Unknown extra query
parameters do not select files. Callers cannot supply roots, source URLs or model
paths. No filesystem or source evidence endpoint exists.

The new routes cap the combined decoded path/query length at 4,096 characters/bytes
with `oversized_request` (413). This is an application guard, not a network-layer
request-body or header cap; configure the local server accordingly.

A response is capped at **256 KiB** before HTTP serialization; a read model is
capped at **4 MiB** offline and when read. Archive bytes are capped at the existing
64 MiB, expanded evidence at 256 MiB and all existing artifact/reference/depth/work
limits apply offline. Source five-ticker requests already limit decision dates to
10; read-model generation still has an explicit byte cap. Collection validation
reads at most 20 selected archives per request, sequentially, not arbitrary disk
contents. Responses do not include expanded Ledger/Position History receipts,
checkpoints, model artifacts or source datasets. Total Python/native memory of
**offline** replay is not bounded by the response cap.

Every envelope and collection item states research-only eligibility. This grants
read-only access, **not** strict PIT/OOS qualification, historical stock-selection
skill, real profitability, investment suitability, production AI approval or
brokerage eligibility. Existing Corporate Actions Coverage 0/993 and Corporate
Actions, Runner Tail, Run Input Freeze and strict PIT blocks remain unchanged.

Summary fields include cash totals/available/reserved cash, original Performance
P&L/return/equity/costs/coverage, decision/order/fill/completed-position/pending counts,
model ID or HGB pair references and original result/report/run/performance hashes.
Unavailable equity/return/unrealized P&L remain null with reason codes. The realized
summary value is `performance.ledger_realized_pnl`, including partial exits, not an
independent sum or only completed trade episodes.

Each trade entry has a stable opaque eventId bound to run/opportunity identity,
original source references and separate admission/reservation/execution evidence.
`filled`, `pending`, `accepted`, `rejected`, `no_order` are presentation categories;
`terminalStatus` separately preserves cancellation/expiry/rejection. A pending
reservation is accepted but has no confirmed fill. The timeline includes HOLD,
WAIT and unavailable opportunities; absent actions remain null. Absence of an exit
is not interpreted as a deliberate profit-taking strategy. Rejected admissions
include source `blocked` outcomes except `no_order_action`.

Linear feature values/contributions are copied when present. HGB creation time and
attribution stay null when unavailable. The original model family and pair hashes
remain exact. Directional scores/statuses are copied from validated decision input analysis in
`signalEvidence` with its canonical source hash; unavailable inputs stay null.
A distinct aggregate signal-status field is not recorded by the source; it is null
with `aggregate_signal_status_not_recorded`, never inferred from an action or score. Daily-bar events retain source/publication times;
exchange execution time stays null. Contributions do not establish causation and
conditional scores are not calibrated return probabilities.

| Error code | HTTP | Meaning |
| --- | ---: | --- |
| `report_not_found` | 404 | Opaque ID absent or malformed |
| `report_unpublished` | 404 | Revoked in approved index |
| `ticker_not_found` | 404 | Well-formed ticker absent from report |
| `archive_unavailable` | 503 | Reader/storage/file unavailable or unsafe symlink/type |
| `archive_integrity_mismatch` | 503 | Archive bytes changed since offline validation |
| `publication_manifest_mismatch` | 503 | Pinned index/manifest/projection/binding mismatch |
| `unsupported_archive_version` | 503 | Unsupported publication/archive/result/report schema |
| `result_validation_required` | 503 / offline | Replay validation absent/failed; offline validation required |
| `report_projection_incomplete` | 503 | Incomplete or unsafe projection; offline work required |
| `invalid_storage_locator` | 503 | Invalid immutable locator, not exposed verbatim |
| `invalid_ticker` / `invalid_pagination` | 422 | Invalid exact ticker or page request |
| `oversized_result` | 413 | Archive/read model/response or publication count exceeds cap |
| `local_research_access_required` | 403 | Not permitted by local-only exposure boundary |

Errors contain no local paths, secrets, database or checkpoint locations, upstream
exception strings or stack traces. Collection requests fail closed if a selected
publication cannot be verified rather than silently hiding corruption.

## Client preparation and examples

[Generated synthetic response examples](research-historical-portfolios-examples.json)
cover list, summary, ticker, trades, unavailable valuation and invalid/revoked IDs.
[Measurements](research-historical-portfolios-benchmarks.json) record query sizes,
first-owner/reused-owner timings and archive integrity rechecks for one and multiple
registered archives. These are bounded TestClient observations on a shared machine,
not production SLA, physical cold-cache or concurrency benchmarks.

Future iOS/Web clients can list report IDs, fetch a summary, then request ticker or
trade pages without downloading evidence. Preserve decimal strings, null reasons,
original source hashes and the explicit research restriction. Display publication
and source timestamps separately. On 404/503/413 display the machine reason and
unavailable state; do not invent zero returns or silently substitute another run.
Clients and their repositories are unchanged in this PR.

## Bounded query observations

Single observations during the full offline regression on the shared development
host. First/reused owner HTTP timings exclude separately recorded app/reader startup;
OS caches were not flushed. No application verification cache is used.

| Publications | Fixture fills | Query | First owner ms | Reused owner ms | Response bytes |
| ---: | ---: | --- | ---: | ---: | ---: |
| 1 | 3 | list | 32.65 | 12.43 | 1,137 |
| 1 | 3 | summary | 17.25 | 11.73 | 4,090 |
| 1 | 3 | ticker | 16.74 | 11.36 | 15,296 |
| 1 | 3 | trades | 18.21 | 11.99 | 7,780 |
| 2 | 0 | list | 103.21 | 43.57 | 2,014 |
| 2 | 0 | summary | 42.07 | 37.49 | 3,722 |
| 2 | 0 | ticker | 120.94 | 37.99 | 4,751 |
| 2 | 0 | trades | 46.53 | 25.92 | 8,389 |

- 1 publication(s): representative compact archive 1,818,330 bytes; separate streaming integrity recheck 4.70 ms.
- 2 publication(s): representative compact archive 1,763,233 bytes; separate streaming integrity recheck 13.87 ms.

The one-publication fixture has three controlled mechanics fills. The two-publication
fixture contains two genuinely inferred linear runs with no fills. Both have five
synthetic tickers. Different fixtures and concurrent work prevent interpreting this
as a scaling or model-performance comparison. No full replay occurs in these queries.

## Local end-to-end operator workflow

See the [local preparation, publication, startup and GET runbook](historical-portfolio-local-runbook.md)
for reproducible synthetic mechanics/linear/HGB demos, the existing publication
CLI's bounded `encode`/`diagnose` commands, explicit external pin capture, exact curl
requests, diagnostics, revocation and foreground shutdown. No HTTP schema or
publication authorization boundary changes are introduced by that workflow.
