# Independent evaluation evidence availability

This read-only extension reuses the completed selection review, source owners,
Shadow Prediction journal, deferred resolver and research cycle. It neither fits a
model nor creates a collector, journal, scheduler or evaluation label. It does not
publish private artifacts through the study API.

## Historical audit

```sh
dsv autonomous-research evidence-audit \
  --root "$ORIGINAL_SELECTION_REVIEW" \
  --plan-pin-file "$ORIGINAL_REVIEW_PINS/plan.pin" \
  --report "$NEW_RESEARCH_OUTPUT/availability.json" \
  --pins-root "$NEW_INDEPENDENT_PINS"
```

Use new private output locations outside all original study, review and source
roots. The command retains `report.pin` separately. Source databases must remain
quiescent and match their original pins before and after metadata reads. Appended
or changed databases fail closed: retain the original snapshot and supply verified
current references to the existing Shadow workflow for subsequent observations;
do not reissue an old database PIN.

The audit validates the original review plan, separately retained assessment and
readout pins, cached report identity, original study/source hashes and dataset
owners. Historical reading permits a newer implementation while retaining the
original implementation identity; selection writers and inference workers still
require an exact implementation match. No old experiment or inference is rerun.

Inventory SQL selects only ticker/date/count metadata **outside** the sealed Final
Test. It does not select Final Test rows or payloads. The full-file checksum is
still compared for integrity. The training, original validation, source-exposed
windows, Q3 diagnostic, feature-only context and previous readout form a conservative
exposure ledger. A date never becomes fresh simply because it has no surviving
label. Unclassified dates require exposure review and preregistration; they are
not automatically admitted to an evaluation.

Per-stock absences are measured against the observed market-date union. This is
not a new official trading calendar: absent metadata does not establish a holiday,
suspension, listing, delisting or corporate action. Reasons remain UNKNOWN without
source evidence. Missing observations are never filled. The original 2026-10-02
empty response remains unchanged and is not silently classified as a holiday.

Label counts reuse the retained, original-source-verified 1/5/20-observation
readout. The existing feature-history, neutral-band, purging, embargo and maturity
rules remain owned by `research_v2` and the original folds. No new labels are
created in exposed periods. Per-ticker and input-month score/baseline partitions
are explicitly **previously exposed descriptive partitions**, not new independent
performance tests or selection inputs.

## Calendar evidence

All stocks are unioned by input/target date before counting anchors. A selected
anchor blocks the complete maximum outcome interval across all stocks/cohorts;
the next anchor must occur strictly after its end. Several stocks on one day
contribute one calendar anchor. Conflicting duplicate stock/date labels fail;
identical repeated rows collapse. The maximum is 100,000 rows and 50 stocks.

Reports distinguish total labeled rows, positive/negative rows, purged overlaps,
calendar anchors, per-stock counts and period partitions. Existing minimums remain
20 disjoint calendar anchors and at least 10 rows of each class on selected
anchors, following the historical research class-count guard. Passing these
necessary checks yields `READY_FOR_EXISTING_EVALUATOR`, not performance approval,
statistical certainty or independent publication/PIT authentication.

The original held-out-stock population is reported separately from all available
retained labels. Pooled and held-out fit roles and old policy/model comparisons
remain separate. No additional cohort or duplicated stock row is treated as a new
independent time observation.

## Frozen prospective accumulation

Use the existing, source-pinned Shadow request and head reference. Freeze the
research model and conditions into a new private file and independent PIN:

```sh
dsv shadow-prediction freeze-evidence \
  --request "$SHADOW_REQUEST" --request-pin-file "$SHADOW_REQUEST_PIN" \
  --journal "$EXISTING_JOURNAL" --head-pin-file "$EXISTING_HEAD_PIN" \
  --report "$NEW_RESEARCH_OUTPUT/registration.json" \
  --evidence-plan-pin-file "$NEW_INDEPENDENT_PINS/registration.pin"

dsv shadow-prediction evidence-readiness \
  --request "$CURRENT_VERIFIED_SHADOW_REQUEST" \
  --request-pin-file "$CURRENT_REQUEST_PIN" \
  --journal "$EXISTING_JOURNAL" --head-pin-file "$CURRENT_HEAD_PIN" \
  --evidence-plan "$NEW_RESEARCH_OUTPUT/registration.json" \
  --evidence-plan-pin-file "$NEW_INDEPENDENT_PINS/registration.pin" \
  --report "$NEW_RESEARCH_OUTPUT/readiness-001.json"
```

Registration pins model identity/artifact/version/creation time, tickers, request
and event version, feature contract, threshold, evidence-policy version and unchanged count/class guards, local registration time, and the
complete existing journal prefix. It does not execute predictions or alter the
journal. Changing the model/conditions or rewriting the prefix fails closed. A
new operating database reference can reflect appended observations, but must pass
the original verifier; the frozen model database remains unchanged.

Continue to use existing `shadow-prediction record`, `resolve`, `recover`, and
`cycle` commands for actual work. They alone own prediction creation, source/model
validation, calendar deadlines, five-session label reconstruction, append-only
outcomes, duplicate protection and recovery. The readiness consumer semantically
replays those owners; it never writes outcomes or changes Pending records.

Existing pre-registration predictions and their outcomes remain a separate stream
under their original conditions. Only post-registration, provider-backed Forward
candidates enter the new accumulation cohort. Historical Replay and offline
fixtures never enter real prospective evidence counts, even after successful
fixture outcome resolution. Corrections exclude withdrawn predictions.

The current Shadow contracts support **five observations only**, separately for
v1 and v2. They are not the same event as the historical 1/5/20-observation
research labels. Targets 1 and 20 report `UNSUPPORTED_PROSPECTIVE_CONTRACT` with
zero prospective anchors; this PR does not fabricate contracts or reinterpret
five-session outcomes. Supporting those horizons prospectively requires separately
reviewed prediction/label contracts. Do not combine v1/v2 scores or add their
same-day anchors together.

Future outcomes remain Pending until original raw sources, full event observations
and the existing time guard are satisfied. Retained verified calendar guards may
state the earliest **conditional attempt** time, but every report leaves the
earliest guaranteed evaluation time null. Neither source arrival nor a future
trading date is guaranteed. Repeated reporting is read-only; interrupted reporting
cannot commit a partial outcome. Reuse existing journal recovery after interrupted
record/resolve operations. An incomplete registration without its separate pin
must not be treated as a valid cohort.

## Actual local audit

The initial audit found 868,898 research rows / 919 dates / 993 stocks outside
Final Test and 26,302 operational rows / 28 dates / 942 stocks. Operational dates
span 2026-10-06–2026-11-13. All audited dates are covered by existing exposure
roles; no new preregistered independent historical interval was established.

| Historical target | Existing held-out anchors | Minimum | Additional anchors needed |
| --- | --- | --- | --- |
| 1 observation | 9 | 20 | 11 |
| 5 observations | 4 | 20 | 16 |
| 20 observations | 1 | 20 | 19 |

These are the existing exposed readout counts, not newly acquired evidence. The
20-observation held-out result is single-class. New qualified historical evidence,
new provider requests, new training and new official evaluations are all zero.
The existing v1 and v2 streams retain four Pending predictions each. Their model,
source and journal verification succeeded; registration does not retroactively
include these eight predictions in the new cohort.

All PIT, performance, champion promotion and execution permissions remain false;
Final Test, source/model/PIN artifacts and scheduling remain unchanged. Local
hashes, receipts and timestamps establish integrity under their owners, not
independently authenticated historical availability or future predictive power.
