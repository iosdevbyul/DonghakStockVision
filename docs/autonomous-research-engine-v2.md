# Autonomous stock-direction research v2

This is bounded retrospective research, not an operating prediction model or a
24/7 service. It reuses the original source verifier, AnalysisStore, worker,
ResearchAgent/LLMResearchAgent/ProviderFactory, metrics, checkpoints and resource
locks. No collector, scheduler, provider installation or training database is added.

## Versioned inputs and features

A new `autonomous_direction_source_v2` request uses the same research database,
source contract, explicit split, tickers and cutoff as the previously verified
request. `prepare` independently verifies those originals, then creates a separate
`direction_dataset_v2`, immutable `autonomous_plan_v4` and independent source,
snapshot, dataset and plan PIN files. Old plans/reports/PINs stay unchanged.

The 46-column causal vector retains the original eight columns, adding return20,
SMA/EMA gaps and distances, slope, persistence, RSI, MACD/signal/histogram,
Bollinger position/width, ATR, volatility change, volume/value changes and ratios,
interaction, candle body/wicks/streak/gap/reversal, plus six columns each for a
closed observed week/month. The largest daily window is 35 observations. EMA is
seeded at that bounded window's first close; RSI is the normalized ratio of
14-period positive/negative log-return means; ATR uses a 14-period simple mean of
true range; Bollinger uses population standard deviation over 20 closes. These
are frozen research definitions, not interchangeable third-party indicator states.

Higher-period OHLCV is aggregated only from supplied original daily bars. The
current period and the first retained (possibly partial) period are excluded.
Volume share uses the last completed period's final-session volume divided by its
own total volume, with no current-period denominator. Seven masks support legacy
eight-column, daily, weekly, monthly, daily+weekly, daily+monthly and all features.
All masks use the same eligible population for controlled comparisons. Missing,
zero-trade, excluded action or known incomplete segments fail the original quality
checks. Intraday/index/sector fields are not invented. Unadjusted source revisions,
corporate actions and unauthenticated session completeness remain limitations.

## Labels, chronology and evaluation

New `delayed_close_direction_h{1,5,20}_v2` labels skip the next observed bar and
measure the following h-observation endpoint close against that skipped bar's
close. That future base price belongs to the label, never the Feature vector.
The nominal research anchor is next observed date 19:00 Seoul. Actual source
receipt availability is retained separately; late historical collection never
becomes historical PIT evidence. The old reversal/Forward v1/v2 contracts are
unchanged and these artifacts cannot be loaded as production signal models.

Training-internal expanding windows share calendar boundaries across all tickers
and purge labels crossing a boundary. h1/h5 use two 60-date evaluation windows;
h20 uses two 80-date windows when at least 480 training dates exist, otherwise one
100-date window. Existing population/class minimums still apply. A one-fold report
cannot establish fold stability. Scalers and class/sample weights use only fold
training rows. Search never evaluates the reserved confirmation population; v4
confirmation is explicitly sealed, as is the original Final Test.

Full-population metrics describe overlapping labels. Separate greedy calendar
anchors exclude overlapping label intervals across all tickers and expose their
much smaller sample counts and metrics. Cross-sectional dependence and small
samples still prevent independent performance qualification. Neither these scores
nor Brier/calibration diagnostics are approved price-rise probabilities.

## Finite model space and adaptation

294 frozen configurations combine three horizons, seven feature masks and fourteen
profiles. LogisticRegression: C=0.1/1/10, linked lbfgs/balanced or liblinear/no class
weight profiles. RandomForest: depth3/6 with linked (32 trees, leaf30, sqrt) or
(96 trees, leaf10, 0.75 features) profiles. HistGradientBoosting: leaves7/15 with
linked (rate0.03, 80 iterations, L2=2) or (rate0.1, 160 iterations, L2=0.5).
These are finite linked profiles, not every Cartesian parameter combination.
The existing fitters and single-threaded sklearn execution are reused.

Each space has a persistent 24-terminal-experiment ceiling, including failures.
Restart resumes completed memory and remaining budget. The rule policy balances
horizons, explores unseen families/features, rewards training-internal stability
and penalizes overfit/failures. A selection retains verified history IDs/metrics,
parent, alternatives, the winner with/without history and whether memory changed
that winner. Those counterfactuals concern the same remaining pool and horizon;
they do not claim a causal improvement in general model quality.

LLM input contains bounded derived experiment summaries and fixed parameter
profiles, never raw KRX rows, credentials or source paths. The common validator
maps its ID back onto the frozen config. Prose is untrusted, not evidence. Timeout
or invalid proposals use the existing fallback only after verified unload. The
shared reservation serializes inference and fitting; native AC/thermal/memory,
RSS/CPU/disk, child deadline and checkpoint-size limits stay in force.

A paired LLM/Rule-Based choice retains the exact same candidate/history view,
source, horizon, folds and one-choice budget. Existing fit results can be reused;
a distinct rule counterfactual needs its own bounded fit before comparison is
complete. The report exposes selection latency, fit cost, cache reuse and the AP
difference. A single pair cannot establish policy superiority. Older #240/#241
results have different labels/features/populations and are historical references.

## Explicit manual workflow

Prepare a new private request using the retained source fields and the new request
version, a distinct dataset version, and `horizons:[1,5,20]`. Its request PIN is the
existing canonical `data.learning.digest(document)` contract, not the SHA of
arbitrarily formatted JSON. Use new paths; never overwrite an old study.

```sh
dsv autonomous-research prepare --request "$NEW_REQUEST" --request-pin-file "$NEW_REQUEST_PIN" --root "$NEW_STUDY" --pins-root "$NEW_PINS"
dsv autonomous-research run --root "$NEW_STUDY" --plan-pin-file "$NEW_PINS/plan.pin" --steps 9
# Safe continuation in a new process; prior terminal IDs consume the same budget:
dsv autonomous-research run --root "$NEW_STUDY" --plan-pin-file "$NEW_PINS/plan.pin" --steps 10
# Optional explicitly validated managed LLM, using the #242 private control contract:
dsv autonomous-research run --root "$NEW_STUDY" --plan-pin-file "$NEW_PINS/plan.pin" --steps 1 --agent managed --management-root "$NEW_CONTROL" --model-config "$PRIVATE_CONFIG" --model-config-sha256 "$CONFIG_SHA"
dsv autonomous-research report --root "$NEW_STUDY" --plan-pin-file "$NEW_PINS/plan.pin" --report "$NEW_REPORT"
env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 BLIS_NUM_THREADS=1 LOKY_MAX_CPU_COUNT=1 dsv autonomous-research reproduce --root "$NEW_STUDY" --plan-pin-file "$NEW_PINS/plan.pin" --experiment-id "$COMPLETED_ID"
```

Managed model validation remains a separate explicit operation under the #242 API
or ModelManagement. New controls/studies must share a Research parent for the
common reservation. Never modify the operating LaunchAgent or enable unattended
research in this workflow. Preserve executed packages, original receipts/source
references, independent PINs, request, experiment DB, checkpoint files and reports.
All PIT/performance/execution approval flags and operational registration stay false.

Exact reproduction must start with the same native single-thread environment used
by the original worker, before importing numerical libraries. On this host an
ambient-environment direct fit differed; the comparison was rejected rather than
relaxed. The command above preserves the worker's BLAS/OpenMP limits. Byte-level
reproduction is not promised across different numerical libraries or machines.
Host-policy memory counterfactuals are not an LLM no-memory ablation and do not
establish that LLM prose is a faithful causal explanation.

## Actual local KRX research evidence

The retained five-ticker source contained 3,055 original-linked daily bars over
611 dates, 2022-01-03 through 2024-06-28. Twenty-four distinct new experiments
completed (eight per horizon), with zero training failures and duplicate terminal
IDs. A new process resumed after the first nine. At the 24-trial ceiling another
run produced zero fits and zero new attempts, reporting BUDGET_EXHAUSTED. No KRX
request, remote provider, new model download or scheduler activation was used.

| Horizon | Stability-selected model / mask | Mean internal AP | Balanced accuracy | Best existing baseline AP | Nonoverlapping rows, both folds |
|---|---|---:|---:|---:|---:|
| 1 | hist_gradient_boosting / v2_legacy | 0.486322 | 0.512103 | 0.468648 | 176 |
| 5 | random_forest / v2_monthly | 0.577576 | 0.554073 | 0.512780 | 77 |
| 20 | random_forest / v2_monthly | 0.617919 | 0.490832 | 0.552947 | 29 |

AP is training-internal exploration, selected after search, not independent future
performance. h20 has only six nonoverlapping calendar anchors across both folds
(29 pooled ticker rows); its balanced accuracy is below 0.5 despite higher AP.
The h1 stability winner retained the legacy feature mask. Do not infer universal
feature improvement or investment value from these numbers.

Thirteen same-profile feature pairs were retained. On h20, the identical depth3,
32-tree, leaf30/sqrt forest scored AP 0.533843 with legacy columns versus 0.617919
with monthly columns (+0.084076), on the same source, labels and folds. Other
feature/model/horizon conditions are not pooled as causal superiority evidence.

Host memory changed 19 of the rule-policy counterfactual rankings. The actual
Gemma 4 proposal was accepted and linked to a verified prior experiment. Its
matched one-choice comparison scored AP 0.513180 versus the rule alternative's
0.505314 (+0.007866), but took 175.81s versus 0.000686s to select. The different
rule candidate was fitted once, not silently assigned a cached or invented score.
One pair does not establish LLM policy superiority or an LLM memory-ablation effect.

Supervised learners used 124.98s total, maximum observed RSS 160.9 MiB. The Gemma
proposal peaked at approximately 3.04 GiB RSS and 335.1% aggregate Ollama CPU,
inside its configured watchdog bounds. Model release preceded every learner.

Strict RandomForest reproduction matched folds, scores, model SHA and parameters.
One experimental liblinear profile did not exactly reproduce: identical input,
means and scales still yielded coefficient variation up to 2.84e-5, including
under the native worker thread environment. Its exact-reproduction status remains
failed; the comparator was not relaxed and original checkpoints were retained.
This limitation is separate from immutable dataset/source reproduction. All
models remain unqualified for performance, PIT, promotion and investment execution.

The private study retains request/independent PINs, immutable memory, checkpoints,
24-trial report, 13 controlled feature comparisons, frozen LLM comparison views,
budget replay and reproduction diagnostics. Original operating/research files are
compared separately against the pre-task SHA-256 inventory.
