# Forward market-data capture pilot

## Integration decision (recorded before implementation)

Base: main `731d6e0891dcdb6b9e35a4a28958e746001dd9a6`, merged PR #138.

Reuse `dsv collect/update`'s `KRXProvider.fetch_market/parse_market`,
`KRXMarketPipeline.collect(tickers=...)`, `SQLiteStore.archive/save`, automatic
`raw_capture_receipt_v1`, and the existing receipt export/verification functions.
The pilot adds an opt-in bounded HTTP transport and a local append-only metadata
journal **inside a newly provisioned pilot SQLite archive**. Original source bytes
remain in `raw_pages`, not in a second object store. Complete manifests are
published atomically after original ingestion. Interrupted response/ingestion
stages are visible and may be reconciled offline without repeating HTTP requests.
An unknown request outcome requires a new explicit attempt, never an automatic
retry on restart. Original collection commands and archive formats stay unchanged.

The existing compact trading archive is a JSON transport for economic receipts,
not a raw HTTP byte store, so it is not used as a competing source archive.
Existing custody export/outbox/readback can consume the original raw receipt IDs;
no S3 submission or third-party custody is activated by this pilot.

Existing KIND collection is an explicit evidence-acquisition tool (calendar HTML,
original normalization and source hashes), not an approved recurring quote feed.
Docker/operations facilities do not establish provider authorization. No new
scheduler, Docker service, public API, or automatic job is needed.

## Provider inventory and operational readiness

* KRX daily trading: original implementation supports KOSPI `stk_bydd_trd`,
  KOSDAQ `ksq_bydd_trd`, KONEX `knx_bydd_trd` at
  `https://data-dbg.krx.co.kr/svc/apis/sto/{service}`; `AUTH_KEY`, `basDd`.
  Whole-market responses are unavoidable; only explicitly named instruments are
  ingested. Daily unadjusted KRW integer prices; no intraday feed or revision
  publication chronology is asserted. Today/future final daily bars are rejected.
* Repository request gate enforces >=1 second spacing and <=9500 daily attempts.
  These are **local safety limits**, not independently verified current provider
  entitlements. Published frequency/licensing/redistribution permission cannot be
  established by these limits. Operator service approval and terms review are
  required before a live request; no provider was contacted for this inspection.
* KIND original calendar pages establish session classification under their
  existing verifier, not quote availability. KIND/DART acquisition is excluded.
* Inspection on 2026-10-24: KRX key absent in process environment, present in local
  `.env` (value never printed). Key presence does not prove service authorization.
  Historical user approval for another service/date is not a standing capture grant.
* Header Date, request start and response completion are different observations.
  Neither local UTC clock nor provider-reported Date authenticates publication.

## Configuration, authorization and limits

`ForwardCapturePlan` is immutable `forward_capture_plan_v1`, using the existing
`ExplicitResearchSplit` and canonical `FrozenJSON` fingerprint. Supply an
independently retained plan pin on CLI reads. Training/tuning membership is checked
before opening the archive or making HTTP requests. Final-test, OOS and
final-holdout partitions are rejected. The operator must provide the **original**
reviewed split pin; constructing a replacement split cannot establish custody or
permission to access a previously frozen holdout.

One explicitly named instrument, one weekday, one market per plan. Whole-market
raw pages are retained because that is the original KRX API contract; the existing
collector validates the full page then ingests only the named ticker. Repeating
captures uses new explicit attempt names, not implicit universe/date expansion.

Hard configuration ceilings: 3 total HTTP attempts, 2 MiB retained bytes per
response, 120 seconds cooperative duration, concurrency exactly 1, interval
1–60 seconds, daily local gate 1–9500 attempts. A pilot archive accepts at most 32
capture intents and 256 MiB. Synthetic and provider-origin captures cannot share
the same pilot archive. Raw HTTP streaming stops at the byte bound; an
oversized/disconnected response is an explicitly incomplete prefix and never
parsed or ingested. The HTTP library owns the currently delivered chunk; these
limits are not hard process-memory isolation. Relevant headers have an 8 KiB
bound; over-budget headers produce an incomplete outcome with an explicit reason.

The existing SQLite `RequestGate` reserves daily request slots, including retries.
Retries only apply to transport errors and HTTP 408/429/500/502/503/504, within
the explicit total request count, exponential delay and remaining deadline.
Numeric Retry-After is honored within 60 seconds; unsupported or excessive values
stop the run. No redirect, proxy discovery, automatic scheduler or implicit restart
retry is allowed. Timing limits and cancellation are cooperative; individual HTTP
operations have bounded timeouts, not a hard whole-process kill deadline. Cancel
through the library callback or interrupt the CLI; no unrelated service is stopped.

Live mode additionally needs a nonempty key, service-approval reference,
terms/frequency-review reference and explicit `--allow-network`. These are operator
collection authorizations, **not** independently authenticated PIT or operational
trading approvals. Missing fields return actionable blocked reasons without creating
a pilot DB. The repository cannot independently establish current service approval
or rate/license terms. This implementation performed zero live provider requests.

## Runnable offline pilot

Use the existing `[api]` extra for PR #138's Pydantic-based PIT assessment.
Basic capture/status/verify/readback and the original CLI do not require that extra.
Use a fresh private temporary directory. No network socket or provider is used:

```sh
pilot=$(mktemp -d /tmp/dsv-forward.XXXXXX)
dsv forward-capture offline-pilot --root "$pilot" > "$pilot/result.json"
dsv forward-capture status --root "$pilot"
```

The MockTransport response is explicitly synthetic: one 2025-07-01 KOSPI `005930`
bar, 202 bytes, unadjusted close 105 KRW, volume 10. The controlled local clock and
server Date are 2025-07-02 UTC fixture values, not authenticated historical time.
The actual offline run ingests one row and exports a verified manifest; the
included PR #138 PIT assessment has one scoped source lineage, zero authentic
publication/revision/decision-safe observations and blocked historical execution.
No real historical performance, trading eligibility or profitability is claimed.

Save the original plan and pins from the completed pilot (this is local fixture
bookkeeping; production pins must come from independently reviewed originals):

```sh
python - "$pilot" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
result = json.loads((root / 'result.json').read_text())
(root / 'plan.json').write_text(json.dumps(result['manifest']['plan']))
(root / 'plan.pin').write_text(result['plan_pin'])
(root / 'capture.pin').write_text(result['capture_pin'])
PY
plan_pin=$(cat "$pilot/plan.pin")
capture_pin=$(cat "$pilot/capture.pin")
dsv forward-capture dry-run --root "$pilot" --plan "$pilot/plan.json" --plan-pin "$plan_pin"
dsv forward-capture verify --root "$pilot" --plan "$pilot/plan.json" --plan-pin "$plan_pin" \
  --attempt pilot-1 --pin "$capture_pin"
dsv forward-capture history --root "$pilot" --plan "$pilot/plan.json" --plan-pin "$plan_pin" \
  --attempt pilot-1 --pin "$capture_pin"
dsv forward-capture readback --root "$pilot" --plan "$pilot/plan.json" --plan-pin "$plan_pin" \
  --attempt pilot-1 --pin "$capture_pin" --output "$pilot/response.bin"
```

Readback creates a private 0600 file exclusively; it does not print raw response
bytes or overwrite a destination. Compare its SHA against the original response
receipt. `verify` is read-only and reconstructs the report from original records;
`status` only lists committed/uncommitted local records, explicitly unverified.
`history` validates the supplied pin and shows prior capture pins and raw hashes.
Identical content still gets a distinct response record and raw receipt. Changed
content is a new observation; its capture links prior pins/hashes without claiming
an exchange correction time. Ingestion preserves the original unchanged-bar and
stale-revision rules, including the original collection failure record.

## Authorized real capture (not executed)

Provision a **new private pilot root**, review the original split/scoped date,
service-specific access, allowed frequency and private-use terms. Use the existing
endpoint for the intended daily-trading service, not the security-master service.
Set mode `authorized_provider`, authorization/terms references, and explicit budgets
in the reviewed plan. Keep the KRX key only in the environment or existing private
`.env`. `dsv forward-capture dry-run` does not contact KRX or create the archive.
Only after operator authorization:

```sh
# Variables point to privately reviewed metadata, never protected historical DBs.
dsv forward-capture capture --root "$private_pilot" --plan "$reviewed_plan" \
  --plan-pin "$independent_plan_pin" --attempt "$unique_attempt" --allow-network
```

The pilot cannot decide legal entitlement from a key or local flag. No raw bytes,
keys or real response metadata should be committed or publicly redistributed.
KRX attribution is “한국거래소 통계정보”; original repository guidance requires
review of noncommercial/third-party redistribution rights. KIND recurring capture
and DART requests remain excluded. This change adds no external permission.

## Evidence, recovery and integrity

Original bytes live only in the existing `raw_pages`; `raw_capture_receipts` and
existing export/readback/outbox tooling retain their original semantics. New tables
`forward_intents`, `forward_responses`, `forward_stops`, `forward_manifests` contain
bounded metadata and references, not a second custody store. UPDATE/DELETE triggers
protect these tables against normal application mutation, not a hostile DB owner.

A response includes fixed endpoint/basDd, explicit market/symbol/split scope,
status, allowlisted headers (Date, Content-Type/Encoding, ETag, Last-Modified,
Retry-After), exact retained bytes/hash, request index, deterministic capture ID,
local start/completion UTC times, parser/software version and original ingestion
status/rows/error. Request AUTH_KEY and secret-bearing headers are never recorded.
Transport failures with no response retain an explicitly incomplete empty outcome;
those bytes are not represented as a provider page successfully received.

Capture identity = canonical hash of plan fingerprint + explicit attempt label.
Transport content hash identifies original bytes. Manifest fingerprint covers the
whole canonical metadata projection, original raw receipt, parsed scoped bar and
original ingestion outcome; nondeterministic collection audit finish time and the
collector's temporary UUID are not promoted to decision/publication timestamps.
Start/completion times are recorded observations and remain part of the identity.

SQLite publishes complete manifests in one transaction only after original
collection. Original archive, HTTP metadata and ingestion have separate commit
boundaries: this is **not** a new cross-stage transactional custody system. A crash
between raw insertion and response-journal insertion leaves a detectable orphan;
stop for explicit operator triage, never erase or invent missing metadata. A crash
after a complete response permits offline ingestion reconciliation. A crash after
save but before collection audit completion repeats the original idempotent save.
A crash after audit completion does not repeat ingestion. An intent without a
retained outcome has unknown HTTP status: explicitly start a new attempt instead
of retrying the old request. A completed attempt requires its separately retained
pin for reuse and never repeats HTTP. Exclusive process locking releases on
failure; interrupted records remain visible. This is bounded local recovery, not
durable authenticated process-crash custody.

Fresh verification checks plan/attempt binding, contiguous response indexes,
original bytes and raw receipt, exact dates/scopes/timestamps, original parser,
original ingestion audit and current cleaned projection against original linked
bars. Later price revisions do not invalidate earlier immutable captures. Modified
or missing bytes, forged manifests (even self-rehashed), source/ingestion mismatch
and partial publication fail closed. A malicious owner replacing every local
record cannot establish authenticity; retain the original manifest/plan pins
outside the mutable archive. Filesystem-level backup/retention remains operator work.

## PIT boundary and next work

`assess_capture_pit` validates original scope before I/O, verifies the capture, then
calls the unmodified PR #138 assessment with the original archive and scope pin.
It intentionally supplies no invented publication/custodian authority. Server Date
is retained only as provider-reported HTTP metadata; local capture time maps only
to existing local retrieval evidence. Publication, authenticated timestamp and
strategy-safe time remain null. No synthetic timestamps are submitted as real
availability claims. Original Corporate Actions, Runner Tail, Run Input Freeze,
strict PIT and frozen OOS/holdout gates stay blocked/unchanged.

The existing Trading Core lacks a KONEX Instrument; capture supports the original
KRX KONEX parser, but this pilot's PIT Instrument bridge supports KOSPI/KOSDAQ only.
Existing Prometheus/Grafana monitors itself, not application collection metrics;
this pilot reuses structured collection logging and adds capture ID/index/byte/pin
events without claiming new dashboards or a production telemetry integration.

Next steps: independently approve service use/frequency and source identity;
perform one separately authorized private capture; retain external authenticated
custody evidence with an approved verifier; establish clock/correction chronology,
retention/backup, calendar and corporate-action completeness. Forward retrieval
alone cannot retroactively qualify the existing historical dataset. No strategy,
model, paper order, API publication or live operation is activated here.

## Local validation record

The deterministic fixture retains raw SHA-256
`54cad52043093d6ddbeb25b8c93b681576fb42ee78fb6a732af661725b81258a`.
Repeated fixture runs in separate pilot archives produce identical manifest bytes
and pins. The installed-wheel smoke exercises CLI capture, original archive and
ingestion, verify/history/readback, completed-attempt reuse, PR #138 assessment
and deliberate raw-byte tampering rejection. Zero external financial requests
were performed. Local regression scope excludes the complete offline suite,
which remains assigned to existing self-hosted CI; CI status is not queried.
