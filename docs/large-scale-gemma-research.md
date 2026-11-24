# Multi-cohort, multi-horizon controlled Gemma campaigns

Phase C consumes the Phase B finite research sessions, original study lineage,
immutable AnalysisStore, Python worker, OllamaProvider, schema/validator, shared
resource lock and native guards. No new collector, learner, market database,
scheduler or dashboard is installed. The research agent selects allowed experiments;
Python owns fitting and measurement. Model promotion and trading remain disabled.

## Preregistered design

A campaign contains six cohort/horizon strata, each with Rule, blind Gemma and
memory Gemma: 18 arm cells. Horizons are H1/H5/H20 and two original cohorts are
required. The default target is eight candidate evaluations per arm (144 slots),
not 144 mandatory fits. Each child is a versioned `memory_guided_session_v2`;
legacy v1 remains capped at four and keeps its publication contract. Insufficient
unexecuted catalog capacity reduces effective budgets explicitly or blocks the
stratum. New plans and PINs never replace original study identities.

Freeze original source/dataset references, model revision, candidate catalog,
seed 42, evaluation policy, inference reservations, wall/disk/step limits and
calibration size before running. A small calibration batch measures cost without
changing model-selection policy or total ceilings. The total wall window starts
with child preparation. Stopping and resuming does not reset it. Unknown costs
from interrupted attempts remain unknown; inference reservations are consumed
conservatively even if a call cannot later be proved.

The intended local envelope is 7,200 seconds, 36 inference reservations, 4,096 MiB
of campaign-owned files and at most 900 seconds per step, including supervision.
The controller reserves time for final analysis and supervises only its own child.
The existing 300-second/1-GiB learner bounds, CPU thread limit, 240-second inference
limit, 7-GiB Ollama ceiling, AC/thermal/free-memory/disk checks remain unchanged.
A foreign resident model pauses dispatch without consuming the next slot. Nothing
forces another project's model to stop or unload. No unattended activation exists.

## Selection, cache and memory isolation

All arms in a stratum receive the same frozen candidate space and evaluation
budget. Six preregistered arm permutations are distributed across the six strata
and rotated by round. Planned balance does not imply realized balance after
failures or resource stops; the retained schedule and actual attempt order show
both. Actual fits, candidate slots and exact cache hits are separate measures.
A cache hit never proves superior selection or a causal efficiency advantage.

Candidate selection occurs before cache lookup. The blind arm receives no leader,
past scores, diagnosis, previous expectations or cross-arm state. The memory arm
receives only permitted same-cohort/horizon/stock-policy history and its own prior
selected results. A result adopted after independently selecting that candidate
can be its own feedback; other arms' unselected results are not memory inputs.

Campaign reasoning adds source-backed baseline failure, overfit, calibration and
period-instability associations. Counterfactual proposals cite a verified original
parent and must change an allowed configuration. Scope and profiles remain frozen;
no code, shell, SQL, credentials, arbitrary parameters or investment commands are
accepted. Prior expected improvement/falsification and observed internal metrics
are supplied to the next permitted memory step. Explanations remain hypotheses,
not established causes. Q3 diagnostics, later assessment and Final Test are not
search feedback. The original 72 experiments and Phase B outputs are prior assets,
never counted as new Phase C evidence.

## Execution and recovery

Use new private siblings of the original studies so the existing heavy-work lock
is shared. Keep PINs separately. A request contains `version=research_campaign_plan_v1`,
two `studies` entries (`source` and `memory` references), `horizons=[1,5,20]`,
`candidate_budget`, `llm_budget`, `wall_seconds`, `step_seconds`, `calibration_steps`,
`disk_mb` and the existing pinned local `ModelConfig`. Each source reference has
`root` and `plan_pin_file`. Model installation is outside this path.

```sh
dsv memory-research campaign-prepare --root "$NEW_CAMPAIGN" \
  --request "$REQUEST" --request-pin-file "$REQUEST_PIN" \
  --pins-root "$SEPARATE_PINS" --report "$NEW_PREPARE_REPORT"
dsv memory-research campaign-run --root "$NEW_CAMPAIGN" \
  --campaign-pin-file "$SEPARATE_PINS/campaign.pin" --steps 3 \
  --report "$NEW_CALIBRATION_STATUS"
dsv memory-research campaign-report --root "$NEW_CAMPAIGN" \
  --campaign-pin-file "$SEPARATE_PINS/campaign.pin" --report "$NEW_CALIBRATION_REPORT"
# Continue within the same preregistered envelope; no budget reset.
dsv memory-research campaign-run --root "$NEW_CAMPAIGN" \
  --campaign-pin-file "$SEPARATE_PINS/campaign.pin" --steps 144 \
  --report "$NEW_EXECUTION_STATUS"
dsv memory-research campaign-recover --root "$NEW_CAMPAIGN" \
  --campaign-pin-file "$SEPARATE_PINS/campaign.pin" --report "$NEW_RECOVERY_STATUS"
dsv memory-research campaign-publish --root "$NEW_CAMPAIGN" \
  --campaign-pin-file "$SEPARATE_PINS/campaign.pin" --report "$NEW_PUBLICATION"
```

Outputs must be new files in the campaign directory or its parent. A durable
attempt precedes dispatch. Inherited locks prevent simultaneous resume behind an
orphan child. Recovery checks existing worker ownership, reconciles committed
feedback and never replays an acknowledged slot. A failed cell does not rewrite
another cell. Hard limits stop dispatch safely; incomplete/blocked cells remain
visible. Reports acquire the campaign lock and reconstruct from original pinned
records rather than mixing a moving execution with a publication.

There is no cross-file atomic snapshot: source identities are verified by the
existing owners, model/result checksums are checked, and SQLite commits and locks
protect their own boundaries. A source change fails closed. Keep original sources,
frozen request/plan, independent pins, child plans, proposal/worker audits, models,
feedback, controller attempts/end records and reports. Do not rewrite old references
to conceal a mismatch.

## Analysis and interpretation

Report each stratum separately with means/medians/worst cases and AP, BA, ROC-AUC,
Brier, baseline AP gains, class counts, calendar anchors and ticker/period/regime
stability. Comparisons require the same source/split population and equal completed
candidate counts. The internal screen requires at least four candidates per arm,
20 calendar anchors and ten rows of each class per fold, an AP margin above .005,
BA regression at most .01 and Brier regression at most .002. This does not authorize
performance. Historical folds and repeated model rows are correlated: uncertainty
intervals stay null, and shared-market cohorts are not independent even when their
symbols are disjoint. No independent future evidence is manufactured.

Classifications are `INTERNAL_IMPROVEMENT_OBSERVED`, `INTERNAL_NO_CLEAR_ADVANTAGE`,
`INTERNAL_REGRESSION_OBSERVED`, or `INSUFFICIENT_EVIDENCE`; generalization remains
`GENERALIZATION_NOT_ESTABLISHED`. Lower AP, worse calibration, rejected proposals,
partial budgets and extra inference cost are valid negative findings. The default
Rule/operating policy is never changed automatically.

Costs distinguish conservative inference reservations from observed calls, worker
attempts from successful fits, physical cache savings from logical evaluation slots,
prompt bytes, available token observations, inference and worker time and RSS.
Unavailable measurements remain null. A larger raw model count is not a larger
independent calendar sample.

## Existing Research Lab API

An operator may approve a separate `research_lab_publication_index_v3` with
`entries` (possibly empty) and `campaignReports` entries containing `report` and
physical `sha256`. Existing index pinning, permissions and local access control
apply. Version 1/2 publications remain supported. The existing report catalog and
report-by-ID routes serve approved snapshots; no new endpoint or UI is created.

```json
{"schemaVersion":"research_lab_reports_v1","items":[
 {"reportId":"<public-campaign-id>","researchKind":"research_campaign",
  "independentGeneralization":"GENERALIZATION_NOT_ESTABLISHED"}
],"total":1,"offset":0,"limit":25}
```

GET `/api/v1/research/lab/reports/<public-campaign-id>` returns nested cohort/horizon
strata with per-arm analysis, costs, sanitized hypotheses and progress/unexecuted
reasons. Web and iOS share this contract. Private paths, source/dataset/plan PINs,
raw prices, full prompts and model binaries are absent. Byte pins and reconstructed
statistics reject unauthorized/tampered publications; they are not independent
chronology or custodian authentication. PIT/performance/execution remain false.

## Actual finite window: 24 November 2026

The first registered window closed without extending its 7,200-second ceiling.
Eight steps produced seven internal evaluations: four new Python fits, three exact
cache adoptions and five Gemma proposal inferences. One H1 memory proposal failed
`memory_parent_evidence` and did not reach training. Cohort 1 ran H1/H5/H20;
foreign Ollama residency prevented the remaining steps, including cohort 2.
Actual multi-cohort execution remains incomplete. The original 72 experiments
and Phase B outputs are excluded from these counts.

| Horizon | Arm | Evaluations | AP | BA | Brier |
|---|---|---:|---:|---:|---:|
| H1 | Rule / blind Gemma | 1 each | 0.470232 | 0.502506 | 0.251733 |
| H1 | Memory Gemma | 0 | unavailable | unavailable | unavailable |
| H5 | Rule / blind Gemma | 1 each | 0.504271 | 0.508747 | 0.251161 |
| H5 | Memory Gemma | 1 | 0.492734 | 0.513773 | 0.252774 |
| H20 | Rule / blind Gemma | 1 each | 0.451971 | 0.540697 | 0.248299 |
| H20 | Memory Gemma | 0 | unavailable | unavailable | unavailable |

H5 memory inference took 228.625 seconds versus blind Gemma's 117.285 seconds.
Its observed AP was lower and calibration worse. Each executed arm has only one
candidate; all formal comparisons remain `INSUFFICIENT_EVIDENCE`. No improvement
or generalization advantage is established. No uncertainty interval or cost per
demonstrated improvement can be reported. Identical outcomes and cache savings
do not establish a selection advantage.

Recorded supervised steps totalled 1,390.996 seconds; this excludes preparation
and waiting for foreign resources. Peak parent RSS was 715,588 KiB, learner RSS
351,284 KiB and Ollama RSS 3,566,128 KiB. Peak observed Ollama CPU was 326.9%.
The learner and inference limits remained unchanged. No KRX request, foreign
model interruption, scheduler activation or model promotion occurred.

The final reconstruction and separately pinned sanitized publication passed the
existing catalog/detail API checks without changing a production mount. The
original execution baseline of 1,549 files was unchanged after calibration;
an expanded baseline added 22 pre-existing files after calibration and finished
with 1,571/1,571 unchanged. Full results, experiment IDs, citations, class counts,
period diagnostics and the research narrative remain in private research storage.
Any further execution requires a separately approved finite window; the closed
plan, results and PINs must not be reset or rewritten.

## Separately approved continuation and combined readback

A later explicit authorization permitted a separate 90-minute window, at most six
Gemma inferences and nine dispatches. It referenced the original prepared cohort-2
sessions, candidate budget eight, model, Source PINs and first-round arm order.
No replacement candidate catalog was prepared and cohort 1 was not retrained.
The original plan/PIN and closed report were retained. The extra window completed
all nine attempts with eight evaluations, six new fits, two exact cache adoptions
and six inferences; H1 memory timed out without fallback. It closed at 14:07 KST
on 24 November, within its original 90-minute ceiling.

A subsequent explicit user instruction released idle foreign Gemma residency to
prioritize this work. Already-paused request processes and unrelated services were
left unchanged. The sleeping DSV controller resumed at its committed step index;
the registered deadline and remaining counters were retained.

Combined totals are 17 attempts, 15 evaluations, ten new fits, five exact cache
adoptions and eleven Gemma inferences. Two memory proposals were rejected:
`memory_parent_evidence` and `llm_inference_timeout`. All 18 formal comparisons
remain `INSUFFICIENT_EVIDENCE`; each executed arm has one evaluated candidate.

| Cohort | Horizon | Arm | n | AP | BA | Brier | Baseline AP gain |
|---|---|---|---:|---:|---:|---:|---:|
| historical-cohort1 | H1 | rule | 1 | 0.470232 | 0.502506 | 0.251733 | 0.000147 |
| historical-cohort1 | H1 | gemma_blind | 1 | 0.470232 | 0.502506 | 0.251733 | 0.000147 |
| historical-cohort1 | H1 | gemma_memory | 0 | unavailable | unavailable | unavailable | unavailable |
| historical-cohort1 | H5 | rule | 1 | 0.504271 | 0.508747 | 0.251161 | 0.010315 |
| historical-cohort1 | H5 | gemma_blind | 1 | 0.504271 | 0.508747 | 0.251161 | 0.010315 |
| historical-cohort1 | H5 | gemma_memory | 1 | 0.492734 | 0.513773 | 0.252774 | -0.001223 |
| historical-cohort1 | H20 | rule | 1 | 0.451971 | 0.540697 | 0.248299 | 0.041920 |
| historical-cohort1 | H20 | gemma_blind | 1 | 0.451971 | 0.540697 | 0.248299 | 0.041920 |
| historical-cohort1 | H20 | gemma_memory | 0 | unavailable | unavailable | unavailable | unavailable |
| historical-cohort2 | H1 | rule | 1 | 0.500323 | 0.498503 | 0.251951 | -0.003744 |
| historical-cohort2 | H1 | gemma_blind | 1 | 0.500323 | 0.498503 | 0.251951 | -0.003744 |
| historical-cohort2 | H1 | gemma_memory | 0 | unavailable | unavailable | unavailable | unavailable |
| historical-cohort2 | H5 | rule | 1 | 0.531369 | 0.507888 | 0.251545 | 0.013898 |
| historical-cohort2 | H5 | gemma_blind | 1 | 0.533640 | 0.518056 | 0.251151 | 0.016168 |
| historical-cohort2 | H5 | gemma_memory | 1 | 0.542259 | 0.514614 | 0.250731 | 0.024787 |
| historical-cohort2 | H20 | rule | 1 | 0.507581 | 0.531947 | 0.250202 | 0.033362 |
| historical-cohort2 | H20 | gemma_blind | 1 | 0.507581 | 0.531947 | 0.250202 | 0.033362 |
| historical-cohort2 | H20 | gemma_memory | 1 | 0.445061 | 0.471936 | 0.258720 | -0.029157 |

Cohort-2 H5 memory had higher observed AP and lower Brier than Rule, while
cohort-2 H20 memory had lower AP/BA and worse Brier. Cohort-1 H5 memory was
also worse on AP/Brier. These single-candidate differences do not establish
consistent value, superiority or an approved policy change. Shared calendar dates
and repeated cached models are not independent evidence. Higher rounds were not
filled beyond the approved execution windows.

Rule performed six evaluations with three fits/three reuses. Blind Gemma performed
six evaluations with four fits/two reuses and 665.992 inference seconds. Memory
Gemma performed three evaluations with three fits, two rejected proposals and
1,121.219 inference seconds. Its five inference attempts cover fewer cells than
the blind arm's six, so the totals are cost observations, not a matched causal ratio.
Recorded supervised steps totalled 3,061.319 seconds, excluding preparation and
resource waiting. Maximum recorded parent/learner/Ollama RSS was respectively
731,308 / 386,816 / 4,570,684 KiB; maximum recorded Ollama CPU was 384.7%.
Timeout-call peak/token details remain unavailable; these are recorded maxima,
not complete continuous peaks. Existing guards were unchanged.

The new window preserved 1,710 immutable protected files byte-for-byte and verified
all 36 original artifacts in its twelve authorized append-only journal targets.
The readback first used the frozen learning implementation `b1048e1`. No real
candidate was retrained to validate the later report consumer.

`combine_execution_windows(verified_source_report, approved_window_summaries)` is
a separate read-only projection. It checks window bounds and reconstructs counters
against the source models, cache, inference and rejection counts. Report/publication
v2 adds `executionWindows`; v1 remains accepted unchanged. Each window retains its
own ceiling, actual counters and unauthenticated local chronology. This function
cannot execute a study, reset a plan or authenticate an operator. Callers must first
verify source exports and separately retained fingerprints through their owners;
rehashing supplied metadata does not grant independent authority.

The existing publication index v3 and catalog/detail routes serve this v2 snapshot.
The actual TestClient readback verified both windows without a production mount
change. Private source/plan pins, paths and raw data remain excluded. The full
private narrative retains experiment IDs, hypotheses, class counts and diagnostics.
PIT, performance approval, promotion and trading remain false.
