# Research shadow recording and deferred evaluation

For the later November 14 actual capture and four research-forward candidates,
see [KRX availability observations](krx-data-availability.md). Earlier zero-candidate
reports below retain their original observation-time meaning.

`shadow-prediction` connects original operational captures and the existing safe
JSON research model to a separate research event stream. It grants no PIT,
performance, investment signal, paper, execution or independent chronology approval.
Scores are conditional reversal-barrier research scores, not share-price rise/fall
probabilities. No provider request, model refit or scheduler change occurs.

## Existing owners and explicit separation

The existing forward signal journal owns SQLite initialization, immutable triggers,
transaction/commit behavior, byte/count bounds, hash chains and retained heads.
New `research_shadow_journal_entry_v1` entries reuse that owner/schema in a separate
file. Legacy v1/v2 entry bytes and reconstruction remain intact. The original
prospective prediction/outcome/evaluation owners remain offline_fixture-only;
shadow entries cannot be passed to them as genuine prospective evidence.

Original source verification reuses the bounded OperationalMarketStore batch
replay, original backfill intent/receipt/PIN checks, and forward capture checks
extracted from the existing dataset reader. The original dataset reader retains
its defaults. A shadow window explicitly skips unrelated forward references
before response replay and reads only its required dates. Source raw bytes are
never reconstructed or replaced. Exact expected session membership prevents gaps
being compressed into a valid-looking feature or label history.

Models use `load_model`, the PR #231 real-source compatibility gate and original
independently retained snapshot/source/manifest PINs. Features reuse feature_vector
and context; outcomes reuse label_event/reversal_barrier_v1, five future observed
bars, max(0.02, 2*volatility_10), unchanged ambiguity/no-trade guards. Metrics reuse
PR #232 diagnostics and existing prior/reversal_strength baselines. No second
collector, normalized market DB, trainer, outcome estimator or journal subsystem.

## Time and event alignment

Each prediction freezes an immutable ID/slot, ticker, mode, source classification,
target date, all five expected outcome sessions, latest input date, 12 original
bars/batch references and their projection PIN, feature names/version/values/hash,
model artifact/dataset identifiers and SHA/parameters, fixed threshold/score,
input-read verification time, feature time and actual UTC generation time.
Unknown outcome is null, with initial Pending state. Later outcome events carry
source references, label interval, original response observation time and local
result verification/availability time separately. Predictions never change.

Original operational storage does not retain a separate ingestion-commit timestamp:
that field stays null. Raw collected_at is not silently renamed ingestion time.
Availability established in this process is a local read timestamp, not independently
authenticated historical publication. Model created_at, journal and intent times
are also local. Corporate actions, revisions, suspensions, delisting/survivorship
and independent publication/custodian authority remain unverified; pit_qualified,
genuine_forward_evidence and performance_approved always stay false.

The original next-observation model alignment is preserved: the target must be
the next retained-calendar session after the last input bar; all 12 preceding
sessions must exist. This includes the previous edge used by the existing feature
quality check, without relaxing the model's 11-bar minimum. A candidate target is
strictly after the actual generation date in Asia/Seoul. Inputs/model must be
available by execution, and candidate inputs must be after the model's frozen
development window. The sealed outer Final Test is rejected before operational
source replay. No input or outcome window may cross it.

Historical Replay is explicit, generated now with a past target; model/input time
contradictions at that historical target are never repaired by backdating.
Replays are excluded from candidate metrics. Explicit offline fixture sources
have a third separate report group and never count as real forward candidates.

Missing required latest data blocks generation and records a failure, not a
prediction. In particular, with data through November 12 only, a November 16
target requires November 13 input. The policy's one-day minimum lag and existing weekday
19:00 collector schedule must be reconciled with the original event alignment
before automatic forward operation; this PR does not silently invent a lagged
forecast rule or change the schedule to force success.

## Collection alignment and optional continuation

`shadow-prediction alignment` is a read-only consumer of the same pinned request,
calendar, model and bounded original operational input verification as recording.
It creates no journal, prediction or provider request. Its versioned report
`research_shadow_alignment_v1` separates missing input sessions, raw collection
time, local verification time, model creation, the unchanged five outcome sessions,
and the exclusive generation deadline. Feature/prediction/ingestion/result times
not established by this operation remain null. `input_timing_ready` is only an
input/timing assessment: directional context, inference, development-window and
commit checks still belong to `record`.

The existing candidate contract deliberately requires generation **before midnight
KST starting the target date**. This is stricter than a pre-open check and is not
an authenticated exchange opening time. This change preserves that guard and
all historical event bytes. It does not enable same-day pre-open predictions.
Such support would require a separately reviewed timing contract with retained
session-specific opening evidence, including exceptional opening hours.

With `--scheduler-config` and its independently retained `--scheduler-config-pin-file`,
the report also compares the current weekday/hour configuration with that deadline.
`research_shadow_schedule_alignment_v1` computes the policy's next-calendar-day
lower bound and the next configured weekday slot. An in-scope slot is not a
statement that the scheduler is enabled or that KRX has finished publishing.
This helper does not change policy, lag, calendar, credentials, schedule or PINs.

```sh
dsv shadow-prediction alignment \
  --request "$NEW_REQUEST" --request-pin-file "$NEW_REQUEST_PIN_FILE" \
  --scheduler-config "$EXISTING_SCHEDULER_CONFIG" \
  --scheduler-config-pin-file "$EXISTING_CONFIG_PIN_FILE" \
  --report "$NEW_ALIGNMENT_REPORT"
```

All output reports must be new paths. No journal/head arguments are needed for
alignment. An invalid request/source/model PIN fails closed; missing inputs and
an elapsed target are reported without generating a substitute Historical Replay.

The existing `krx-scheduler run-once` and `run-due` accept an **optional, inactive
by default** continuation using all six explicit arguments:

```sh
# Append these options to the existing authorized collection command only after
# the timing, fresh request/source PIN, model and private outputs are reviewed.
--shadow-request "$NEW_REQUEST" \
--shadow-request-pin-file "$NEW_REQUEST_PIN_FILE" \
--shadow-journal "$RESEARCH_JOURNAL" \
--shadow-head-pin-file "$RETAINED_HEAD_PIN_FILE" \
--shadow-intents "$PRIVATE_INTENTS" \
--shadow-pins-root "$SEPARATE_RESEARCH_PINS"
```

These arguments require `--execute`, cannot accompany daily-research arguments,
and never grant collection/network permission. No LaunchAgent is installed or
changed. Existing collector controls remain authoritative. The continuation
`krx_shadow_handoff_v1` preserves the collection report unchanged and reports an
independent research status. With no successful collection it skips without even
opening the research request or journal. A successful summary only selects a date:
the production path verifies the existing completion registry, original capture
and stored batch, and binds the operational root and immediate preceding input session.
It then freezes a **new derived request** after commit: the original template and
its independently held PIN remain unchanged, the committed database gets a new
quiescent-file PIN, and the verified completion's original plan/capture/batch
references are retained in separate private input and PIN directories. An existing
template reference for that date must agree; it is never silently replaced.
`research_shadow_handoff_input_v1` binds the parent request, scheduler config,
completion and derived request. These new local checkpoints do not authenticate
external custody or publication. The existing recorder re-verifies the complete
source window using that derived request. Missing completion, future local completion time,
elapsed target, wrong source/model or backwards clock blocks research. Synthetic
fixture requests use the existing explicit fixture source verifier and remain
excluded from real collection/forward counts.

The recorder owns deterministic IDs, immutable entries, source replay, duplicate
handling and recovery; no second journal or completion registry is introduced.
Research errors never retry collection or change its success/exit status. If a
research write is interrupted, the wrapper reports its outcome as unknown, not
zero writes. Inspect the retained per-event head checkpoints and intents, use
the existing `recover` command for a lost acknowledgement, and pass the recovered
head on continuation; the error report retains the derived request reference when
one has been prepared. A stale head fails closed. A pre-collection template's old
whole-file PIN is never claimed to match the appended DB: only the separately
retained post-commit request is supplied to the recorder. Direct manual `record`
still requires a request with a current source PIN. The original template,
prediction projection and historical head remain intact.

There is no atomic snapshot spanning the completion registry, original archive,
model and market database. Each existing verifier uses its own read transaction,
original receipt/PIN checks and quiescent file hashes; a concurrent append fails
closed. Run research after the collector has released its write transaction.
Do not infer an authenticated SQLite commit time from a registry's local timestamp.

### Observed operating environment, November 14, 2026

Read-only verification retained a separate private report directory
`Research/schedule-alignment-20261114T002756Z`. Existing market storage remained
25,366 observations/27 observed dates through November 12. The original 10 replay
predictions and five terminal outcomes reproduced; all 314 protected files,
including the previous 218-file baseline, retained their SHA-256 values.
No authenticated market request, model training or scheduler change was performed.
Installed-wheel verification also replayed the original October 26 completion
(940 observations), reproduced a separately retained post-commit request and PIN,
and rejected an elapsed October 27 event without creating a journal. This was a
negative timing check against existing evidence, not a new collection or Forward
Prediction. Neither the old template nor its held PIN was updated.

The weekday 19:00 LaunchAgent was installed/loaded/enabled, idle with two native
runs and last exit code zero. Retained application logs establish starts at
November 13 **20:51:12 KST** and **20:55:33 KST**, both `no_due_dates`; they do not
prove application entry exactly at the configured 19:00 trigger. Earlier runtime
diagnostics documented startup delay before collection. On November 13 every
permitted policy day was today or later, while both scheduler plan and backlog
require at least one calendar day of lag. Thus no date was eligible, regardless
of the startup delay. The next weekday trigger is November 16 19:00 KST for
November 13 input. It misses the unchanged November 16 event deadline; moving the
event to November 17 would silently change the model and is rejected.

The stored official `Spec.docx` identifies `stk_bydd_trd`, `basDd` and `OutBlock_1`
but contains no availability hour. The [official KRX service description](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC)
confirms the daily service and coverage, not its publication hour. Public HTML
retrieval was refused (403); no authenticated sample API was invoked. Consequently
the exact official publication time and same-day availability remain UNKNOWN.
No third-party claimed hour is promoted to an operating rule. The local policy's
delay bound is not a provider publication guarantee.

All five November 16 candidates remain blocked by missing November 13 input.
The real last-batch handoff correctly skipped (`no_successful_collection`) and
created no journal. A separately authorized weekend collection completed and
verified before Monday midnight could satisfy the existing event contract without
changing the model; the present weekday-only schedule cannot do so. Actual provider
availability, an explicitly reviewed timing/collection plan, sufficient new input
and successful guarded research execution are still needed before activation.
Ordinary next-morning collection cannot pass the current midnight guard. Any
proposed same-day pre-open extension needs its own reviewed evidence contract;
changing only LaunchAgent's hour would not solve that boundary.

## Manual commands and post-collection interface

Store new requests, journal, intents and reports under a new private Research
folder. Store request/intent/head PINs separately under a new ResearchPins folder.
Never point writable outputs at model/operational sources. The request version
`research_shadow_request_v1` has:

- mode: historical_replay or research_forward_candidate;
- source_kind: local_provider_capture (actual use) or explicit offline_fixture;
- target_trading_date and sorted unique tickers (at most 25);
- model: database, pin, model_version, source_pins_root from PR #231;
- operational: root, current quiescent whole-file pin, original evidence references;
- calendars: retained original raw/source/normalized_pin month references.

Use the original source evidence formats documented for real-market-dataset.
Do not submit an unverified normalized DB row or fixture as a real source.
The request's canonical JSON PIN and initial journal head are retained privately.
For a new journal, write the existing forward_signal_journal.GENESIS constant to
its new head-PIN file with the existing private write_new helper; do not print it.
The first record transaction initializes the existing journal schema atomically.

```bash
dsv shadow-prediction record \
  --request "$REQUEST" --request-pin-file "$REQUEST_PIN_FILE" \
  --journal "$SHADOW_JOURNAL" --head-pin-file "$TRUSTED_HEAD_FILE" \
  --intents "$PRIVATE_INTENTS" --pins-root "$SEPARATE_PINS" --report "$NEW_RECORD_REPORT"

dsv shadow-prediction resolve \
  --request "$CURRENT_SOURCE_REQUEST" --request-pin-file "$CURRENT_REQUEST_PIN_FILE" \
  --journal "$SHADOW_JOURNAL" --head-pin-file "$NEW_TRUSTED_HEAD_FILE" \
  --intents "$PRIVATE_INTENTS" --pins-root "$SEPARATE_PINS" --report "$NEW_OUTCOME_REPORT"

dsv shadow-prediction verify \
  --request "$CURRENT_SOURCE_REQUEST" --request-pin-file "$CURRENT_REQUEST_PIN_FILE" \
  --journal "$SHADOW_JOURNAL" --head-pin-file "$LATEST_TRUSTED_HEAD_FILE" \
  --report "$NEW_VERIFICATION_REPORT"
```

`status` reads retained state/metrics without source I/O; its chain consistency
alone is not semantic replay. `verify` replays original input/model/outcome owners
and checks frozen scores and labels. `correct --prediction-id ... --reason ...`
appends an explicit withdrawal, preserving original prediction/outcome history.
A replacement requires a separately identified recording; no silent mutation.

After existing authorized collection, ingestion and readback succeed, an operator
can update a *new* source request with current DB pin and original completion
references, then call record/resolve. This is the integration interface; no second
LaunchAgent, timer or automatic callback is activated. Whole-file source pins
are checked before/after reads; they are not one multi-file atomic snapshot.
Append-only storage can grow: older predictions bind immutable selected bars and
batch PINs, not equality of an old whole DB to a later appended DB. A new source
request must authenticate the current read and reproduce the old projection.

## Deferred outcomes, idempotence and recovery

Before nominal horizon completion, Pending is retained and future response bodies
are not replayed. After completion all five exact calendar sessions and locally
available originals are required; missing inputs remain Pending. Ambiguous or
invalid label windows are explicitly unavailable. No zero label is substituted.
Final outcomes are independently appended; duplicate resolution must reproduce
the same result and does not change a record. Candidate/replay/fixture reports are
separate, with direction, ticker and target-month metrics and null undefined AUCs.
Small counts never imply sufficient statistical or prospective performance.

Each event has a durable, separately pinned intent before append. Each successful
commit retains a new head checkpoint outside the DB; old checkpoints are never
overwritten. Same prediction slots reject changed inputs/model/score/threshold.
An uncommitted identical prediction intent can resume within 300 seconds, retaining
its original generation time. A stale intent or elapsed target cannot create a
backdated prediction. A partial intent/PIN pair fails closed for operator review.

For one committed event whose acknowledgement/head write was interrupted, use
`recover` with the last trusted head, exact intent and independent intent PIN.
It verifies the single suffix and semantically replays the retained event chain,
then retains a new head. It never appends an old prediction or trusts an arbitrary
self-reported tail. More than one unknown suffix requires operator reconciliation.
A blocked CLI report never asserts that no commit occurred; inspect retained checkpoints
and intents after an interrupted/partial operation. An interrupted schema initialization fails closed; preserve that file and use a
new output after inspection. Transaction failure before commit preserves the old
valid head. Missing/corrupt source evidence never repairs or rewrites old records.

Existing bounds apply: 256 entries / 32 MiB per journal, 128 KiB per entry and
original response/row limits. Retain verified completed journals and start a new
explicit study before capacity exhaustion. No automatic rollover or unbounded
raw-body cache is introduced. Reports provide research measurements separately
from null approval-level performance metrics. No automatic retraining/threshold
adaptation consumes outcomes.

## Verification

```bash
pytest -q tests/integration/test_shadow_prediction.py \
  tests/integration/test_prospective_prediction.py tests/integration/test_prediction_binding.py \
  tests/integration/test_real_market_dataset.py tests/learning tests/architecture
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

Fixtures use the real model/source/receipt/journal validators, poison protected
source ranges, block sockets and explicitly segregate simulated forward records.
Actual private runs must report Replay, candidate, failure, Pending and completed
counts separately, source protection hashes, and unresolved future/publication
requirements. A working research interface is not production investment approval.
