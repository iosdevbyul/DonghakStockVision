# Multi-stock generalization research

This extends the existing autonomous runner, source replay, 46-column features,
three estimator families, metrics and AnalysisStore. It does not install a model,
collect KRX data, change a scheduler, publish a prediction or grant investment
permissions. Existing v1-v4 plans and the original liblinear evidence stay intact.

## Inventory and bounded cohorts

`storage.stock_universe.inspect(original_request)` verifies the retained database
and locked protocol pins before/after a read-only transaction. Full-DB inspection
uses dates/counts only. Payload quality and liquidity selection use **the original
training window only**, never protected Final Test payloads or validation returns.
It reports per-symbol coverage/positive fields, source metadata, exclusions and
unknown survivorship/listing/delisting/sector/capitalization/publication authority.
First/last observed rows are not authenticated listing/delisting dates. Training
value is a liquidity proxy, not market capitalization or a sector classification.

`cohorts(inventory, original_core)` deterministically retains the five original
symbols and spreads 45 additional symbols across training-value thirds. All 50
are disjoint between two 25-symbol cohorts. This is a resource-bounded research
sample, not all historically listed KOSPI stocks or a representative market index.
Metadata eligibility is not original-byte verification: each selected cohort must
subsequently pass the unchanged RealMarketTrainingRequest/verified-market owner.
That owner retains its 25-symbol, 20,000-row, raw-byte/response and read-only limits.
The research archive's legacy SHA/BLOB linkage does not acquire missing receipts
or independent issuer/custodian authenticity by being used in a larger study.
Operational SQLite remains a separate, unused training owner.

A new `autonomous_multi_stock_source_v1` request carries the original research,
protocol, cutoff and horizons plus:

```json
{
  "multi_stock": {
    "heldout_tickers": ["<sorted permitted codes>"],
    "budget": 12,
    "policy": "adaptive",
    "inventory": {"path": "<new private inventory.json>", "pin": "<canonical inventory digest>"},
    "cohort": "cohort1"
  }
}
```

The cohort has 10-25 symbols, with at least two and at most one third assigned to
stock holdout. Actual execution uses 25/5. The new direction_dataset_v3 and
immutable autonomous_plan_v5 bind this scope, inventory, source, folds, code and
policy. References are `(dataset_id, experiment_id)`; a bare ID is not a license
to reuse a result from a different stock assignment. Per-symbol samples and
exclusion reasons are retained. A request-local, <=20,000-row **derived-feature**
cache avoids recalculating the same indicators for each horizon; no raw-response
cache survives source verification. No market DB is merged or rewritten.

## Generalization and leak boundaries

The 1/5/20 delayed-direction labels, 46 features and seven masks remain unchanged.
The v5 catalog has 462 configurations: eleven remaining parameter profiles,
seven masks, three horizons and pooled/heldout stock policies. Only the three
experimental liblinear profiles are quarantined. Direct execution rejects them;
old studies still reconstruct them and their failed exact-refit evidence. No
numerical tolerance or model SHA comparison is weakened.

All symbols share purged expanding calendar folds. A heldout-stock model fits and
scales on the other 20 symbols only; its five excluded symbols are evaluated only
on later dates. A pooled model fits all 25 as the explicit comparison control.
Main search scores use the same 20-symbol future CV population in both policies.
Heldout metrics are sidecars, never selection inputs. Pooled-policy memory cannot
tune a heldout-policy model: history and proposal candidates are policy-isolated.
Flipping excluded-symbol labels cannot change a heldout model's checkpoint or
seen-symbol search metric. Peer-cache adoption requires matching data, scope,
configurations, code and budgets, including the stock policy of the selected ID.

Assessments include seen/excluded-symbol metrics, per-symbol distributions,
quarterly periods and causal **stock-local** regimes. Regimes use input SMA20 gap
(±3%), input volatility10 (1%/2.5%) and input relative volume (2x), not future
returns. Official market regimes stay UNKNOWN. Sparse buckets are explicitly
insufficient. No sector or macro label is invented. Calibration bins/Brier describe
uncalibrated research scores, not approved price-rise probabilities.

Nonoverlapping event cohorts retain actual calendar dates and group assessments.
Adding stocks increases cross-sectional rows, not independent calendar anchors.
Do not sum shared dates across cohorts or mistake correlated ticker observations
for independent samples. Search does not evaluate the original reserved
confirmation/Final Test periods. The v5 confirmation path remains sealed.

## Equal-budget memory comparison

Each study spends at most twelve terminal trials, failures/cache hits included.
The adaptive policy reuses training-internal stability/overfit/failure memory,
balances horizons and stock policies, and records counterfactual choices and
alternatives. Other populations' scores are reference-only. A predeclared arm uses
an a priori order beginning with matched legacy/multi-feature RF controls for
both stock policies. Both arms use the existing RuleBasedAgent; this comparison
is not an LLM-versus-human or an LLM superiority experiment.

`fork_policy` inherits original source artifacts without replaying or replacing
bytes. The immutable child plan and policy-arm.json explicitly record its new
predeclared assignment; the original source request is copied unchanged. A cache
hit avoids an identical refit but consumes the same selection budget. Measured
CPU savings from shared cache are not intrinsic selection-policy speedup.
Heldout details are omitted from the standard report until the arm's search is
locked. Even then, results remain retrospective descriptive research.

## Manual execution and retention

Create an inventory using the pinned original request, save it with `write_new`
and retain its canonical `data.learning.digest` separately. Create new cohort
requests/roots/PIN directories; do not overwrite old studies.

```sh
dsv autonomous-research prepare --request "$NEW_REQUEST" --request-pin-file "$REQUEST_PIN" --root "$ADAPTIVE" --pins-root "$ADAPTIVE_PINS"
dsv autonomous-research run --root "$ADAPTIVE" --plan-pin-file "$ADAPTIVE_PINS/plan.pin" --steps 6
# New process safely resumes the same budget:
dsv autonomous-research run --root "$ADAPTIVE" --plan-pin-file "$ADAPTIVE_PINS/plan.pin" --steps 6
dsv autonomous-research fork-policy --source-root "$ADAPTIVE" --source-plan-pin-file "$ADAPTIVE_PINS/plan.pin" --root "$PREDECLARED" --pins-root "$PREDECLARED_PINS"
dsv autonomous-research run --root "$PREDECLARED" --plan-pin-file "$PREDECLARED_PINS/plan.pin" --steps 12 --reuse-root "$ADAPTIVE" --reuse-plan-pin-file "$ADAPTIVE_PINS/plan.pin"
dsv autonomous-research report --root "$ADAPTIVE" --plan-pin-file "$ADAPTIVE_PINS/plan.pin" --report "$NEW_REPORT"
```

`stock_research.compare` accepts locked reports with identical condition pins and
budgets. Different cohorts/Horizons/old five-stock studies cannot be pooled as a
controlled gain. Preserve inventory/request/PINs, source proofs, dataset, memory,
checkpoints, packages and reports. The shared heavy-work lock, one-thread native
worker, 1GiB/300s/size/disk guards and restart/idempotence owners are unchanged.
No 24/7 operation is activated. All PIT, performance, Champion/operational-model
registration and execution approval flags remain false.

## Executed KRX research (2026-11-16)

The retained research archive contains 1,099,255 bars, 993 tickers and 1,160 dates
(2022-01-03–2026-10-01). Read-only operational metadata contains 26,302 observations,
942 tickers and 28 dates through 2026-11-13; those later operational rows were not
used for training. The archive has no original receipt owner. Existing legacy raw
BLOB/SHA and authoritative bar-source replay were reused, without synthesized
receipts or authenticated publication-time claims.

Training-only inventory found 867 metadata-eligible symbols and 126 exclusions:
51 incomplete training panels, 106 with excessive nonpositive required fields
and 20 without training-window rows (reasons overlap). This eligibility is not a claim that all 867 originals were
replayed. Training-value terciles and deterministic ordering selected two disjoint
25-symbol cohorts. The original five core symbols remain in cohort 1. Another
817 eligible symbols were deferred under the bounded execution budget. Sector,
market capitalization, historical constituents, delisting/suspension causes and
survivorship completeness remain UNKNOWN; trading value is not market cap.

Both cohorts passed original-source replay: **50 symbols, 611 dates and 30,550
bars**, 2022-01-03–2024-06-28, versus the previous 5 symbols/3,055 bars. Each cohort
has five excluded symbols and twenty seen symbols. Potential labeled training
rows before stock-policy filtering were respectively 9,300/10,098/9,886 and
9,229/9,911/9,655 for horizons 1/5/20. The reserved confirmation and Final Test
payloads were not evaluated. Actual expanding-window CV stays within original
training roles; the 611-date source window does not imply all rows were fitted.

A precommitted budget of two cohorts × two policies × twelve selections completed:
**48 selections, 47 unique new fits, one exact cache reuse, zero training failures**.
Worker wall time totaled 586.39 seconds; peak worker RSS was 406,600 KiB (397.1 MiB).
All learners ran sequentially with one native thread. Memory changed five adaptive
choices. The cache reuse partly affects measured policy cost, so cost differences
are not evidence of intrinsically faster selection. No KRX, remote provider or
local LLM inference was performed.

New fits by family (cache hits excluded):

| Family | Fits | Worker seconds | Peak RSS KiB |
|---|---:|---:|---:|
| Histogram gradient boosting | 10 | 185.18 | 406,600 |
| Linear/lbfgs | 6 | 58.24 | 404,088 |
| Random forest | 31 | 342.98 | 383,052 |

Seen CV AP ranges across the executed configurations were .4950–.5166,
.5362–.5412 and .4512–.4890 for boosting; .4793–.4966, .5332–.5393 and
.4667–.4767 for linear; .4814–.5629, .4995–.5463 and .4793–.5127 for RF
(horizons 1/5/20 respectively). These are search-population summaries, not
controlled family superiority estimates.

For heldout-policy candidates selected using seen-symbol stability only, mean
excluded-symbol PR-AUC (AP) / balanced accuracy across the two calendar folds was:

| Cohort / policy | 1 observation | 5 observations | 20 observations |
|---|---|---|---|
| 1 adaptive | .4683 / .4837 | .5081 / .5485 | .4881 / .5479 |
| 1 predeclared | .4880 / .5032 | .4887 / .4557 | .4476 / .5340 |
| 2 adaptive | .4509 / .4641 | .5535 / .5481 | .4299 / .5336 |
| 2 predeclared | .5268 / .5299 | .5358 / .5066 | .5758 / .6521 |

These are descriptive retrospective results, not a uniform adaptive-policy gain.
Matched fixed-arm RF comparisons found multi-feature minus legacy-feature seen
AP differences ranging from −.0557 to +.0320; extra features did not consistently
help. Model-family costs, overfit gaps, per-ticker metrics, causal regimes,
quarterly metrics, calibration and baseline comparisons are retained separately.
Across selected excluded symbols, mean fold balanced accuracy ranges were
.3815–.6038, .3589–.6904 and .3793–.7813 for horizons 1/5/20. Sparse regime/period
buckets remain marked insufficient. No best regime is promoted after inspection.
For example, cohort 2 adaptive/horizon 5 had excluded-symbol balanced accuracy
.5521 in down-trend rows (133), .5517 sideways (232), .6185 up-trend (56),
.4725 high-volatility (113), .4659 low-volatility (46), .5500 normal-volatility
(262) and .5345 volume-surge (37). These overlapping causal buckets are not
independent tests. Low-volatility/volume-surge buckets passed neither fold
adequacy screen, while up-trend/high-volatility passed only one of two folds.

Nonoverlapping excluded-symbol rows per selected arm were 146–154, 66–69 and
23–25. The union of independent calendar anchors across both cohorts is only
**40, 16 and 6**, respectively. More tickers did not create more independent
long-horizon dates. The previous five-stock study and its 24 terminal records
were reverified as a reference population; they are not a matched performance
gain control. Do not compare AP across different horizons to select a winner.

One preselected result from each active family (linear/lbfgs, histogram boosting,
random forest) was refitted. Folds, scores, parameters, training-symbol ownership
and model SHA-256 matched exactly. The prior liblinear failure evidence is
preserved; only those three profiles are quarantined in the new catalog. Old
catalogs and records remain valid. No tolerance or digest comparison was relaxed.
An exhausted-arm replay made zero fits and zero new attempts.

Private evidence is retained under
`KRX-Private/Research/multi-stock-inventory-20261116T050026Z` and four sibling
`multi-stock-20261116T050026Z-{1,2}-{adaptive,predeclared}` roots, with separate
ResearchPins. Inventory, requests, original-source proofs, immutable plans,
experiments, detailed comparisons, exact-reproduction results and package/check
artifacts are retained there. No private PIN value or original payload is committed.
All performance/PIT/execution approvals remain false; no operational model,
Final Test, scheduler or LaunchAgent is changed or activated.
