# Autonomous direction research (v1)

This CPU-only research engine runs a finite search against an immutable, locally
verified KRX daily snapshot. It does not need a collector, a new trading date,
credentials, network access, or a running market LaunchAgent. It does not change
v1/v2 reversal models, their events, forward journals or operational registration.

## Owners and lineage

`prepare_real_market_training` verifies original archived responses and produces
an access-bounded snapshot. The existing protocol supplies the original Training,
Validation and sealed Final Test boundaries. Preparation reads only Training and
Validation. It does not read operational SQLite or open the sealed Final Test.
Legacy research archives retain their existing original-byte/SHA verification
semantics; missing receipt or independent publication authority is not invented.
Original databases and PINs remain unchanged. New dataset, snapshot, source and
plan PINs are retained separately from the experiment DB.

`models.directional` creates distinct `close_direction_h1_v1`, `h5_v1` and `h20_v1`
labels. The target is `future_close / input_close - 1`, using the next 1, 5 or 20
observed daily bars. Absolute returns at most 20 basis points are excluded as
neutral. Prices are unadjusted; corporate-action/quality gaps fail the existing
segment checks, with no interpolation. These are observed-session horizons, not
independently authenticated exchange calendars. Future prices enter labels only.
The feature vector uses causal original OHLCV/value history. Delayed retrospective
collection does not prove historical availability; these labels are **not live
forward compatible** under the KRX next-day delivery policy. Their scores are
uncalibrated classifier scores, not a forecast of the probability of a price rise.

Daily OHLCV/value is available. The selected source has no authenticated intraday
bars, sector/index history or complete calendar proof for qualifying aggregated
weekly/monthly observations. Those timeframes are reported as unavailable or not
qualified, rather than synthesized.

## Frozen search and confirmation

The first search space has twelve configurations per horizon: the existing three
linear regularization settings and two HistGradientBoosting leaf limits, two
RandomForest depths, plus five additional feature-mask combinations. It reuses
all eight existing features and four masks (all, momentum, flow, range). This
version does not add RSI, MACD, new moving-average periods, LightGBM or XGBoost.
The original five-candidate reversal catalogue is unchanged. Seeds are 42; CPU
threads are one. Configuration identities bind the training population, folds,
implementation hash and dependency versions. Changing source/runtime requires a
new explicit study, not mutation of a running experiment.

Three expanding, purged walk-forward folds lie entirely inside original Training
(60/73/86/100 percent boundaries over its observed dates). Scaling and final model
fitting use Training only. Label windows crossing any fold/split boundary are
excluded. Existing conservative population/class minimums apply. Each search
records balanced accuracy, ROC-AUC, AP, precision/recall/F1, Brier/calibration,
ticker/quarter stability, train/evaluation gap and causal/prior baselines.
Illustrative 20-basis-point net event returns are overlapping event means, **not
an executable portfolio backtest or investment return**.

The generator first covers model families, then follows training-internal
stability-adjusted AP leaders, preferring reduced complexity after an overfit gap.
It never uses confirmation metrics to choose a subsequent trial. After the finite
catalogue finishes, one winner per goal is locked and its separate original
Validation confirmation is consumed **before** evaluation. A crash cannot reopen
it. Successful replay must reproduce the exact frozen model SHA. Every trial and
selection rationale is retained. Different horizons are never ranked together.

Promotion requires all fixed discrimination, baseline-margin, cost, calibration,
period/ticker stability and overfit gates. The existing historical Validation has
prior cross-study exposure, so independent confirmation is **not established**:
these studies cannot promote a Research Champion on those scores. A failure keeps
existing champions/models. `champion_check` also enforces a minimum 0.02 balanced
accuracy improvement against an eligible incumbent. It is a decision contract,
not permission to reuse a consumed confirmation population. A future replacement
needs separately eligible independent evidence and a versioned comparison owner;
this release does not silently replace a prior champion or operational model.
Checkpoint bytes and all earlier results are preserved. There is no pickle-load
interface; reproduction refits the pinned configuration. Linear parameters also
have the existing safe JSON representation.

## Memory, restart and resource boundary

The existing `AnalysisStore` supplies content-addressed immutable artifacts,
update/delete protection, checksum reads and atomic result/terminal transactions
in a new `experiments.sqlite3`. No second prediction journal is introduced.
Completed/failed terminals determine the remaining queue; replay skips completed
work. Attempt/phase/resource/run records retain history. An interrupted parent
leaves a recoverable attempt. A bounded second attempt can adopt only a matching
checkpoint, never overwrite it. Exhausted retries become explicit failures.
A partial SQLite transaction rolls back. Uncommitted checkpoint/temp files remain
as audit/recovery artifacts, subject to the study disk bound.

On the 16 GB Intel Mac, execution uses one CPU thread, one worker, low priority,
300-second wall/CPU budgets, a 1 GiB child RSS watchdog, at most 16 MiB per model,
512 MiB per study, a 1 GiB free-disk reserve and 60-second error backoff. Unknown
resource readings pause work. Native AC power is required; thermal serious/critical
and free-memory percentage below 20 pause/stop work. Probes use `pmset`, Foundation
thermal state and `memory_pressure -Q`. These conservative OS observations are not
hard kernel memory reservations; RSS/thermal polling has a short detection delay.
The watchdog terminates only its own child process group. There is no busy loop.

A runner lock prevents duplicate parents; a worker lock detects an orphan still
finishing. A shared heavy-work reservation in the study parent directory serializes
studies and future local-agent work. The worker inherits that reservation so
parent death cannot free it while training continues. Put all studies under the
same dedicated Research parent. A manual `worker` command is internal, not an
operator entry point. CPU-heavy agent proposal and training must remain sequential.
Do not move studies or bypass the common reservation directory.

`models.research_agent.ResearchAgent` is a runtime-independent, bounded proposal
interface. The host validates returned IDs against its frozen allowed set; agents
receive no raw source, secrets, confirmation population or execution authority.
The implemented backend is rule based. A future Gemma 4 E2B adapter is a separate
PR: no Gemma installation/runtime, MLX dependency, GPU assumption, model download
or LLM/learner concurrency is introduced here. Intel CPU support is the baseline.
The host's immutable plan, validation gates and resource reservation retain control.

## Operator commands

Install the package with its existing `[learning]` extra in a dedicated environment.
Keep paths/PINs private. A request has exactly:

```json
{
  "version": "autonomous_direction_source_v1",
  "dataset_version": "unique-reviewed-study-version",
  "research": {"database": "/absolute/existing/research.sqlite3", "pin": "retained-original-pin", "source_contract": "legacy_research_v1"},
  "protocol": {"path": "/absolute/existing/protocol.json", "sha256": "retained-original-sha256"},
  "tickers": ["000660", "005380", "005930", "035420", "051910"],
  "cutoff": "original-approved-cutoff-in-UTC",
  "horizons": [1]
}
```

Reuse the actual source contract/protocol/cutoff, rather than copying placeholders.
Retain the canonical request digest in a separate private PIN file. New paths must
not already exist or overlap protected source locations.

```sh
dsv autonomous-research prepare --request "$REQUEST" --request-pin-file "$REQUEST_PIN" --root "$STUDY" --pins-root "$PINS"
dsv autonomous-research run --root "$STUDY" --plan-pin-file "$PINS/plan.pin" --steps 3 --report "$STUDY/run-1.json"
dsv autonomous-research status --root "$STUDY" --plan-pin-file "$PINS/plan.pin" --report "$STUDY/status-1.json"
# Exit and restart; completed experiments are skipped.
dsv autonomous-research run --root "$STUDY" --plan-pin-file "$PINS/plan.pin" --steps 9 --report "$STUDY/run-2.json"
dsv autonomous-research reproduce --root "$STUDY" --plan-pin-file "$PINS/plan.pin" --experiment-id "$EXPERIMENT_ID"
dsv autonomous-research render-agent --root "$STUDY" --plan-pin-file "$PINS/plan.pin" --output "$STUDY/research-agent.draft.plist"
```

Reports use new filenames; writes never overwrite. The private DB retains full
metrics/source/model lineage; stdout contains bounded counts/state, not PINs or
credentials. A valid run advances completed count with `network_requests=0`.
`SEARCH_EXHAUSTED` stops training when all configurations have terminals; a
periodic invocation may inspect status but cannot refit the same queue forever.
An explicit, reviewed new dataset/search-space version is required for expansion.
Missing source/checksum, insufficient data, unavailable resources, bad checkpoints
or a changed implementation fail closed. Confirmation failure consumes its gate;
recover the audit record rather than automatically evaluating it again.

The rendered five-minute Background LaunchAgent is a **draft only**. Review a
stable deployed interpreter/package path, file permissions, log retention, sleep,
login and reboot behavior before a separate activation approval. LaunchAgents
require a logged-in user and cannot promise work during sleep or shutdown. This
PR does not load/install a research agent, change power settings, or alter the
market collection LaunchAgent. Long unattended operation still needs a supervised
soak, thermal/RSS observation and operational approval. Stopping the research
runner leaves market collection and committed research artifacts intact.

All PIT, performance and execution approvals remain false. Independent publication
chronology, future generalization and investment suitability remain unverified.

## Verified local rehearsal (2026-11-15)

The installed wheel was executed on macOS x86_64 against original KRX-linked
000660, 005380, 005930, 035420 and 051910 daily data. The isolated study retained
3,055 observations / 611 dates (2022-01-03 through 2024-06-28), producing 2,155
training labels and 536 separate confirmation labels for horizon one. Fifty
warmup bars, five immature labels and 304 neutral observations were excluded;
labels crossing Training/Validation were separately excluded from their roles.
No new KRX request, market ingestion or synthetic replacement was used.

The runner completed twelve distinct configurations in separate invocations of
3, then 4, then 5 experiments: five linear, four gradient-boosted and three forest
configurations. Nine later selections referenced an earlier training-internal
leader. There were zero failures. A fourth invocation executed zero experiments
because the search was exhausted. Explicit refitting reproduced checkpoint SHA
and fold metrics. The maximum observed worker RSS was 132.7 MiB; the longest
bounded worker took 7.62 seconds in this rehearsal. These observations do not
replace the configured watchdog limits or a long unattended soak.

| Family (best internal selection per family) | Mean training-internal AP | Stability-adjusted AP |
| --- | ---: | ---: |
| LogisticRegression | 0.4851 | 0.4501 |
| RandomForest | 0.5091 | 0.4827 |
| HistGradientBoosting | 0.5300 | 0.4927 |

The locked gradient-boosted winner's one confirmation evaluation had balanced
accuracy 0.5328, ROC-AUC 0.5583 and AP 0.5190. Prior-baseline AP was 0.4627 and
momentum-baseline AP was 0.4614. Its Brier score (0.2494) was worse than the
training-prior baseline (0.2486). Promotion was rejected for independent
confirmation not being established, overfit gap and calibration baseline.
**No Research Champion or operational model was promoted.** These figures are
retrospective development evidence, not independently verified future performance.

The before/after audit matched all 559 current protected files, plus 23 additional
files retained in the earlier audit (including original operating source/config
references). Research DB remained 1,099,255 rows / 1,160 dates / 993 tickers;
operational DB remained 26,302 rows / 28 dates / 942 tickers, latest 2026-11-13.
The existing v1/v2 models, eight forward candidates and journals, original PINs,
Final Test and market LaunchAgent were preserved. The research agent plist was
rendered only; autonomous unattended execution and any LLM integration remain
separate activation/integration steps.
