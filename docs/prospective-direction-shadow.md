# Prospective direction shadow research

This manual backend path extends research recording across 1, 5 and 20
observations. It reuses original autonomous study/model owners, the bounded
checkpoint reader, source/receipt verification, feature vector and masks,
append-only Shadow journal, intents/head pins, recovery and existing metrics.
No fitting, market requests, model promotion or scheduling occurs.

## Contract and compatibility

`future_delayed_direction_v3` is a separate contract from legacy v1/v2 five-session
reversal-barrier events. For input session T, the next verified session B supplies
the **outcome base close**. The following H verified sessions form the event; the
last close is compared with B. Absolute returns <= 20bps are unavailable/neutral,
not a negative class. The exact integer-price label primitive is shared with
`research_v2`; old training labels, serialized contracts and model parameters do
not change. The 20bps cost is an illustrative research exclusion, not actual fees.

Features use only original observations through T. B and all outcome prices are
read only after the outcome guard and original sources are available. Generation
must precede midnight KST of the event's first session. This new future recording
rule explicitly differs from the retrospective training sample's nominal B 19:00
anchor: the nominal anchor never authenticated historical availability. An
unchanged 19:00 next-day collector can finish before the second-next session's
midnight; no collector delay or legacy deadline is relaxed.

Each registration verifies exactly three original completed checkpoints, one per
H, from supported expanded research study versions v4/v5/v6. The original model
contract must equal `observed_delayed_direction_v2` for that H, with 46 causal
features, the original mask, training prior and threshold .5. A legacy eight-feature
reversal model is incompatible. Models cannot be relabelled by changing H.
Checkpoint constructors/runtime versions/SHA and original study metadata are
verified; registration is pinned separately and never rewritten.

Future windows require a complete retained verified calendar (up to six bounded
months), exact observed sessions and no missing-session compression. Missing input
sessions block generation; missing future sessions preserve Pending. Listing,
suspension, corporate-action and publication-time uncertainty is not guessed away.
The labels remain unadjusted-close research events, not authenticated PIT events.

## Private request and commands

Registration request (`direction_shadow_registration_request_v3`):

```json
{"version":"direction_shadow_registration_request_v3",
 "models":[{"root":"/private/study","plan_pin_file":"/private/pins/plan.pin","experiment_id":"<original H1 ID>"},
           {"root":"/private/study","plan_pin_file":"/private/pins/plan.pin","experiment_id":"<original H5 ID>"},
           {"root":"/private/study","plan_pin_file":"/private/pins/plan.pin","experiment_id":"<original H20 ID>"}],
 "tickers":["005930"],"source_kind":"local_provider_capture"}
```

Use new private report/PIN locations outside source roots:

```sh
dsv direction-shadow register --request "$REG_REQUEST" \
  --request-pin-file "$REG_REQUEST_PIN" --report "$REGISTRATION" \
  --report-pin-file "$SEPARATE_REGISTRATION_PIN"
```

Prediction/outcome request (`direction_shadow_request_v3`) contains registration
and registration_pin_file, the original pinned research/protocol references,
current verified operational reference (`root`, `pin`, `evidence`), bounded
context_window (`start`, historical research `end`), input_trading_date,
calendars and mode (`research_forward_candidate` or explicit `historical_replay`).
A context split before start explicitly selects operational-only history. Research
Final Test windows are rejected before source reads.

```sh
dsv direction-shadow record --request "$REQUEST" --request-pin-file "$REQUEST_PIN" \
  --journal "$JOURNAL" --head-pin-file "$HEAD" --intents "$INTENTS" \
  --pins-root "$EVENT_PINS" --report "$NEW_RECORD_REPORT" \
  --report-pin-file "$NEW_RECORD_REPORT_PIN"
# Later, with new verified source references and retained head:
dsv direction-shadow resolve --request "$CURRENT_REQUEST" \
  --request-pin-file "$CURRENT_REQUEST_PIN" --journal "$JOURNAL" \
  --head-pin-file "$CURRENT_HEAD" --intents "$INTENTS" --pins-root "$EVENT_PINS" \
  --report "$NEW_RESOLVE_REPORT" --report-pin-file "$NEW_RESOLVE_REPORT_PIN"
dsv direction-shadow report --request "$CURRENT_REQUEST" \
  --request-pin-file "$CURRENT_REQUEST_PIN" --journal "$JOURNAL" \
  --head-pin-file "$CURRENT_HEAD" --report "$PUBLICATION" \
  --report-pin-file "$PUBLICATION_ID_FILE"
```

The report is generated only after semantic replay of inputs, original model
scores and terminal outcomes. It is safe for explicit publication. Record/resolve
reports containing head references are private and cannot be served as publication
snapshots. Use `recover` with the separate intent and intent pin to recognize one
committed unacknowledged suffix; recovery never inserts an old prediction. Unknown
multi-entry tails require existing operator reconciliation. Re-execution uses
stable slot/prediction IDs; conflicting evidence or source changes fail closed.

The legacy resolver is fixed to five-session reversal barriers. The narrow v3
adapter changes only horizon/label orchestration, reusing its source and journal
owners; it does not route new labels through that incompatible resolver. Existing
v1/v2 journals and research cycle remain unchanged. After an authorized collection,
operators may run the commands above; this PR installs no automatic research task.
Original input and outcome references are retained per event, allowing bounded
replay as operational archives append without requiring an unbounded evidence list
in every new request. Archive substitution is rejected.

Consistency boundary: source databases, calendars, model files and journals do
not share a cross-file atomic snapshot. Existing source owners verify retained
identities and quiescent database fingerprints; research sources and journal
heads are checked across replay, and writes use the journal owner and its lock.
Concurrent source changes can fail verification. Retain the failure evidence,
wait for collection to finish and prepare a new separate current read reference;
never rewrite historical event references or treat local hashes as chronology.

`publish` accepts a pinned `direction_shadow_collection_v1` request containing at
most ten streams (`kind`, `request`, `request_pin_file`, `journal`, `head_pin_file`).
Use `kind=direction_v3` for new journals and `legacy_v1_v2` for the original
five-session reversal journals. Legacy projections retain original IDs, labels
and conditioning direction; `predictionType=conditional_reversal_event` is not
an absolute up/down forecast. Their original resolver alone owns outcomes. Each
stream is semantically verified, then up to 1,000 sanitized predictions are merged.
A snapshot-local integer `inputCohort` joins identical privately verified source
bindings without exposing their PINs. Duplicate evaluated model/ticker/date rows
are collapsed for scoring (and reported separately); conflicting replicas fail.
Comparisons require identical input cohort, horizon, source quality, mode, contract, evaluation
policy, ticker/input/outcome population and actual labels. Unequal populations are
noncomparable; there is no automatic winner or model replacement.

## API for web and iOS

Use an operator-approved publication SHA-256 and an existing authorized secret
in an environment variable; this code does not create or change credentials:

```sh
dsv-api --direction-only --direction-publication "$PUBLICATION" \
  --direction-publication-sha256 "$APPROVED_FILE_SHA256" \
  --direction-read-token-env DSV_RESEARCH_READ_TOKEN --host 127.0.0.1
```

The file SHA is over retained bytes (including newline); it is distinct from the
canonical JSON report ID. Embedded apps may inject the existing verified principal
resolver with scope `research:read`. The same ScopedBearerAuthority used by model
management is reused. Authentication, scope and loopback/Origin guards apply;
forwarded headers are disabled. Web/iOS use this backend, not raw journals or model
endpoints. Re-publication requires a new immutable file and explicitly approved
fingerprint; replacing the configured file fails closed. Remote deployment requires an approved authenticated gateway/security
policy; this PR does not expose the private service publicly.

```http
GET /api/v1/research/predictions?horizon=20&ticker=005930&state=pending&limit=25
Authorization: Bearer <authorized token>
```

Other reads: `/api/v1/research/predictions/models` and `/comparisons`. All use
`schemaVersion=prospective_direction_read_v1`, items/total/offset/limit/nextOffset;
limit is 1–100. Prediction rows contain public model/prediction IDs, H, ticker,
input/target dates, exact sessions, generation time, feature/label version,
score, predictedClass, threshold, state and actualLabel. `calibratedProbability`
is null. Source paths, private Source/Snapshot/Dataset/Plan PINs, checkpoint paths,
features and model binaries are absent. Extra/nested fields, modified snapshot
bytes and unreconstructable metrics are rejected. There are no write routes.

Model rows provide counts, accuracy/BA/precision/recall/F1/ROC-AUC/AP/Brier,
confusion matrix, frozen training-prior baseline and relative diagnostics. Undefined
metrics remain null. AP is not accuracy. Historical Replay, fixtures and real
prospective outcomes are separate populations. Calendar anchors union stocks by
start date and purge complete maximum outcome intervals. Minimums remain 20
anchors and 10 rows of each class on selected anchors. Blockers and deficits are
explicit. Passing local count guards does not authenticate chronology: qualified
anchors, PIT, performance approval and investment authority remain zero/false.

## Actual execution boundary

The original completed research assets supplied compatible, SHA-verified H1/H5/H20
models. Three historical-input inference smoke checks completed without training
or recording predictions. The independent authorized scheduler subsequently collected 936 rows for
2026-11-16, bringing the operational store to 27,238 rows across 29 sessions.
This task made zero KRX requests. With that latest input, new generation was
blocked after the 2026-11-18 event-start midnight. No prediction was backdated. Existing exposed 9/4/1 historical anchors are not
added to the new prospective totals, which are 0/0/0 (20/20/20 still required).
New predictions and outcomes were zero. The existing eight legacy candidates
remain in their original v1/v2 journals and are verified separately.

Operational-only history currently has 29 sessions, less than the 35-observation
feature warm-up (closed higher-timeframe history is also required). Further
recording needs fresh verified inputs, complete retained calendars through each
H outcome and execution before that input's event deadline. Missing future prices
are normal Pending evidence, never synthesized results. Existing data, models,
Final Test, PINs, scheduler settings and operating request budgets remain protected.

Illustrative paginated response shape (IDs abbreviated here; actual IDs are 64 hex):

```json
{"schemaVersion":"prospective_direction_read_v1","items":[
 {"predictionId":"<public-id>","modelId":"<public-model-id>","horizon":20,
  "ticker":"005930","state":"pending","actualLabel":null,
  "score":0.54,"calibratedProbability":null,"predictedClass":1,"threshold":0.5}
],"total":1,"offset":0,"limit":25,"nextOffset":null}
```

The example is a shape example, not an actual Samsung forecast. Real rows also
contain dates/sessions, artifact SHA, version/policy identity and source/mode fields
listed above. Registered legacy rows explicitly carry their original reversal
contract and conditioningDirection; new direction rows have a null conditioning
direction. Count sufficiency never changes the false approval flags.

Journal entry/byte limits remain unchanged. Use bounded preregistered cohorts and
nonoverlapping recording cadence or explicitly verified segments; do not raise
limits or inflate anchors with daily overlapping replicas. The publication limit
is 1,000 predictions across at most ten explicitly pinned streams. Retain full
private journals even when publishing a bounded view. Existing original model
contracts keep their `live_forward_compatible=false` status: this research-only
adapter never authorizes production prediction or performance approval.
