# Guarded DART multi-batch sessions

Prepared on 2026-10-14 against main `58bad87858b9363c840e267550a80d4b5e62e543`.
This change performs **zero authenticated OpenDART requests**. Synthetic tests are
not official evidence. Corporate Actions Coverage remains **0/993**, and Corporate
Actions, Runner Tail and Run Input Freeze remain blocked.

## Composition and approval

`dart_multi_batch_session.py` composes `plan_for`, `inventory`, `summary`, the
orchestrator's `inspect_next`/`draft_for`, and the existing `approval_check` and
`execute`. Transport, streamed 16 MiB response limits, ZIP member/size/CRC/path
checks, immutable source receipts and request reservations are unchanged.

Prepare fixes the entire ordered Pending scope, divides it into consecutive
batches of at most 25 and writes a parent draft under
`.data/dart-multi-batch-sessions/session-NNNN/draft.json`. Each child retains the
existing exact eleven-field batch schema. Parent drafts and child templates have
`approved_at: null`, `access_confirmed: false`, `quota_confirmed: false`.
Draft validation never fabricates an approval timestamp.

A parent has an explicit finite request budget (1–3,107), exact receipts, endpoint,
plan fingerprint, child execution paths, interval >=1 second, zero retries and
operator attestations. The budget equals the selected scope size; when fewer
Pending documents exist, the prepared budget decreases. It never increases while
running. A pre-existing next batch draft must exactly match the first child;
otherwise preparation stops. Preparing a smaller scope than that draft also stops.

On explicit execution, the approved parent must exactly match its saved draft
except for the three approval gates. The runner persists its immutable copy and
`binding.json`, which contains the parent approval fingerprint and each derived
child approval fingerprint. Child gate values are inherited only from a validated,
explicitly approved parent, not invented by Prepare. Every derived child passes
existing batch approval validation. No unlimited CLI boolean replaces the record.

These are local operator attestations, not cryptographic proof of human identity
or current OpenDART account privileges. Operators must protect the local records.

## Locking, stop and resume

The same Mac/Linux nonblocking kernel execution lock spans the whole session.
The private context used by child calls is bound to root, process and thread, and
only avoids reacquiring the already-held lock. All normal batch execution still
acquires that lock. Direct batch calls cannot bypass any unfinished parent session.

The parent fixes all child scopes before starting. Before each child it rechecks
inventory, bindings and budget: existing reservations plus remaining Pending
requests cannot exceed the parent budget. Reservations are conservative consumed
budget even if a crash occurred before HTTP dispatch. Each request retains the
engine's durable pre-request claim/attempt marker. No automatic retry is added.

Any child failure, HTTP 429, quota/800/authentication error, unsafe ZIP/CRC,
missing/corrupt evidence, scope mismatch or exception stops the parent. A static,
secret-free `stopped.json` records the stop and parent fingerprint. Remaining
children are not executed. A new session ID cannot bypass an unfinished or
stopped session, and standalone batches are also blocked.

A process death can leave no stop record. Explicit `--resume` is required; saved
parent approval/bindings, existing child approvals and all sources are revalidated.
Completed children are skipped. A fully preserved ZIP plus receipt can be
reconciled by the original executor without repeating its request. Reserved but
unresolved requests, body-only saves, Failed/Blocked states and persisted stops
are refused. Operator review is required; there is no reset/retry override.
A missing/partial parent binding also fails closed. No automatic repair is made.

Completion persists the exact scope's source SHA-256 map and revalidates it on
subsequent status/preparation. It proves local acquisition/replay only, not
historical identity, event semantics, cancellation/correction history, effective
dates, no-event coverage, PIT or permission for AI/backtest use.

## Offline commands

```bash
.venv/bin/python -m donghak_stock_vision.backtest.dart_multi_batch_session \
  --root . --status
.venv/bin/python -m donghak_stock_vision.backtest.dart_multi_batch_session \
  --root . --prepare --max-requests 100
```

Status is read-only. Prepare writes only a new unapproved session draft (and any
missing lock infrastructure). Repeated Prepare refuses to overwrite or skip an
unfinished session. Existing Pilot, batch-0001/0002, approvals, reservations and
`.data/dart-batch-0003-draft.json` are preserved byte-for-byte.

For a **future separately authorized run**, an operator supplies a separate
approved parent file matching the draft and explicitly confirms execution:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.dart_multi_batch_session \
  --root . --execute .data/OPERATOR-APPROVED-PARENT.json --confirm-execution
```

Add `--resume` only for explicitly reviewed interruption of that same scope.
This command is not run by this implementation task. Before authorizing it,
confirm the exact parent fingerprint, receipt list, child IDs/paths, total budget,
interval, retries, current approved key/IP access and remaining quota, with a real
UTC approval time. Previous one-document or individual-batch approval cannot be
reused as parent authorization. Never commit local approval, ZIP, receipt, key,
market DB, snapshot or audit records.

## Local evidence baseline

Read-only inspection found 3,108 planned documents: 51 Verified Local, 3,057
Pending, zero Attempted/Failed/Blocked. The next batch is batch-0003 and its
existing 25-document draft is compatible. The successful Pilot and both completed
25-document batches were replayed by inventory validation. The prior status-800
cache and failed Pilot remain separate and are not promoted as successful sources.

The offline preparation created
`.data/dart-multi-batch-sessions/session-0001/draft.json` for 100 receipts,
batch-0003 through batch-0006 (25 each). The original batch-0003 draft was
reused without modification. No parent/child executable approval was generated.
Plan fingerprint: `74540d0b223a8da4f375eef59feef49ca0da892d640434c085c27f9dd917e7ab`.
The preservation check compared SHA-256 of 6,482 existing evidence/control/DB/
snapshot files and found zero changes. The new draft is Git-ignored.
