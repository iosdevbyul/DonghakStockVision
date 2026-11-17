# Memory-guided finite Gemma research

This path reuses the existing ResearchAgent port, local OllamaProvider,
LLMResearchAgent audit/validator, Autonomous Runner, immutable AnalysisStore,
Python worker, resource reservation and result adoption. It does not collect
KRX data, train an LLM, open Final Test, promote a champion or install a service.

## Frozen comparison

`memory_guided_session_v1` registers three arms: `rule`, `gemma_blind`,
`gemma_memory`. Each has the same dataset, source PIN, horizon, stock policy,
unexecuted candidate catalog, purged internal folds, fixed prior baselines,
seed 42 and budget (1–4 completed/failed Python experiments, at most twice that
many proposals). Registration precedes proposals and stores the original source
and memory database hashes. New plan/code identities are separate from original
plans; no old PIN or experiment ID is changed. The original study's complete
catalog and dataset contracts remain intact. The finite candidate scope is a
validated subset passed to the existing runner.

A uses the existing predeclared host ranking and the same allowed internal memory.
B sees candidate definitions and objective but no history, prior scores, leader or
its own prior feedback. Completed candidates are excluded solely for duplicate
prevention. C sees only bounded internal-fold evidence from the registered
same-dataset/cohort sources, plus its own completed results. It never receives
excluded-symbol metrics, Q3 diagnostics or the later selection assessment.
Memory models are not ranked using those excluded scores. Old experiments count
as memory, never as new Gemma-selected training successes.

The existing 72 experiments may be audited read-only. Each comparison session
permits only same-cohort/dataset memory references, not a pooled cross-dataset
score. Original experiment IDs must belong to the original pinned plan and match
its configuration, result, dataset and checkpoint SHA. Array positions are never
IDs. Request/model/source/configuration bounds are checked before execution.

## Hypotheses and feedback

The provider returns a schema-bounded existing candidate, short hypothesis (at most 60 characters per text field),
problem, expected improvement, failure condition, rationale and zero-or-one
original experiment/metric citation. Citations must exist in the supplied view
and include that metric. A nonempty history requires evidence. The host adds
frozen cohort/horizon, baseline, validation/falsification conditions, minimum
evidence, one-experiment cost and prompt version; these are host policy, not
LLM-derived claims. The host derives the hypothesis ID and retains the original
response plus provider/model revision and prompt audit. LLM text cannot create
code, parameters, source ownership, orders or network tools.

Rejected proposals are retained and do not silently execute a Rule fallback in
the controlled comparison. Invalid or incomplete suggestions consume proposal
budget, not successful-training counts. Model release must be verified before
Python launch. Existing shared reservation, CPU/RSS/time/disk/AC/thermal guards
remain enforced. Another project's resident Ollama model is never forcibly
unloaded. The existing model is reused; no download/install command exists.

Each accepted selection is linked to its immutable terminal result and internal
fold/baseline/robustness metrics. Feedback includes the original result ID and
whether observed internal AP gain matches the proposed direction. Internal pass
requires positive baseline AP gain, worst fold BA >= .5 and overfit gap <= .15.
It is not independent evaluation or a causal explanation. Exact results can be
adopted across arms with the same new plan/code/dataset; cache hits are distinct
from actual Python training. Inferencing time and Python time/RSS are separate.

Every `run` performs at most one proposed experiment and reconciles committed
feedback first. A crash after worker commit can resume without another fit or
proposal for that completed budget slot. Existing attempts/worker locks handle
worker failures; a rejected/interrupted proposal remains in the audit. Reports
are immutable new files. Do not reuse an output report pathname. Repeated report
reads do not train, infer or mutate original sources. This is a user-started finite
session, not a 24/7 daemon.

## Commands

Prepare a private JSON request with `version`, `source` (`root`,
`plan_pin_file`), `memory` (one to six references with the same fields),
`horizon` (1/5/20), `stock_policy` (pooled/heldout), `budget`, and the existing
`ModelConfig` object for pinned `ollama` / `gemma4:e2b`. Use a new session root
beside the original studies so the existing heavy-work lock is shared. Use a new
separate PIN directory. Three separate arm directories are created beside it.
Never use an original source directory as the destination.

```sh
dsv memory-research prepare --root "$NEW_SESSION" --request "$REQUEST" \
  --request-pin-file "$REQUEST_PIN" --pins-root "$NEW_PINS" --report "$NEW_PREPARE_REPORT"
dsv memory-research run --root "$NEW_SESSION" --session-pin-file "$NEW_PINS/session.pin" \
  --arm rule --report "$NEW_RULE_REPORT"
dsv memory-research run --root "$NEW_SESSION" --session-pin-file "$NEW_PINS/session.pin" \
  --arm gemma_blind --report "$NEW_BLIND_REPORT"
dsv memory-research run --root "$NEW_SESSION" --session-pin-file "$NEW_PINS/session.pin" \
  --arm gemma_memory --report "$NEW_MEMORY_REPORT"
# Repeat bounded run actions while budget and native resource guards permit.
dsv memory-research report --root "$NEW_SESSION" --session-pin-file "$NEW_PINS/session.pin" \
  --report "$NEW_PRIVATE_REPORT"
dsv memory-research publish --root "$NEW_SESSION" --session-pin-file "$NEW_PINS/session.pin" \
  --report "$NEW_SANITIZED_REPORT"
```

Private reports contain reference/evidence detail and are not API publications.
The allowlisted `memory_guided_publication_v1` contains model provider/revision,
arm counts/costs/internal metrics, public hypothesis/experiment IDs and sanitized
hypothesis text. It excludes raw prompts, private paths/PINs, source rows and
checkpoints. Unsupported fields, nonfinite values or approval promotion fail.

## Existing Research Lab API

An operator may approve a separate index with version
`research_lab_publication_index_v2`, historical `entries` (possibly empty) and
`memoryReports: [{"report":"memory.json","sha256":"<physical SHA-256>"}]`.
The existing independently pinned index, file limits, private permissions and
local access controls apply. Version 1 indices and historical reports retain
exact behavior. No original publication/index is overwritten automatically.

Use the existing `/api/v1/research/lab/reports` catalog and report-by-ID route;
the additional catalog item has `researchKind=memory_guided`. Web/iOS need no
new endpoint or direct Ollama access. Loading a publication does not start
research or change the operating model. Publication approval authenticates local
bytes only, not independent historical chronology or performance.

## Interpretation and retention

Keep the frozen session, separate PINs, original sources/checkpoints, arm memory,
request/proposal audits, worker logs, resource audits and feedback together.
There is no cross-file atomic snapshot: original reference hashes are verified
before resumed execution, source changes fail closed, and the existing journal
and SQLite owners protect their individual commits. Retain failures for audit.

The original internal folds are already exposed development evidence. This work
cannot turn them, Q3 diagnostics or PR #248 results into new independent tests.
Calendar/class insufficiency must remain visible. A/B/C scores and costs on a
small finite budget are descriptive; stochastic model outputs, caches and partial
budgets must be reported rather than selecting only successful proposals.
Generalization superiority remains `NOT_ESTABLISHED`; PIT qualification,
performance approval and execution authorization remain false.
