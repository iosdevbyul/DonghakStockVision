# Optional local Gemma research agent

The existing autonomous research runner still defaults to the rule-based generator.
`--agent gemma4` explicitly enables the installed `gemma4:e2b` through the fixed
`http://127.0.0.1:11434` Ollama endpoint. No model install/pull, MLX, GPU dependency,
external proxy, redirect, arbitrary URL, shell tool or generated code is enabled.
The price-model learner, temporal folds, metrics and approval owners are unchanged.
Gemma weights are never trained; its memory is the retained experiment history.

## Separate frozen studies

`autonomous-research derive` consumes an independently pinned PR #240 v1 study
read-only. It verifies its immutable dataset/snapshot/source artifacts, original
request binding and current original research SQLite file SHA. It copies only
verified research artifacts to a **new** AnalysisStore and retains the same source
and dataset PINs. It does not replay raw sources redundantly, query Final Test,
modify prior experiment history or merge market databases. New plan version v2
binds the parent, implementation/dependency versions, installed model digest and
`agent_masks_v1` search space. Existing v1 plans/reports remain retained.

The new finite search has twelve configurations per direction horizon: four
previously untried causal masks (`returns_only`, `price_range`, `volume_value`,
`short_trend`) crossed with linear C=1, RandomForest depth=3, and boosted leaves=7.
It reuses the same eight original features and learners. It does not change old
reversal v1/v2 events or direction labels. Original PR #240 configurations are
excluded semantically, regardless of changed implementation IDs. Exhausted queues
stop; neither Gemma nor fallback can invent configurations or reopen confirmation.

The memory view verifies the original study's source/population ownership, then
summarizes training-internal metrics, overfit gaps, prior baselines, failures and
current leader. No raw rows, original response bytes, paths, source PINs, credentials,
model weights or confirmation scores enter the prompt. The bounded prompt retains
the leader and up to seven recent summaries, allowed candidates and remaining
budget. This is development search, not independent future performance evidence.

## Proposal validation and audit

Gemma must return `gemma_research_proposal_v1` JSON with experiment ID, hypothesis,
model family, feature mask, exact allowed hyperparameters, goal, parent ID,
expected-improvement hypothesis and rationale. The host verifies exact fields,
string/number types, limits, duplicate JSON keys, finite numbers, parent ownership,
unchanged model/feature/goal settings and an unexecuted allowed ID. Structured
Ollama output supplements those host checks; it does not replace them. Response
text is untrusted research content and is never executed. Expected improvement
is prose describing a hypothesis, never measured performance.

The agent receives a deep copy; mutation cannot enlarge the host's allowed set.
Every attempted proposal retains a start artifact before the API operation and a
separate completion/rejection artifact: prompt summary, response text, installed
model/digest, server version, UTC times, inference counts/timings, resource samples,
selection/fallback reason and unload state. AnalysisStore supplies original
immutable/checksum/atomic semantics. Private reports distinguish local HTTP
attempts, actual inference requests, zero KRX requests and zero external requests.

## Sequential CPU/resource lifecycle

The common parent-directory heavy-work lock covers proposal, unload and training.
The existing single-worker lock and inherited reservation protect restart/orphan
recovery. All studies must use the same dedicated Research parent. The loop is:

1. Check native AC, thermal, free memory and disk protection.
2. Verify the installed exact model digest and require no preexisting loaded model.
3. Load with CPU-only options: one thread, GPU layers zero, context 4096.
4. Confirm the resident digest and `size_vram=0`; request one structured proposal.
5. Persist response; request `keep_alive=0`; verify no models remain and native
   Ollama RSS falls below 256 MiB before proceeding.
6. Recheck training resources, then run/adopt the validated price-model experiment.
7. Persist learning results before constructing the next memory view.

Loading has a 60-second timeout; inference a 240-second bound and 384 output-token
cap; unload polling is bounded to 20 seconds. Prompt cap is 14,000 bytes; HTTP body
cap 65,536 bytes. The installed artifact is approximately 4.6 GB. The conservative
Ollama process RSS bound is 7 GiB (rounded artifact size plus allocation headroom).
Loading requires at least 45% native free-memory percentage on the 16 GB host;
inference stops below 10%, on battery, at serious/critical thermal state or unknown
resource probes. Native process RSS/CPU and power/thermal samples are retained.
A sampled aggregate CPU watchdog stops inference above 450% (4.5 logical cores),
with headroom over the observed 410.2% loading/prefill peak. CPU options limit model
inference threads; this is not a kernel-wide CPU quota on
Ollama's daemon/UI or unrelated applications. Sampling has a short detection delay.
The existing learner keeps its separate one-thread/1-GiB/300-second limits.

If proposal/schema/service/timeout checks fail, the frozen rule-based choice can
continue **only after release is verified**. A known active unrelated model, a
failed unload, unknown resources or a still-running request blocks training. Rule
mode in a v2 study also checks native Ollama residency before training, including
a model orphaned by a different study; it does not silently unload other work.
A down daemon permits fallback only when native OS evidence confirms no large
Ollama model allocation. No unrelated model or service is killed. An unfinished
owned request is explicitly recovered and released before another proposal.
Do not manually issue competing Ollama requests during a supervised research run.
Uncooperative external clients are outside this runner's reservation mechanism.

## Commands and fair comparison

Use the existing installed learning extra, existing local Ollama service and model.
Keep all PIN files private. Retain the installed digest in a separate private file;
this is a content identifier, not permission to download or replace a model.

```sh
dsv autonomous-research derive --source-root "$ORIGINAL_STUDY" --source-plan-pin-file "$ORIGINAL_PINS/plan.pin" --root "$GEMMA_STUDY" --pins-root "$GEMMA_PINS" --model-digest-file "$MODEL_DIGEST_FILE"
dsv autonomous-research derive --source-root "$ORIGINAL_STUDY" --source-plan-pin-file "$ORIGINAL_PINS/plan.pin" --root "$RULE_STUDY" --pins-root "$RULE_PINS" --model-digest-file "$MODEL_DIGEST_FILE"
dsv autonomous-research run --root "$GEMMA_STUDY" --plan-pin-file "$GEMMA_PINS/plan.pin" --agent gemma4 --steps 1 --report "$GEMMA_STUDY/round-1.json"
# Exit and restart: the same memory supplies subsequent choices.
dsv autonomous-research run --root "$GEMMA_STUDY" --plan-pin-file "$GEMMA_PINS/plan.pin" --agent gemma4 --steps 4 --report "$GEMMA_STUDY/round-2.json"
dsv autonomous-research run --root "$RULE_STUDY" --plan-pin-file "$RULE_PINS/plan.pin" --steps 5 --reuse-root "$GEMMA_STUDY" --reuse-plan-pin-file "$GEMMA_PINS/plan.pin" --report "$RULE_STUDY/comparison.json"
dsv autonomous-research status --root "$GEMMA_STUDY" --plan-pin-file "$GEMMA_PINS/plan.pin"
```

Both arms have the exact same frozen data, folds, allowed configurations, previous
PR #240 memory and five-selection budget. Their own newly observed histories are
separate. Exact overlapping selections reuse verified model bytes/results from
the peer, with an append-only cache-source reference; they are not trained twice.
Cache reuse requires identical plans, runtime, original data, settings and model
SHA. Reports distinguish fresh fits from cache hits. Compare original training
costs for cached models when normalizing compute; do not interpret donated compute
as a faster learner. Finite adaptive arms and previously exposed development data
do not establish statistical superiority or independent generalization.

CI uses deterministic local HTTP MockTransport and real source parsers, hashes,
fitters and memory checks; it does not depend on an Ollama server. Actual local
Gemma execution is a separate supervised integration validation.

No KRX/research DB, old Source PIN/model/history, eight forward candidates/journals,
Final Test or KRX LaunchAgent is changed. No research LaunchAgent is installed or
activated. Long unattended use needs supervised load/unload stability observation,
resource/log retention review and separate activation approval. PIT, performance,
trading/execution and operational model registration remain unapproved.

API behavior references: [Ollama generate](https://docs.ollama.com/api/generate),
[installed models](https://docs.ollama.com/api/tags), and
[resident models](https://docs.ollama.com/api/ps). Actual first data-publication
chronology and market-model performance authority remain unrelated to local LLM
API metadata.

## Supervised local execution (2026-11-15)

The existing Ollama 0.32.15 / `gemma4:e2b` installation was used without downloading
or changing models. The pinned installed digest is
`b37049369adfe3d2b653af0ab301a062ca5cbe96ace6aa0d3f2559ee9b563fc2`.
Five actual inference requests produced five valid, distinct proposals; rejected
and duplicate proposals were zero. Five real KRX-derived models were trained,
with checkpoint SHA and training-internal temporal metrics retained. No new KRX
request, raw ingestion or synthetic replacement occurred.

The process exited after the first trial, then resumed for four more. All four
follow-up prompts included newly stored results. None of the five responses
explicitly cited a new parent experiment ID (all were null), so this observation
proves memory delivery and structured selection, **not** a measured causal benefit
from memory use or superior reasoning. The rule arm independently chose the same
five configurations in another order and reused all five verified results. Thus
there were five unique fits total and zero duplicate fits, with equal five-choice
budgets and identical allowed space/data/folds.

| Candidate / mask | Mean internal AP | Stability-adjusted AP |
| --- | ---: | ---: |
| Linear C=1 / returns_only | 0.4836 | 0.4480 |
| Forest depth=3 / returns_only | 0.4928 | 0.4815 |
| Boosted leaves=7 / returns_only | 0.4939 | 0.4761 |
| Forest depth=3 / price_range | 0.5049 | 0.4876 |
| Forest depth=3 / volume_value | 0.5154 | 0.4772 |

Both arms selected the forest/price_range leader using the frozen stability rule.
Its mean AP gain over the strongest per-fold baseline was 0.0286, but neither
arm exceeded the previous PR #240 maximum mean AP of 0.5300. These different
adaptive search spaces are development observations, not independent performance
comparisons or a basis for promotion. No confirmation gate or Final Test was opened
for the five-choice comparison; seven configurations remain per arm. No Research
Champion, operational registration, PIT or execution approval was granted.

Mean actual inference response time was 169.65 seconds; mean proposal lifecycle
including load/release was 183.28 seconds. The five unique learner processes took
25.48 seconds total (5.10 seconds per fit). For the cached rule arm, normalize to
those original learner costs; donated models do not establish faster training.
The LLM added about 916 seconds of proposal work and did not produce a better
five-choice set. Its measured peak aggregate Ollama RSS was 4,857.5 MiB and peak
aggregate process CPU was 410.2%, despite requesting one inference thread. All
five resident-model checks reported zero GPU allocation and all releases succeeded.
This is evidence for supervised sequential execution, not a 24-hour reliability
or strict one-core guarantee.

The live execution wheel is retained separately for exact frozen-plan replay.
Final defensive changes add the calibrated 450% CPU watchdog, native release checks
(including rule-mode orphan protection) and atomic cache adoption. Original study
plans/PINs are not rewritten to impersonate a newer runtime. Final validation
retains separate guard/release and mathematical reproduction evidence; a changed
runtime requires a newly reviewed study. Unattended research remains disabled.
