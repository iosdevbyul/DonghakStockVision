# Separately approved historical KOSPI acquisition

This is a historical acquisition path, not a relaxed forward capture policy.
`ForwardCapturePlan` v1/v2/v3, `FullMarketScope`, and the existing recurring
policy retain their original windows, identifiers, permissions and lag limits.
The active daily LaunchAgent/configuration is neither installed nor changed here.
No live request or private policy issuance is performed by importing these modules.

## Reused components and boundaries

`KRXProvider.fetch_market(day)` builds the original `stk_bydd_trd` request.
`CaptureHTTP` supplies the existing bounded streaming recorder (one attempt,
30 seconds maximum, 2 MiB, no retry); the shared original TLS/client checks reject
proxy discovery, redirects, cookies, default query arguments, hooks and retrying
HTTP transports. Test transports require the existing explicitly synthetic source
policy plus a separate fixture acknowledgement. They never become real evidence.

The archive uses `SQLiteStore`'s original `raw_pages`, `raw_capture_receipts` and
`archive_raw_page`. `prepare_raw_source_custody` verifies exact bytes and original
receipt ownership. Additional append-only tables retain plans, unique date intents,
HTTP metadata and completion reports. This is not a second normalized market DB.
`OperationalMarketStore.ingest`, original parsing, SQLite transactions, duplicate
checks, `_verify_batch` readback and `validate` persist and verify the same source.
The small `operational_backfill_reference_v1` variant distinguishes this owner
from forward capture; old reference bytes/verification remain compatible.

Research/source databases are read-only inventory inputs, not destinations.
The new policy's inventory hash does not replace any Phase 2 model/snapshot pin.
Final Test payloads, models, journals and split definitions are untouched.
No training, forward prediction, PIT, performance or trading approval is granted.
All approval flags remain false, and performance metrics remain null.

## Operator-reviewed policy, separate from existing private records

Only use merged, reviewed code in production. Retain the existing configuration,
service evidence, daily policy, independent pins, archive and database unchanged.
Prepare a NEW private policy file and a NEW independent trusted-pin directory.
Policy/plan files must be mode 0600; output/pin directories must be mode 0700.
Nothing overwrites an existing policy, pin or plan file.

The policy fields are:

| Field | Required review |
|---|---|
| `version` | `operator_krx_historical_backfill_v1` |
| `acknowledgement` | `private_historical_acquisition_not_forward_authority` |
| `reviewed_at`, `valid_from`, `valid_until` | Canonical UTC, start <= actual review < exclusive end; <=31-day execution window. `pin-policy` requires the actual review within 60 seconds; never backdate it. |
| `start`, `end` | Explicit inclusive historical dates; <=366-day difference, no earlier than 2010-01-04; end before the current KST date. First requested scope: 2026-10-02 through 2026-11-11. |
| `scheduler_config`, `scheduler_config_pin` | Original canonical private config and separately retained fingerprint; source credential/document bindings are reused. |
| `research_database`, `research_database_sha256` | Explicit read-only inventory and its reviewed current hash; does not re-pin a training snapshot. No WAL/hot-journal source; <=2 GiB. |
| `archive_root` | NEW separate canonical private directory for historical originals and request records. Cannot overlap research, forward archive, policy, runner or operational storage. |
| `source_pins_root` | Existing separate 0700 directory outside the archive for exclusive original-response seals. Never discard/reissue a missing original seal automatically. |
| `calendars` | Up to 13 `{raw, source, normalized_pin}` entries. Raw and source are canonical paths; use genuine retained official KIND month HTML and source metadata. Existing `CalendarMonthSource`/`normalize_month` must verify every day in the requested range. No weekday-only expansion, missing month or unknown holiday annotation is accepted. |
| `max_requests` | Explicit total request reservation ceiling, 1..366 across all plans for this policy. Failed/unknown intents consume that ceiling; no refund. |
| `daily_limit` | Explicit bound on known local request usage before backfill; at most 9500 minus reserved daily requests, and below the retained service ceiling. This is not an account balance. |
| `reserved_daily_requests` | At least the existing scheduler's daily limit; >=1. |
| `min_rows`, `required_tickers` | Reviewed positive response coverage floor <=10000 and 1..25 sorted unique six-digit symbols (initially include 005930). Do not invent a historically constant listed universe. These checks do not prove the provider omitted no other symbols. |
| `known_request_ledgers` | Explicit canonical paths to all other known account request-budget DBs, including earlier backfill archives. The research DB, original daily capture DB and current backfill archive are always included/deduplicated. Earlier retained originals in declared ledgers are also skipped. |

The original service approval period is checked against the actual request time,
not misapplied to the historical observation date. Original permission/terms/spec
bytes and key binding remain mandatory. The separate reviewed historical date
range supplies acquisition scope; it supplies no independent source authentication.

[KRX's official service list](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)
describes KOSPI daily history from 2010-01-04. That does not guarantee every date's
availability, current quota, publication time, or authorize wider uses.

## Existing CLI integration

Use the actual approved paths in these variables. Pin **files**, not their values,
are passed to the CLI; JSON output excludes policy/plan/capture/batch pin values.
Policy and plan pins must live outside their document directories and the archive.
The first command records an explicit local operator review, not a provider or
independent cryptographic signature. It never creates credentials or signing keys.

```sh
dsv krx-backfill pin-policy --policy "$POLICY" \
  --policy-pin-file "$POLICY_PIN_FILE" --confirm-operator-authorization

dsv krx-backfill prepare-plan --policy "$POLICY" \
  --policy-pin-file "$POLICY_PIN_FILE" \
  --plan "$PLAN" --plan-pin-file "$PLAN_PIN_FILE"

dsv krx-backfill inspect --policy "$POLICY" \
  --policy-pin-file "$POLICY_PIN_FILE" \
  --plan "$PLAN" --plan-pin-file "$PLAN_PIN_FILE"

# Only after merge and review; start with two dates, one request/date, zero retries.
dsv krx-backfill run --policy "$POLICY" --policy-pin-file "$POLICY_PIN_FILE" \
  --plan "$PLAN" --plan-pin-file "$PLAN_PIN_FILE" \
  --execute --allow-network --confirm-operator-authorization --max-dates 2
```

The plan selects official sessions newest first and skips any existing research
original (including a zero-row response), research bars, operational rows or forward
original. It retains separate skip reasons. October 26 must be skipped; October 2's
original must remain untouched. Calendar classification and a zero-row response are
separate facts: an empty response alone does not establish a holiday or its cause.
An unknown official calendar annotation blocks planning instead of being guessed.

After successful inspection of the first small batch, use the SAME plan/pins with a
larger explicit `--max-dates` (up to 366, within the policy's total ceiling). Batches
stop cooperatively at `--max-seconds` (default 120, maximum 300); inspect and continue
only remaining dates. Completed dates are reverified without new requests. Do not
loop after nonzero exits or failed/no-data outcomes. This CLI creates no scheduler.

## Budgets, failure and recovery

The existing daily runner's authoritative `.run.lock` excludes concurrent daily or
historical execution. Backfill refuses the 30-minute window on either side of the
configured weekday schedule. Existing LaunchAgent/control files are not changed.
Known counters are summed before each reservation; the original `RequestGate`
persists actual local allocations and spacing. Account-wide remaining quota is
always `UNKNOWN`: other hosts/apps can consume requests concurrently, and a local
ledger snapshot is not an atomic provider account reservation.

Each unique date intent commits before HTTP. Original bytes, metadata and receipt
commit together; an independent source seal follows. A 403/429, timeout, size error,
malformed/wrong-date market response, coverage failure or valid zero-row page stops
the batch without retry. `no_data` is distinct from `failed`. Both are retained;
repeating the same plan stops at that date and never repeats its HTTP request.
Failure/no-data runs exit nonzero. With explicit operator authorization,
`--continue-date-failures` proceeds past isolated empty/invalid/coverage-failed dates
and skips their retained outcomes on replay; it never repeats their HTTP requests.
HTTP/transport failures (including 403/429/timeouts), uncertain outcomes, and source
integrity errors still stop execution. Without this flag the original stop behavior
is unchanged. Inspect and review before any newly approved plan;
retain the same archive and include prior archives in known ledgers.

A committed response/seal with interrupted ingestion can resume with the SAME
policy/plan/pins and `run --execute --max-dates 1`, WITHOUT `--allow-network` or
credentials. SQLite ingestion is atomic/idempotent: a crash after SQL commit but
before completion causes source-verified readback, not another insert/request.
An intent without a response is an unknown outcome: preserve it, block replay,
and investigate. A missing seal is also blocked; never reconstruct a supposedly
independent seal, erase evidence or refund request counters to force another call.

Local archive/receipt/response seals and stored row replay prove local consistency.
They prove neither authenticated publication/completion time nor custodian authority.
External files cannot form one atomic snapshot: pinned evidence is rechecked, source
inventories use read transactions and before/after DB hashes, and SQL ingestion is
atomic. Keep source files quiescent; a mismatch fails closed. Retain every policy,
plan, independent seal, raw archive and operational batch for offline readback.

## Validation

```sh
pytest -q tests/integration/test_krx_backfill.py tests/integration/test_krx_recurring.py \
  tests/integration/test_krx_scheduler.py tests/integration/test_forward_capture.py \
  tests/integration/test_operational_capture.py tests/architecture
ruff check src tests
ruff format --check src tests
mypy
python -m build --no-isolation
```

These tests use temporary synthetic official-schema inputs and MockTransport. They
are not live collection evidence or operational deployment. Operational completion
requires actual authorized responses, newly committed rows and verified readback.
