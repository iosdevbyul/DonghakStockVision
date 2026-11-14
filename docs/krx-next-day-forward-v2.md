# Delayed-input forward research v2

This opt-in offline research path preserves v1. No model, label, deadline, policy,
PIN, LaunchAgent, operational registry or historical prediction is migrated.
It makes no provider request and enables no automatic research execution.

## Fixed event and timing contract

`next_day_forward_contract_v2` uses the original eight `ohlcv_value_v1` features
through input session T, skips exactly one subsequent session, and targets the
**second** verified session after T. Its exclusive deadline is still target-date
00:00 Asia/Seoul. It does not move a target dynamically when a run is late.
`delayed_reversal_barrier_v2` evaluates the five sessions beginning at that target,
using the target open, the original volatility barrier, reversal context, ambiguity
rules and fixed 0.5 threshold. The skipped session's prices never enter features,
barrier width or the model score. This new label requires a new fitted model;
applying v1 weights at a later event is forbidden.

For ordinary consecutive sessions, Monday input collected and verified Tuesday
can support a Wednesday event if the new model already exists and recording finishes
before Wednesday midnight. Friday input targets Tuesday, skipping Monday (or the
actual next session on a verified holiday calendar). A weekend does not shorten
this contract to v1. Late collection, absent calendar coverage, missing history or
unavailable model direction blocks recording rather than shifting the event.

The [official FAQ](https://openapi.krx.co.kr/contents/OPP/COMM/faq/OPPCOMM004.cmd)
excludes same-day/pre-next-day-08:00 queries and describes updates on a business-day
basis. This contract does not treat that guidance as a per-date publication clock.
Actual source collection, local verification, feature generation, model creation,
recording, future event sessions and outcome availability are recorded separately
by the existing snapshot/AnalysisStore/shadow owners. Local clocks and raw hashes
are not independently authenticated chronology; PIT approval remains false.

## Reused owners and deliberate separation

- Existing `real_market_dataset.prepare_dataset` verifies read-only research raw
  bytes, operational capture/batch evidence, original protocol and source PINs.
  Existing sealed Final Test barriers and October 2 empty-source handling remain.
- Existing `feature_vector`, `label_event`, chronological `split_dataset`, minimum
  sample requirements, `TrainingService._direction`, LogisticRegression fitter,
  validation-only selection and metric/baseline functions are reused unchanged.
- `next_day_research` adapts only the label-selection anchor and adds versioned
  snapshot/Manifest/model bindings. Historical anchors remain nominal input-date
  anchors for original split membership, not historical availability claims.
- New models use `next_day_model` / `next_day_dataset` artifact kinds in a new
  existing-schema AnalysisStore. The original `load_model`, daily analysis and
  operating registry cannot select them. No second estimator or market DB exists.
- `research_shadow_request_v2` uses the existing append-only journal, source/model
  verification, idempotence, recovery, outcome attachment and metrics. Predictions
  carry `research_shadow_event_v2` and the new event rule. Existing outcome envelopes
  and journal transaction mechanics are reused. Keep separate v1/v2 journal files;
  semantic record/verify/resolve rejects cross-contract prediction histories.

Historical labels skip one *observed* session. Their calendar/session basis and
unknown listing/suspension/corporate-action gaps remain explicitly unverified;
no absent bar is interpolated. Known unavailable dates and protected intervals
block crossing labels. Live candidates instead require exact retained calendar
history, the skipped session and all five event sessions. Their eventual outcome
must contain exactly those verified future sessions, irrespective of missing rows.
The skipped session is additionally required for the original quality/continuity
check at outcome time; it is retained separately as `future.gap` with source/batch
references covered by the outcome fingerprint. Its prices affect neither the feature
vector nor the event barrier calculation. Missing gap evidence keeps Pending. This
reproduces the training segment check without including the gap as an event session.

The original training and tuning roles are retained. The unread 2024-07-01 through
2025-06-30 Final Test remains sealed. The post-seal development diagnostic window
is reused only as development diagnostics, never represented as a new independent
final holdout. Label horizons crossing split boundaries are purged. Test rows are
never passed to fitting or parameter selection. Old/v2 scores are not comparable
as the same event and neither is advertised as calibrated stock-rise probability.

## Executable offline commands

Create a new pinned request and new output/pin directories, never rewrite originals:

```json
{
  "version": "next_day_research_request_v2",
  "dataset_version": "operator-selected-distinct-v2-version",
  "contract": "use the exact CONTRACT object from data.next_day_research",
  "base_request": "the complete independently verified real_market_dataset_request_v1 object"
}
```

The string placeholders above describe objects, not accepted request values. Import
`CONTRACT` and insert the complete base request into the JSON document. All fields
are version-checked. The base request's current quiescent operational whole-file
reference may differ after legitimate new ingestion: retain the old request and
PIN, create a separate current reference for this new request, and reverify every
original batch/source. Never silently replace a historical PIN to pass validation.

```sh
dsv real-market-dataset prepare --request "$V2_REQUEST" \
  --request-pin-file "$V2_REQUEST_PIN_FILE" --output "$NEW_PREPARED_DIR" \
  --pins-root "$NEW_PREPARED_PINS"
dsv real-market-dataset verify --request "$V2_REQUEST" \
  --request-pin-file "$V2_REQUEST_PIN_FILE" --output "$NEW_PREPARED_DIR" \
  --pins-root "$NEW_PREPARED_PINS"
dsv real-market-dataset train --request "$V2_REQUEST" \
  --request-pin-file "$V2_REQUEST_PIN_FILE" --output "$NEW_MODEL_DB" \
  --pins-root "$NEW_MODEL_PINS"
```

Training returns `insufficient_data` with real unchanged minimums if neither
conditional direction is trainable. It never replaces real data with synthetic
samples. Trained results retain validation/test metrics, baselines and per-ticker/
per-year diagnostics. `next_day_research.evaluate` rederives the dataset and scores
without writes and must reproduce the retained evaluation exactly.

For manual shadow recording, construct a separate pinned request identical to the
existing v1 shadow request shape, with `version=research_shadow_request_v2`, the
new model DB/version/independent source PIN root, current verified operational
references and the calendar-derived second-session target. Use the existing
`shadow-prediction record`, `verify`, `resolve`, `recover` and status commands as
in [the shadow runbook](research-shadow-forward-evaluation.md). Store the model in
a separate source directory and the new journal/intents outside that protected
model directory, with PINs separately retained. Never point v2 commands at v1's
journal. Recording twice must append no duplicate prediction. Pending/outcome
records advance the head; retain the newly acknowledged head before replay.

Only actual future executions with available inputs and a previously created model
are candidates. Historical runs explicitly use `historical_replay`; offline fixtures
remain in the fixture group. A successful collection is independent of a blocked
research operation. No scheduling or network activation is performed by these CLI
commands. A failed or interrupted run uses the existing retained-intent recovery;
never recreate a missing original or attach a speculative outcome.

## Remaining operating requirements

A future cycle requires original successful data, compatible v2 weights, sufficient
history, pinned current source references, verified future calendar coverage,
recording before the fixed deadline and all five later original outcome responses.
Collection permissions, expiry and request budgets still apply independently.
In particular, an event extending beyond the current policy's allowed dates needs
a separately authorized future collection period before outcomes can be obtained.
Repeated real cycles and failure/recovery review are required before separately
approving automation. Model training is not performance approval; PIT, performance,
genuine-forward and execution approval remain false. No model is auto-registered.

## Actual research execution: November 15, 2026 KST

The existing sources produced **4,725 observations / 945 dates / 5 tickers** and
4,483 feature samples. Source database and original PIN files were unchanged.
The new request explicitly retained a current operational whole-file reference
because November 13 was ingested after the older training request. Original
batch references and the original training/tuning roles were verified unchanged.

| Direction | Training | Validation | Development diagnostic |
| --- | ---: | ---: | ---: |
| Up | 1,234 | 291 | 642 |
| Down | 1,091 | 270 | 722 |

Training remained January 3, 2022–December 28, 2023; validation January 2–June 28,
2024; the reused development diagnostic window July 1, 2025–November 12, 2026.
The sealed July 1, 2024–June 30, 2025 Final Test was not read. Label horizons were
purged at partition edges. Both directions passed the unchanged real minimums and
were fitted with the existing trainer. Model creation was November 15 at
01:34:03.519989 KST; training code was commit
`1c4d940cb3ce5cbdb1f6d5d1d05d30bfa268dd8d`. Later consumer/diagnostic additions
reloaded those exact weights and reproduced their retained evaluations.

| Diagnostic direction | N | Accuracy | Balanced accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Up | 642 | .6199 | .5307 | .3598 | .2980 | .3260 | .5589 | .3341 |
| Down | 722 | .6607 | .5276 | .3333 | .2312 | .2730 | .5725 | .3280 |

The existing training-majority baseline accuracy was .6916 / .7244 respectively,
above the model's accuracy. Its AP was .3084 / .2756; reversal-strength AP was
.2868 / .2960. Up did not beat the original validation baseline margin; down met
that local validation rule. Neither result grants performance approval or proves
future accuracy. Complete validation/diagnostic, ticker/year metrics and baseline
comparisons are retained privately and reproduced with the existing diagnostics.

Four separate v2 research candidates were recorded for 000660, 005380, 005930 and
035420. Input is November 13, target November 17, with exact outcome sessions
November 17, 18, 19, 20 and 23. 051910 retained the unchanged no-directional-context
block. All four are Pending; zero are evaluated and no Historical Replay was
counted. Duplicate execution and checkpoint continuation retained the same head;
source/model/feature/score replay succeeded with the isolated installed wheel.
A development execution was interrupted after durable Pending recording; retained
report/head PINs supported exact continuation without replacement or duplicate.

The original v1 four predictions and Pending records remained byte-identical.
All **427** pre-existing protected files retained SHA-256, including source DBs,
original receipts/PINs, Phase 2, model, evaluation artifacts and LaunchAgent. There
were zero provider requests and zero added market observations in this task.
No model was registered and no automatic research execution was enabled.

Artifacts are separate under `Research/next-day-v2-20261114T140641Z` and matching
`ResearchPins/next-day-v2-20261114T140641Z`: request lineage, snapshot, Manifest,
`models/analysis.sqlite3`, diagnostics, forward request, immutable journal, Pending
report, recovery/verification reports and independently retained PINs. Exact
future outcome verification remains Pending. The current collection policy does
not cover November 23; a separately approved collection period is required for
that source and subsequent outcome attachment. No policy was amended here.
