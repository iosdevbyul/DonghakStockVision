# KOSPI full-market recurring read-only policy

This adds an explicit operating scope, not a scheduler or another collector. No live
request was executed to implement or test it. The existing operator confirmed service
entitlements remain: daily trading information from **2026-09-29 through 2027-09-28**,
and basic stock information from 2026-10-16 through 2027-10-15. Only `stk_bydd_trd`
is covered here. Service approval is not missing merely because a new project policy
has not yet been configured.

## Three separate boundaries

1. KRX service entitlement: issued key, individually approved daily-trading service,
   validity and applicable usage terms. Existing supplied evidence consists of an
   approval screenshot, request example screenshot, developer specification DOCX and
   operator-copied terms RTF. Original document-byte hashes matched both retained
   daily-service operator packages during this work. Their recorded approval period
   matches the operator-confirmed dates above. Local records/screenshots are not a
   cryptographic provider attestation. The copied RTF is not a provider download.
2. Old `operator_read_only_pilot_v1`: exactly one 005930 request, at most 24 hours.
   Its private packages, plan v1/v2, raw responses, failed ingestion, recovery and pins
   retain their meanings. The previously captured market-wide response is **not**
   retroactively approved for full-market import.
3. New `operator_full_market_recurring_v1`: a separately named, explicitly reviewed
   prospective full-market **private, read-only** scope. It uses the same owner-local
   original-document review trust basis. It is neither independent Ed25519 review
   nor a new KRX entitlement. Existing `independent_review_v1` is unchanged and cannot
   be silently reinterpreted as this policy.

The operator must prepare and retain a new private recurring policy and independent
canonical pin after reviewing the full-market scope. This PR does not issue a real
operator policy or copy/overwrite any private evidence. No new service approval or
replacement approval screenshots are requested.

## Plan and policy contracts

`forward_capture_plan_v3` has no ticker, historical split or research partition in
its wire document. Its `full_market_scope` is `kospi_full_market_scope_v1`:

| Field | Required meaning |
|---|---|
| `service_id`, `method`, `endpoint` | `stk_bydd_trd`, `GET`, `https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd` |
| `market`, `scope` | `KOSPI`, `full_market` |
| `valid_from`, `valid_until` | Canonical UTC instants; start inclusive, end exclusive; at most 366 days |
| `permitted_days` | Sorted unique explicit ISO trading-date allowlist, 1–366 entries; weekdays from at most seven days before window start through its end |

The allowlist is an operator-reviewed collection schedule, **not** independently
authenticated exchange-calendar evidence. Weekday validation alone does not prove a
trading session. Verify actual sessions against available authoritative calendar
evidence before including dates; an empty/error response is not a holiday proof.
No default weekday calendar or implicit date-range expansion creates approval.

The plan also contains `provider=krx`, `currency=KRW`, `market=KOSPI`, requested `day`,
`mode=authorized_provider`, `authorization_reference`, `terms_reference`, and the
existing budget fields. Each request has `max_requests=1`, concurrency 1, zero
application retries, at most 30 seconds and 2 MiB retained/decoded bodies. HTTPX's
wire/already-decoded distinction and existing bounded gzip parser remain authoritative.
Internally the existing plan owner uses an empty non-instrument ticker only for v3;
it is never exposed as an instrument, used for orders or stored as a normalized row.

A separate private package uses the existing `permission.bin`, `terms.bin` and
`specification.bin` originals/copies and **`recurring-policy.json`**. Do not rename
or modify an old `operator-policy.json` into the new authorization. Keep trusted pins
outside the mutable package. The exact recurring policy JSON fields are:

```text
version = operator_full_market_recurring_v1
operator_id
acknowledgement = private_operator_review_not_independent_authorization
scope_acknowledgement = explicit_full_market_recurring_read_only
reviewed_at, valid_from, valid_until
approved_from, approved_through
service_id = stk_bydd_trd; method = GET; endpoint = exact endpoint above
permission, terms, specification = {issuer: KRX, source_url, sha256}
capture_root = one canonical, private absolute archive root
transport = provider_https | mock_fixture
document_origin = operator_reviewed_source_copies | operator_supplied_provider_originals
                  (synthetic_test_fixture only with mock_fixture)
max_observation_lag_days = 1..7
credential_sha256 = existing private credential-binding digest
uses = {private_capture: true, forward_signal: false, paper: false}
full_market_scope = exact scope document described above
daily_limit = 1..min(9500, verified_service_daily_limit)
verified_service_daily_limit = 1..10000
max_response_bytes, max_seconds, interval_seconds = exact plan budget values
```

Hashes must be computed from authentic retained bytes using the existing canonical
`FrozenJSON` and credential-binding contract, not invented from this template. No
secret, private digest, original grant or private path belongs in Git or a PR. Existing
original-byte checks reject missing, changed, empty or error-page evidence.

The review must precede the actual capture, not the bar's trading date. An explicitly
listed recent completed session can be collected at policy start, under the same
1–7 day lag used by existing forward capture. This never proves historical publication
time or authorizes reuse of a capture made before the policy review. Policy and scope windows must
match and remain wholly within service approval. The requested day must be allowed,
within approval and 1–7 days before the actual local Seoul capture date (or a smaller
configured lag). Every allowed date must also be within service approval. Dates outside that scope fail closed; historical final-test/holdout
payloads and old pilot responses cannot be relabelled. All persisted instants use
UTC; requested trading dates and the daily quota boundary use Seoul dates.

### One policy, separate deterministic daily plans

No new evidence package is needed for every allowed date. From one independently
pinned policy, construct each daily `ForwardCapturePlan` with `market="KOSPI"`,
`ticker=""`, `partition="forward_market_observation"`, `split=None`, `split_pin=None`,
`observation_scope=None`, the exact `FullMarketScope.from_dict(policy["full_market_scope"])`,
requested `day`, the policy budget values, `max_requests=1`, `mode="authorized_provider"`,
`authorization_reference=policy.identifier` and `terms_reference=policy["terms"]["sha256"]`.
Serialize `plan.document` and retain its canonical identifier separately. Never derive
trust solely from a hash stored alongside an untrusted input. Use exclusive private
file creation when retaining each plan; do not overwrite prior plans or pins.

## Offline preview and controls

All variables below refer to already configured private files and separately trusted
pins; these examples contain no real package or credential values. The existing CLI
uses `KRX_API_KEY` privately and sends it as `AUTH_KEY` only on a separately authorized
capture. Preview validates the binding without printing the key or its digest.

```sh
dsv forward-capture authorization-readiness \
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" --attempt daily \
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \
  --authorization-policy operator_full_market_recurring_v1

dsv forward-capture dry-run \
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" --attempt daily \
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \
  --authorization-policy operator_full_market_recurring_v1
```

Both emit `krx_recurring_preview_v1`: endpoint/basDd, full-market classification,
service expiration, policy window, integrity/credential checks, configured and reviewed
service budgets, local usage/remaining allowance, duplicate/paused status, blockers and
`ready_for_explicit_read_only_capture`. They create neither an archive nor operational
DB and make zero network requests. An absent policy, wrong service/endpoint/key,
expired approval, unlisted/future/stale day or corrupted local state cannot be ready.
The preview is not provider approval, a request reservation or permission to trade.

For the same valid plan/pin/policy arguments, replace the action with
`recurring-pause` or `recurring-resume` to append operator control state. These are
explicit offline writes to the existing capture archive, never the operational or
research DB. No records are deleted. The capture lock serializes control with capture:
pause takes effect at job boundaries; it does not claim hard interruption of an
already executing HTTP request. Failed lock acquisition is explicit. Resume neither
resets counters nor removes duplicate markers and still requires valid policy evidence.
Completed capture replay/readback remains possible without new HTTP or quota spending;
use existing verify/readback and original trusted pins after policy expiry.

## Quota and duplicate semantics

The provider limit confirmed by the operator is 10,000 requests per key per day, an
upper bound rather than a target. The existing project `RequestGate` retains its
stricter 9,500 ceiling. Recommend `daily_limit=1`; use a small explicitly reviewed
higher value only for multiple permitted completed dates. Its durable `request_budget`
table is reused, with the KST 00:00 day boundary and original spacing gate.

The gate reserves usage **before** HTTP, including failed/uncertain attempts. There
is no automatic refund or application/transport retry. One capture archive and its
file lock bind the reviewed credential/scope. A second attempt or changed policy for
an already reserved observation date is rejected. Concurrent submissions cannot
create two requests. Same completed identity plus its independently trusted resume
pin replays without another call, including while paused or after the local budget
is used. An interrupted intent without a trustworthy recorded outcome remains
blocked; it is not safe to assume no request happened. Original raw receipts and
failure states remain immutable.

Counters cover this archive only. They do not prove that another application,
another archive or another machine has not used the same key. Do not create another
archive to evade budgets or duplicates. If KRX responds with a limit/service error,
the bounded attempt is retained and stopped; no retry or new attempt is authorized
by this policy. Provider revocation, changed terms and independently changed quota
conditions still require operator review and pause; offline local hashes cannot
automatically discover those changes.

## Capture to operational storage

A future explicitly authorized invocation uses the existing capture command with
this policy, explicit network opt-in and explicit operator confirmation. Nothing in
this PR starts collection. The existing direct TLS/hostname checks, endpoint allowlist,
no redirects/proxies/hooks, zero transport retries, timeout and raw streaming limits
remain unchanged. Mock transport is explicitly labelled and cannot be invoked as a
genuine provider request through the production CLI.

Full-market sources use `forward_capture_manifest_v2`. It semantically reconstructs
all validated original response rows, rejects duplicate identities and records
`normalization=operational_store_only`. It does not insert full-market data into the
pilot's legacy `bars` table; its `ingestion` remains empty. Original raw receipt v1,
response metadata and immutable capture/binding storage are reused. A new full-market
archive cannot be mixed with a legacy pilot archive, in either direction.

After a separately approved capture, verify and retain its original capture pin, then:

```sh
dsv market-storage ingest-capture --root "$OPERATIONAL_ROOT" \
  --capture-root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \
  --attempt daily --capture-pin "$CAPTURE_PIN" \
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \
  --authorization-policy operator_full_market_recurring_v1
dsv market-storage validate --root "$OPERATIONAL_ROOT"
```

The bridge verifies the original full-market policy/binding at recorded capture times,
then uses the existing atomic, idempotent full-response SQLite ingestion. Receipt and
source references remain linked through the existing batch v2. This is not a new
ingestion engine. Original pilot policies still reject extra tickers. Operational
PIT/training/OOS/signal/PAPER/execution flags all remain false, even with genuine HTTP
origin. No strategy, signal, PAPER or broker behavior is activated.

## Bounds, verification and remaining work

Recurring archives allow up to 366 capture intents; old pilot archives retain 32.
Existing 256 MiB archive read limits remain, with conservative write headroom checks
for recurring captures. Storage can stop before a year if responses are unusually
large. Operators must review capacity/retention before configuring real collection;
there is no automatic archive rotation, quota reset, deletion or backup claim. Limits
are cooperative and file-based, not hard process isolation or independent custody.

Offline tests cover plain and wire-gzip 940-row full-market captures, source replay,
operational SQLite integrity, one policy across 33 dates, concurrency, idempotent
completion, quota reservations, HTTP 429 stopping, pause/resume, modified originals,
wrong service/scope/credential, expiration and old-pilot rejection. These use explicitly
synthetic documents and `MockTransport`, never authentic permission fixtures or real
market-data requests. Existing architecture checks and legacy contracts remain active.

Before real operation, the operator must configure the new explicitly reviewed private
policy, the permitted session dates, stable archive/root, original-document references,
current credential binding and separately retained pins, then pass offline preview.
The already confirmed KRX service approval is not replaced or denied. A background
scheduler, cross-application quota accounting, independent custody and downstream
research qualification remain separate future work.

## Optional macOS execution

The [daily scheduler guide](krx-daily-scheduler.md) describes the separate disabled-by-default
per-user LaunchAgent and one-shot runner. It reuses this policy and original capture/SQLite
verification. It does not issue a real private policy, perform a live request in development,
relax the observation lag or authorize signals/PAPER. Installation and enablement require
explicit later operator actions.

## Future higher budgets and longer operation (no policy issued here)

`daily_limit` is already configurable in a newly reviewed policy. For example 100 is
valid when it is no greater than both `verified_service_daily_limit` and the project's
9,500 ceiling. The operator-confirmed KRX ceiling is 10,000/key/day. A higher project
limit does not authorize extra services, dates, retries or known duplicate observations;
`run-due` still limits one batch to seven work items and each request to one attempt.
Only a few sessions can fit in the existing seven-calendar-day lag, so 100 is an upper
budget choice, not a target or a promise of 100 historical recoveries. Choose the
smallest practical reviewed limit. Existing issued policies and pins are immutable.

For a long-term policy, explicitly review operator identity, retained authentic/copy
source classifications, exact daily-trading endpoint, private-only uses, credential
binding, original document hashes and a sorted session allowlist. The UTC start/review
and exclusive end must match the full-market scope, span at most 366 days and remain
within the confirmed service validity **2026-09-29 through 2027-09-28**. The latest
possible exclusive end for that existing approval is **2027-09-28 15:00 UTC** (2027-09-29
00:00 KST). Do not extend provider entitlement by extending project JSON timestamps.
Every permitted observation date must also fit service validity and the original scope
rules. Never generate an approved calendar from weekdays or invent approval provenance.

Create a distinct private policy package, independent canonical pin and pinned config
only after the operator's explicit scope review. Preserve the old packages, copied
terms provenance, pins and raw captures. Reuse the same canonical capture root and
operational store to preserve local request counters, pauses and duplicate guards;
creating a new archive to evade these is prohibited. A new `daily_limit=100` does not
reset a previously used request. New runner metadata is bound to the new config; use
explicit original-pin completion registration to carry verified source history forward.

The existing approval remains real operator-confirmed history, not missing service
permission. No independent Ed25519 signature is required by this separately named
owner-local policy, and the independent-review path is not weakened or reinterpreted.
Provider revocation/terms changes still require operator review and pause. There is no
new approval authority, automatic renewal, daily reapproval, external submission or
real-provider request in this development work. See the [scheduler guide](krx-daily-scheduler.md)
for offline gap inspection, registration, batch limits and later explicit rollout.
