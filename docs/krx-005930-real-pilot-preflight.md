# Phase 7-A: KRX 005930 offline acquisition and readback pilot

This is a **preflight and durable-original-evidence verification slice**. It does
not issue a KRX request, create a credential, deploy a scheduler, approve trading,
or treat archived/synthetic observations as new live market data.

## Existing architecture and verified implementation boundaries

- `KRXProvider` targets the official daily KOSPI `stk_bydd_trd` GET endpoint
  `https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd`; credential is supplied
  privately as `KRX_API_KEY` and sent in `AUTH_KEY`, with `basDd=YYYYMMDD`.
- One KOSPI endpoint request returns a **full-market `OutBlock_1` response**;
  it cannot silently become an endpoint authorized only for 005930. The first
  *selected observation* is `005930`, and all original response bytes are
  retained. KOSPI `MKT_NM`, `BAS_DD`, `ISU_CD` and OHLCV fields are parsed
  with the existing strict KRX response validator.
- `forward_capture.run_capture` bounds status, request count/timeout, bytes,
  predeclared plan and immutable receipt; `readback` and `verify_capture`
  replay original bytes and the pinned original source identity.
- `operational_capture.ingest_capture` validates original capture authorization
  and **the entire source response before a database write**. Legacy single-
  ticker grants cannot admit unrelated full-market rows. Only the separately
  reviewed full-market recurring authorization permits all-market storage.
- `OperationalMarketStore` persists raw bodies, receipts, ingestion batch
  references, exact source rows, normalized prices and capture metadata in an
  independent SQLite store. Transactions, immutable evidence triggers, idempotent
  retry, conflict rejection, read-only reopen, batch semantic verification and
  schema/database integrity validation are already implemented.
- `krx-scheduler preflight` rechecks original recurring source permission, local
  credential presence (without output), binding, reviewed endpoint, request
  budget, scheduler/pending state, archive permissions, storage health and
  Asia/Seoul host timezone. It does not reserve a provider-wide quota.

The existing private operational records described in
[KRX production readiness](krx-production-readiness.md) include a retained
service approval covering 2026-09-29 through 2027-09-28 and operator-copied
terms describing 10,000 requests per key/day. **These are local retained
copies, not proof of current live provider-side entitlement or remaining quota**.
The repository cannot independently inspect the provider's account-wide usage.
No new permission is inferred from screenshots or fixtures.

## New 005930 operator command

```sh
dsv krx-scheduler pilot-preflight --config "$CONFIG" --config-pin "$CONFIG_PIN"
```

The action is read-only and never accepts `--allow-network`, `--execute`,
`--scheduled`, `--apply` or `--confirm-operator-authorization`. It
reconstructs the existing production preflight rather than accepting an
operator-supplied preflight summary.

A second invocation can link **previously collected, already ingested**
original capture evidence, with original, independently retained pins:

```sh
dsv krx-scheduler pilot-preflight \
  --config "$CONFIG" --config-pin "$CONFIG_PIN" \
  --source-plan "$PLAN" --source-plan-pin "$PLAN_PIN" \
  --source-attempt "$ATTEMPT" --capture-pin "$CAPTURE_PIN" \
  --batch-pin "$BATCH_PIN"
```

For a separately retained exact original authorization owner rather than
the configured owner, add both `--source-authorization-root` and
`--source-authorization-pin`. All source pins must be provided together; no
untrusted fallback or partial evidence is accepted. The stored audit
reconstructs original capture, review authority, immutable batch manifest,
complete original raw body and DB rows, then verifies exact
`(KOSPI, trading_date, 005930)` membership in the independently pinned
batch. It emits original references but no original bytes, secrets or
filesystem paths. An absent/altered original fails closed.

The native preflight may report
`ready_for_manual_provider_capture=true` after local policy checks, but the
**pilot report's `live_pilot_authorized` is always false**: this command
does not give the separate, specific approval to contact KRX. It also retains
`genuine_forward_evidence=false` and `performance_approved=false`.
`configured_provider_ready_for_separate_review` is only an offline review
candidate, **not** a live-call grant.

## Explicit authorization and first live 005930 pilot prerequisites

Before a future authorized run, obtain/reconfirm:

1. Current original KRX service authorization for **KOSPI daily
   `stk_bydd_trd`**, `GET`, the full-market response scope and permitted
   private use, including exact raw-body retention and any redistribution ban.
2. Approved, private existing KRX key and independently pinned authorization
   package; verify it through the existing signed reviewer or operator-approved
   full-market original policy. Do not copy the key into logs, JSON, URLs or Git.
3. One exact Seoul trading day, a verified session/calendar and sufficiently
   delayed completed observation, explicit `basDd`, request budget, maximum
   one request, zero retries, concurrency one, 30-second timeout and at most
   2 MiB of retained original bytes. Validate the provider-wide remaining
   allowance separately; local counters are not authoritative.
4. Separate operator confirmation authorizing **that endpoint, completed
   market date, credential, full-market response scope and maximum cost**.
5. Private archive and operational DB roots and sufficient storage capacity;
   preserve the protected historical research dataset untouched.

**This PR supplies none of those external approvals.** No call may occur
until the user explicitly authorizes the bounded pilot after reviewing actual
preflight evidence.

## Safe post-authorization procedure and stop conditions

Only after the separate operator authorization: use the existing original
capture CLI with its exact approved recurring plan and `provider_https`
transport, retain the manifest/receipt, inspect status and the unmodified raw
body, and run the existing `ingest_capture`/market-storage workflow.
Rerun `pilot-preflight` with separately retained capture and batch pins to
prove local, source-linked 005930 persistence. Reopening SQLite and
`market-storage validate` confirm all retained batch digests and memberships;
they do not prove provider-side publication time. The original `OutBlock_1`
may contain other symbols; a single-ticker grant is never permission for
all-market persistence.

Stop immediately for missing/changed grant or terms, expired approval, wrong
endpoint/scope, unknown remaining quota, any response too large, HTTP/rate
limit error, empty/malformed/stale data, unexpected ticker/date/market,
uncertain or interrupted transaction state, conflict with immutable original,
receipt mismatch, duplicate request uncertainty, or DB integrity failure.
Use existing pending-capture/recovery audit before considering any retry.
Never recover by silently using a fixture or overwriting an original.

## Test boundaries

```sh
pytest -q tests/integration/test_krx_005930_pilot.py
pytest -q tests/integration/test_operational_capture.py
pytest -q tests/integration/test_krx_pilot_readiness.py
pytest -q tests/integration/test_krx_production_readiness.py
pytest -q tests/architecture/test_boundaries.py
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

New tests exercise synthetic KOSPI `005930` original-capture to transaction
storage, independent batch pin and original readback, immutable no-write
replay, wrong pins/source owner, forbidden implicit synthetic mode, provider
approval inference and preflight behavior with network prohibited. Existing
capture suites cover malformed date/symbol/schema/market, HTTP and quota
failure, stale observations, interrupted writes, conflicts and idempotency.
Their presence does not mean tests have passed this branch.

Python 3.12/3.13/3.14 protected CI, installed wheel regressions, protected
data SHA-256 checks and full offline suite must be confirmed before merging.
The real live observation, authorized transport, retention permissions and
external chronology remain **unverified** after this PR.

## Production execution-path E2E — Phase 7-A handoff

The offline integration test
`test_production_scheduler_005930_original_capture_to_verified_database_readback`
now uses the unchanged **production** `krx_scheduler.run_once(execute=True,
client=httpx.MockTransport(...))` path. It verifies the exact outgoing GET
`https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=YYYYMMDD`,
`AUTH_KEY`, a representative two-instrument KOSPI `OutBlock_1` response
including `005930`, the **original byte-for-byte HTTP response**, its receipt
SHA-256, pinned original capture, SQLite transactional batch, reopened read-only
observation storage and `audit_stored_005930`. Repeated completion of the
same observed date cannot generate another HTTP call; original archive bytes
remain unchanged. This uses a synthetic reviewed operator package and **does
not verify current provider-side entitlements**. Two additional E2E tests
reject a verified batch lacking 005930 and resume an interrupted ingestion
without another request.

### Important production handoff distinction

The scheduler's `run_once(execute=True)` is intentionally guarded by a
persistently enabled scheduler, recurring approval, original private state and
pending-capture controls. **Do not turn on recurring scheduling merely for
the first manually authorized pilot.** The existing bounded manual capture
CLI followed by the existing storage CLI is the correct future *single
request* workflow. Never run the following live section until a separate
explicit user authorization covering endpoint, full-market response scope,
completed date, credential, expected budget and applicable costs.

### Preflight and review — no network

Obtain a current account-side entitlement confirmation for `stk_bydd_trd`
and verify original private permission/terms/specification files against the
existing pinned recurring-policy owner. Confirm private-use and original-body
retention rights, service validity, market/calendar, endpoint, and the
provider-wide remaining request count; local counters alone cannot inspect
requests from other clients. Check that the existing private credential
is present and bound using preflight; **do not print its value or hash**.

```sh
dsv krx-scheduler preflight --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv krx-scheduler pilot-preflight --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv krx-scheduler status --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv market-storage validate --root "$OPERATIONAL_ROOT"
```

Inspect `selected_trading_date`, `endpoint`, `parameters.basDd`,
`requests_maximum=1`, `retries=0`, `concurrency=1`,
`max_seconds<=30`, `max_response_bytes<=2097152`, credential binding
and local quota. A read-only preflight can pass its local checks and still
**cannot independently authorize a live request**. Do not infer an allowed
session merely from weekday checks. No synthetic response can close this gate.

Use the existing reviewed pinned-plan procedure in
[KRX production readiness](krx-production-readiness.md#prepare-the-daily-plan-without-network-access).
Verify the selected completed Seoul market date and the manual `manual-first`
plan's independent pin before any request. Then inspect the original authorization:

```sh
dsv forward-capture authorization-readiness \\
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \\
  --attempt manual-first \\
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \\
  --authorization-policy operator_full_market_recurring_v1
dsv forward-capture dry-run \\
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \\
  --attempt manual-first \\
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \\
  --authorization-policy operator_full_market_recurring_v1
```

### Future operator-approved capture — DO NOT EXECUTE UNTIL SEPARATELY APPROVED

The next command issues a network request **only when authorized**. It is
included here for controlled handoff, not executed or activated by this PR:

```sh
dsv forward-capture capture \\
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \\
  --attempt manual-first \\
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \\
  --authorization-policy operator_full_market_recurring_v1 \\
  --allow-network --confirm-operator-authorization
```

Persist the original returned `CAPTURE_PIN` separately. If HTTP status,
body length, source identity, permission, date, data schema or request
completion is uncertain, **STOP**; preserve original failed response and
request counter, do not retry or reset state. A timeout/429 may have used
the only permitted request. Do not substitute a fixture or an older archive.

### Offline ingest/readback after authorized original capture

With exact original `PLAN_PIN` and `CAPTURE_PIN` previously retained:

```sh
dsv forward-capture verify \\
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \\
  --attempt manual-first --pin "$CAPTURE_PIN"
dsv market-storage ingest-capture --root "$OPERATIONAL_ROOT" \\
  --capture-root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \\
  --attempt manual-first --capture-pin "$CAPTURE_PIN" \\
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \\
  --authorization-policy operator_full_market_recurring_v1
dsv market-storage validate --root "$OPERATIONAL_ROOT"
dsv market-storage status --root "$OPERATIONAL_ROOT"
dsv market-storage duplicates --root "$OPERATIONAL_ROOT"
```

Independently retain the successful ingestion's `BATCH_PIN` from the output.
Run the existing source-linked **read-only** pilot audit with all original
pins; it fails for missing 005930, wrong batch/source, tampered raw bytes
or invalid reviewed authorization:

```sh
dsv krx-scheduler pilot-preflight \\
  --config "$CONFIG" --config-pin "$CONFIG_PIN" \\
  --source-plan "$PLAN" --source-plan-pin "$PLAN_PIN" \\
  --source-attempt manual-first --capture-pin "$CAPTURE_PIN" \\
  --batch-pin "$BATCH_PIN"
```

A successful local audit is **not** independent authentication of a real KRX
publication. The origin and capture timestamp remain locally asserted.
Retain original source bytes and independent pins under approved data-rights
and custody constraints. Never set genuine-forward, PIT, performance, paper
or brokerage eligibility from this replay. Do not install/resume the recurring
scheduler; any future recurring operation requires independent authorization.

### Verification gates

```sh
pytest -q tests/integration/test_krx_scheduler.py \\
  tests/integration/test_krx_005930_pilot.py \\
  tests/integration/test_krx_pilot_readiness.py \\
  tests/integration/test_operational_capture.py \\
  tests/integration/test_operational_market_storage.py \\
  tests/integration/test_krx_recurring.py \\
  tests/integration/test_krx_production_readiness.py \\
  tests/architecture/test_boundaries.py
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

Check GitHub Python 3.12/3.13/3.14 required CI, wheel regression and protected
dataset/database SHA-256 separately. The code change provides tests; it does
**not** claim a local run, a protected hash comparison, a production
entitlement, a live KRX capture or a real persisted 005930 observation.
