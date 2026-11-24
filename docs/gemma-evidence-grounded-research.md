# Evidence-grounded Gemma research (Phase D)

This extension uses the existing finite campaign, immutable AnalysisStore, managed
agent, Python worker, exact-cache adoption and checkpoint recovery. It adds no
collector, learner, provider, scheduler or investment authority. Development tests
with deterministic transports are not observations of Gemma's research quality.

## Registered comparison

`research_campaign_plan_v2` owns a single 2-cohort × H1/H5/H20 × 5-arm matrix.
Each child uses `memory_guided_session_v3`. Historical campaign plan v1, session
v1/v2, publication v1/v2 and Research Lab index v1/v2/v3 remain readable.

| Arm | Selection | Permitted evidence | Output budget |
| --- | --- | --- | --- |
| `rule` | Existing frozen rule | Existing internal memory | No LLM |
| `phase_c` | Original Phase C MemoryAgent | Original last-two evidence and own feedback | Original 512 tokens |
| `verified` | Grounded citation/contrast selection | Verified, representative evidence with period detail | 128 tokens |
| `compact` | Same grounded selection | Same evidence and exact metrics, shorter representation | 128 tokens |
| `value_ranked` | Grounded ranked selection and low-value gate | Same compact evidence plus pre-outcome information/cost proxies | 128 tokens |

The B arm is the Phase C **memory** agent, not its blind agent. All arms use the
same dataset, Source PIN, allowed candidate space, learner, internal folds, seed,
baselines, maximum candidate evaluations and decision criteria. Token differences
are declared interventions. Never interpret an unexecuted cell as zero AP.

The frozen schedule rotates and reverses five-arm orders. Cache reuse is checked
only after an independent admissible selection. Other arms' selected candidates,
results and cache availability are excluded from the agent's evidence view.
Permitted prior studies are frozen before the campaign; a completed own result may
inform that arm's subsequent proposal. These reused internal evaluation periods
are development evidence, not fresh independent generalization evidence.

## Evidence ownership

The catalog calls the existing historical context, original result, terminal and
checkpoint validators. It retains original Experiment ID, parent (when actually
recorded), dataset/cohort/horizon, source/plan identity, configuration, checkpoint
SHA-256, AP/BA/Brier, baseline differences, local artifact time and internal period
provenance. Missing information stays unavailable. Final Test, Q3 diagnostics,
held-out scores and future results do not enter retrieval or value ranking.

Evidence selection is deterministic: relevance to allowed masks/families, then
original Experiment ID for ties; one claim-bearing failure, success and neutral
case where available; at most three records. Coverage and omissions are retained.
A case without an available factual numeric claim cannot support a numeric claim.
Rejected proposals without an executed experiment must not acquire invented
Experiment IDs or model performance.

Citation keys are derived from the original experiment, verified record content
and allowed scope. They are not array offsets. The private pack owns the full
alias mapping. The response chooses only allowed candidate and citation keys,
a source-backed claim, an actual configuration contrast and a bounded ranking.
The host maps these to the original identifiers and applies the unchanged common
proposal and memory validators. A citation does not establish causation. Invalid
citation, parent, metric, scope, schema or factual claim fails closed; there is no
Rule fallback.

Catalog reads are not an atomic snapshot across SQLite, checkpoint files and pin
files. Existing content hashes are verified, the original SQLite is hashed before
and after a catalog read, and frozen prior stores are rechecked at session load.
Concurrent changes fail the read. This consistency check is local integrity, not
independent authentication of chronology. There is no persistent raw-body cache. A v3 campaign may retain one fully
validated immutable dataset and at most thirteen verified context owners within
one cohort preparation. Cache hits require private-file integrity, the frozen DB
hash, original content hashes and fresh terminal/checkpoint validation. The
cache is released at the cohort boundary; it never substitutes cached results
for a newly selected experiment or accepts a JSON claim of prior verification.

## Cost and value

Full context is bounded to 12,000 UTF-8 bytes; compact context to 4,500. Excess
context fails closed. Compression keeps the same evidence identities and numeric
precision. Actual provider token counts are retained only when returned; missing
counts/segments/RSS are null, not estimates. Host preparation, load, inference,
unload and total wall time are separate. Failure costs are retained too.

Ranking uses exact-configuration duplication, new mask/family/parameter dimensions,
permitted known-failure similarity and an explicitly heuristic family cost order.
It never predicts AP or reads an unselected peer result. Repeated similar failures
without two novel dimensions have no predeclared information value. An exact
request fingerprint is durably reserved before inference, preventing an identical
failed request being silently replayed. After interruption, use original campaign
recovery; do not erase reservations or rewrite checkpoints.

## Execution and approval

The matrix is prepared before a new long-running study is approved. Preparation
and read-only reports do not authorize an inference window. Keep protected inputs,
old reports and pins unchanged. Do not use Phase C's expired execution approval.
No other project's Ollama model may be stopped or unloaded. Native AC/thermal,
CPU/RSS/disk limits and serial LLM/worker execution remain in force.

Use existing `dsv memory-research campaign-prepare`, `campaign-run`,
`campaign-recover`, `campaign-report` and `campaign-publish` commands. Each command
writes a new report path. The v2 request also freezes `execution_steps`.
After explicit approval, `campaign-start --execution-approved` creates exactly one
local execution window. Repeating start returns the original window and does not
reset its deadline. `campaign-run` rejects an unstarted v2 campaign and enforces
the cumulative step, inference and wall budgets.

A full campaign declaration may outlive the actual
approved measurement window; report every missing cell and its reason.

## Read-only Research Lab consumer

The approved index v3's existing `campaignReports` entry can point to
`research_campaign_publication_v3`. Existing Lab list/detail HTTP routes remain the
consumer interface for web/iOS. The campaign includes five-arm analysis and each
child's `memory_guided_publication_v3`, whose arms include `grounded` diagnostics:

```json
{
  "version": "grounded_diagnostics_v1",
  "records": [],
  "loadSeconds": 0,
  "inferenceSeconds": 0,
  "unloadSeconds": 0,
  "validProposals": 0,
  "calls": 0
}
```

An empty list means no executed grounded proposal. A measured segment can be null
when a call occurred but the provider could not return its measurement. Records
expose allowlisted validity, ranking of actual candidate IDs, context sizes,
omitted counts and sanitized failure classes. They exclude source PINs, private
paths, full prompts, credentials, raw KRX data and model binaries. This feature
does not publish a private report automatically or change the existing approval
boundary. Web/iOS UI changes are outside this PR.

## Interpretation

Separate evidence accuracy, inference efficiency and Python candidate quality.
Report AP, BA and Brier together, worst-fold outcomes, training/cache counts,
per-cohort/horizon comparisons and incomplete budgets. Unequal completion counts
cannot support a simple mean ranking. Existing minimum four evaluations per arm,
20 non-overlapping calendar anchors, ten observations per class and baseline/AP,
BA and Brier guards remain unchanged. Replaying Phase C's known failure cases is
a development regression check, not a new independent result.

`pit_qualified`, `performance_approved`, model promotion and investment execution
remain false. No 24/7 activation, model fine tuning, new KRX request or paid LLM
request is performed by this feature.

## Registered first measurement window

The Phase D matrix reserves four evaluations per arm/cell (120 slots). The first
proposed execution window is separately limited to 10,800 seconds, 24 Gemma
inferences and 30 dispatches, with the original 900-second step limit and a
4,096 MiB campaign disk bound. The first three dispatches are calibration; they
do not authorize extra time/calls. The full schedule is fixed before results.
Missing slots remain unavailable, including when the first window cannot reach
the minimum four evaluations per group. These limits are a proposal until the
operator explicitly approves the new long-running study.
