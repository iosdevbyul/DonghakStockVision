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
