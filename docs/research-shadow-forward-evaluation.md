# Research shadow recording and deferred evaluation

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
target requires November 13 input. One-day provider lag and the existing weekday
19:00 collector schedule must be reconciled with the original event alignment
before automatic forward operation; this PR does not silently invent a lagged
forecast rule or change the schedule to force success.

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
