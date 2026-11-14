# Manual KRX v2 forward research cycle

`shadow-prediction cycle` connects the existing committed collection, Handoff,
shadow recorder, append-only journal, resolve and readiness owners. It performs
**zero network requests**, loads no credential, changes no market/runner DB,
policy, model, threshold or LaunchAgent and activates no scheduled research job.
The existing collector must finish before this manual post-collection command.

## Inputs and execution order

Use a new independently pinned `research_cycle_request_v1` document:

```json
{
  "version": "research_cycle_request_v1",
  "active_v2": "v2",
  "streams": [
    {
      "name": "v1",
      "request": "/private/research/v1/request.json",
      "request_pin_file": "/private/pins/v1/request.pin",
      "journal": "/private/research/v1/shadow.sqlite3",
      "head_pin_file": "/private/pins/v1/current-head.pin",
      "intents": "/private/research/v1/intents",
      "pins_root": "/private/pins/v1/events"
    },
    {
      "name": "v2",
      "request": "/private/research/v2/request.json",
      "request_pin_file": "/private/pins/v2/request.pin",
      "journal": "/private/research/v2/shadow.sqlite3",
      "head_pin_file": "/private/pins/v2/current-head.pin",
      "intents": "/private/research/v2/intents",
      "pins_root": "/private/pins/v2/events"
    }
  ]
}
```

These paths describe existing originals; replace them with the already verified
private references. A stream template is an existing pinned
`research_shadow_request_v1` or `research_shadow_request_v2` in forward-candidate
mode. Its model, source kind, universe, calendar and original target remain frozen.
Only the explicitly named v2 stream can record new candidates. All other streams
resolve their existing predictions only. Journal paths must be distinct; v1/v2
statistics and fixture groups are never merged into one performance estimate.

The consumer:

1. Reads operational status and bounded source-date/collection-time metadata.
   Selects the latest available KOSPI date with stored observations, not a foreign
   market, future response or a normal empty response. Status alone is not an
   original-source integrity claim.
2. Reuses original scheduler completion verification and Handoff request retention.
   A separately frozen current whole-file reference and required original capture/
   batch references are retained for this run. Original templates and PINs are
   never overwritten. All recorded horizons and sealed windows are checked before
   any completion/source replay can extend a read range.
3. Verifies existing predictions, model/input/feature/score bindings and journal.
   Computes the active v2 target from the latest verified input's **second** future
   session using the retained calendar. Existing v2 feature/label/threshold and
   target-date exclusive midnight deadline remain unchanged.
4. Skips fully processed slots, retaining duplicate counts and permanent context/
   unavailable-direction reasons. Incomplete slots use existing Handoff/record
   guards and recovery. A missed deadline blocks new recording, without moving the
   event or preventing resolution of older Pending predictions.
5. Independently resolves each stream using its original target, model and outcome
   sessions. All original five outcomes, plus the v2 gap source, are required.
   Missing/not-yet-available results remain Pending. Research failure cannot undo
   collection or prevent another stream from verifying its own valid evidence.
6. Reuses availability and evaluation-readiness assessments, verifies journal
   replay, checks source-file consistency again and writes a JSON audit report
   with a separately retained fingerprint.

The report includes operational/latest-source dates, original HTTP/raw-source
availability facts, model versions, per-contract real/fixture counts, new and
blocked predictions, reasons, acknowledged head references, original verification
time, source/journal verification status, Pending/outcome conditions and unchanged
false approval flags. Existing per-stream journal metrics are kept separate.
`official_first_publication_time` stays UNKNOWN; a successful response is a local
observability bound, not a signed publication clock or independently authenticated
custody. The published next-day 08:00 guidance is not proof of per-date readiness.

## Manual invocation and repeat

```sh
dsv shadow-prediction cycle \
  --request "$CYCLE_REQUEST" --request-pin-file "$CYCLE_REQUEST_PIN" \
  --scheduler-config "$CONFIG" --scheduler-config-pin-file "$CONFIG_PIN_FILE" \
  --report "$NEW_RUN_DIR/report.json" --pins-root "$NEW_CYCLE_PIN_DIR"
```

The report must be new, its directory private, and the CLI `--pins-root` must be
new and separate from the report directory. This is the **cycle report/input PIN
directory**, not a stream's existing event PIN directory in the JSON above.
The report's fingerprint is retained at `$NEW_CYCLE_PIN_DIR/report.pin`.

For the next run, keep the original cycle request and supply the previous exact
report and separately retained PIN. Use new output paths every time:

```sh
dsv shadow-prediction cycle \
  --request "$CYCLE_REQUEST" --request-pin-file "$CYCLE_REQUEST_PIN" \
  --scheduler-config "$CONFIG" --scheduler-config-pin-file "$CONFIG_PIN_FILE" \
  --resume-report "$PREVIOUS_REPORT" --resume-pin-file "$PREVIOUS_REPORT_PIN" \
  --report "$NEXT_RUN_DIR/report.json" --pins-root "$NEXT_CYCLE_PIN_DIR"
```

Resume accepts only the same pinned cycle input and original per-stream head owners.
It reads acknowledged head PIN files; it never trusts a DB's self-reported latest
head or reissues an old source PIN. Report modification is rejected. Report/PIN
retention is not an external custodian or clock-signing authority.

CLI exit 0 means every stream completed, possibly with per-ticker context blocks.
Exit 1 means at least one stream is blocked; the JSON gives the stage and reasons.
Invalid CLI/config/reference inputs return the existing validation error status.
Do not treat an exit code or a collection completion as prediction approval.

## Failure, recovery and consistency

Prediction/outcome commits remain the original owner's atomic SQLite transactions.
The whole multi-stream cycle is not a cross-DB transaction. Separate original read
transactions and whole-file PIN checks detect a changing/quiescence-invalid source;
stop and inspect a blocked run before resuming after the collector finishes.
Concurrent research writers use the existing head/slot checks and transactions:
a conflict fails rather than silently accepting another runner's unacknowledged head.

If a write loses its acknowledgement, the report marks
`journal_write_outcome=inspect_retained_checkpoints_and_intents`. Counts that cannot
be established are null, not zero; summaries identify the last acknowledged
checkpoint. No resolve is attempted behind an uncertain record commit. Keep the
retained intent, its independent PIN, derived request and last acknowledged head.
Use existing `shadow-prediction recover` with those exact originals. It recovers
only an already committed exact intent; it does not backdate or create a missing
prediction. Explicitly adopt the recovered head in a **new pinned cycle input**,
then run a new cycle. Never edit the old request/report/head file. Inspect partial
resolve commits similarly, using the existing resolve/recovery owners.

A fully retained report without its separate report PIN is not an acknowledged
resume checkpoint. All original records remain append-only; rollback consists of
stopping manual research invocation and leaving collection running. No operating
schedule/config change is needed to stop this consumer.

## Bounds and automation boundary

At most eight unique streams and one active v2 stream are accepted. Existing limits
remain: bounded source metadata/completions, model/source size, 25 tickers, three
calendar months, per-source replay bounds and the journal's 256-entry capacity.
No cache of response bodies survives a source-owner replay. Rotate before a journal
or calendar limit is reached: retain the old stream for resolve, and create a new
pinned template/calendar and separate journal stream. Do not replace calendar PINs
under old predictions to accommodate another month. A new cycle input/versioned
set of references is required to change streams or recover a head explicitly.

Automation remains disabled. Before separate activation, verify repeated real
weekday cycles, successful collection before the fixed v2 deadline, adequate
calendar coverage, source/model/head retention, optimistic-concurrency and failure
recovery, logs and independent collection/research outcomes. Existing provider
permissions, request budgets and future-date policy coverage remain collection
preconditions; this command grants no network permission. PIT, performance,
genuine-forward and execution approval remain false, even after research evaluation.

## Actual existing-source verification: November 15, 2026

The manual CLI ran twice from source and again from the isolated installed wheel,
using the original retained requests, models, journals and independent head PINs.
The later runs explicitly resumed the previous report/PIN. No provider request,
new market observation, training, model registration or schedule change occurred.

| Fact | Verified result |
| --- | --- |
| Operational SQLite | 26,302 observations, latest date November 13 |
| Original latest capture | HTTP 200 / 936 original observations |
| v1 model/dataset | `d2fd9a4d40de1a10…` / `krx-research-and-backfill-20261113-v1` |
| v2 model/dataset | `a8e7c821d48572b2…` / `krx-next-day-v2-20261114` |
| v1 candidates / Pending / evaluated | 4 / 4 / 0 |
| v2 candidates / Pending / evaluated | 4 / 4 / 0 |
| New candidates / provider requests / added market rows | 0 / 0 / 0 |
| Retained v2 duplicates | 4 |
| Known context exclusion | 051910: `shadow_no_directional_context` |
| Source PIN / original receipt / journal replay | Verified |
| Pre-existing protected file SHA-256 values | 484 unchanged |

The installed-wheel original-verification time was November 15
06:35:59.508306 KST, a **local execution timestamp**, not original publication time.
All eight original prediction envelopes, IDs, source/model/feature bindings,
scores, thresholds, generation times, target/event sessions and journal heads
remained unchanged. The one context exclusion did not append another failure or
invalidate collection. No new Historical Replay or actual outcome was created.

v1 still requires November 16–20 outcomes, with the original November 20 16:00 KST
resolve guard. Its last source's local capture lower bound is November 21 00:00;
this is not provider availability, and the existing weekday schedule does not
supply a Saturday execution. v2 requires November 17, 18, 19, 20 and 23 plus its
November 16 gap source. Its resolve guard is November 23 16:00; the local capture
lower bound for the last source is November 24 00:00. The existing policy does
not cover November 23 and expires before that capture. A separately approved
collection period is required. No future source or outcome is invented to close
these dependencies.

Actual artifacts are retained separately under
`Research/research-cycle-20261114T180717Z` and matching `ResearchPins`:
original-eight-predictions, protected-before hashes, cycle request, first/repeat/
installed reports, derived original-source references, report PINs and installed
summary. Full model identifiers remain in those private reports. Future-data
fixtures test complete outcome attachment/replay and recovery separately, and
are never included in the actual counts above.

When a scheduler policy/registry is renewed, retain/register the old authoritative
completion references or create separately pinned templates carrying the previous
verified evidence. Do not discard references needed by unresolved old predictions.
A report head alone cannot restore missing original capture evidence. Keep old
calendars/models/journals and their PINs; a new policy or calendar never silently
changes an old prediction contract.
