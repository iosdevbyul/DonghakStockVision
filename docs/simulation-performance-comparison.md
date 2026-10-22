# Simulation performance comparison

`backtest/simulation_comparison.py` compares independently verified completed
paper valuations, with full checkpoint coverage for failed, cancelled,
interrupted and unexamined jobs. It adds no execution, valuation formula,
publication system, strategy optimizer or winning-strategy selector.

This is separate from existing `compare_diagnostics`, which compares execution
and evidence diagnostics but intentionally has no return statistics. Existing
valuation v1, diagnostic v1/comparison v1, Broker, Risk Engine, Orchestrator,
historical Ledger and public CLI/API contracts are unchanged.

## Evidence owners and approval boundary

- `ValuationSource` carries a job key, original valuation report and trusted
  content fingerprint, original paper result and trusted result fingerprint,
  original typed `ValuationEvidence` and independently retained evidence pin.
- `PerformanceInput` carries the immutable original experiment, complete scheduler
  checkpoint and trusted checkpoint pin, original `PaperSimulationAdapter`, and
  a tuple of valuation sources. A source cannot belong to a failed/unexamined
  job, replace another job's result, or appear twice.
- `compare_performance(inputs, valuation_policy, trusted_policy_hash,
  max_jobs=...)` requires 2–5 distinct experiment fingerprints and an explicitly
  pinned comparison policy. Pins must be retained independently; recomputing a
  pin for arbitrary replacement inputs is not an approval procedure.

The comparison policy is canonical `FrozenJSON` with exactly these fields:

```json
{
  "version": "simulation_performance_comparison_policy_v1",
  "cohort_id": "controlled-native-market-paths",
  "valuation_policy_version": "paper_average_cost_exact_v1",
  "max_age_seconds": 60,
  "cash_flow_scope": "replayed_closed_paper_commands_v1"
}
```

It adds a named evaluation boundary and pins existing valuation conventions;
it does not grant operational approval. Freshness is mandatory, 0–86,400 seconds;
flow scope may be null, in which case existing valuations cannot supply returns.
Every supplied valuation must match these settings. Unknown policy versions,
bad pins, extra fields and incompatible policies fail closed.

Verification independently reconstructs the existing scenario registry owner and
checks its dataset/runner/execution-policy identities, including failed or unused
scenarios. It then restores the checkpoint through existing full trusted-pin,
record, lifecycle, seed and integrity validation. It does not call scheduler run
or resume.

For every latest successful job, the supplied valuation must bind to that exact
checkpoint output. Existing `verify_valuation_report` performs original result
validation, explicit `PaperSimulationAdapter` economic replay, mark-pin validation
and complete valuation reconstruction. Self-rehashed forged returns or costs
are rejected. Even a successful job with an omitted valuation must undergo
existing paper-result validation and semantic replay before it is reported as
unavailable. Non-success causes remain pinned recorded observations; discarded
broker state is not invented or replayed as a successful result.

No user-supplied aggregate performance summary is trusted. No global validated
cache, unsafe deserialization, model loading, inference or network is involved.

## Fair evaluation cohorts and strategy scripts

All compared experiments must have exactly matching:

- Native market and currency, complete frozen dataset registry and runner.
- Common experiment parameters, base seed and resource limits.
- Frozen execution policy, including each registered scenario's costs/risk rules.
- Absolute counts of exogenous evaluation cases (not merely relative mixtures).
- Starting account snapshots, including cash, positions and reserves.
- Existing valuation policy version, freshness and cash-flow scope.

`synthetic_paper_evaluation_case_v1` is a canonical projection of a registered
scenario's native identities, initial account/frame, ordered advance frames and
complete fixture execution policy. Its fingerprint identifies the same controlled
market path and opening capital. Equivalent-looking but differently identified
observations, times or policies are not silently normalized.

Different strategy variants may select different **explicit action/fill scripts**
from the **same shared, immutable scenario registry**. Order and fill instructions
are deliberately not exogenous case identity: e.g. one versus three shares on
the same observations are different actions in the same evaluation case. Their
original full scenario hashes, source dataset identity, run IDs, valuations and
receipts remain distinct and validated. Unequal market paths, case counts,
starting books or costs are rejected even when both scripts share one registry.
Different dataset registries are rejected even if their selected paths look alike.

These are controlled synthetic scripts, not a newly implemented strategy runner
or realistic liquidity experiment. Explicit fill quantities/instructions may
vary by variant, and the frozen source evidence shows them. Matching observations
and fixture rules does not establish equal real-world market access or realistic
liquidity. No price response, fill probability or strategy performance is invented.

Included returns for a given evaluation case must use identical independently
pinned opening/closing mark sets, including full instruments, source IDs and
observation/availability times. Mark tuple order is ignored; quote content is
not. This requirement applies even to a flat variant so that an included variant
cannot cherry-pick different valid closing prices. Initial account plus opening
marks therefore fixes comparable opening equity. A cash-only valuation may be
valid under valuation v1 without marks, but fair comparison against a marked
variant requires the same declared cohort mark set.

Unavailable valuations remain explicit exclusions, with original reason codes
and valuation references; their missing/stale marks are not repaired. They do not
contribute returns to the common mark set or statistics. All-failed cohorts can
be represented with zero evaluated coverage and unavailable statistics; no mark
validity is implied for those absent valuations.

## Statistics and coverage

The immutable output is `simulation_performance_comparison_v1`, with the common
cohort/policy and trusted policy hash, request-ordered experiment summaries,
per-run source references and all unordered request-order experiment pairs.

For exactly verified available total returns `r[1] … r[n]`:

```text
evaluated_count = n
coverage = n / total_expected_jobs
wins = count(r > 0)
losses = count(r < 0)
flat = count(r == 0)
mean = sum(r) / n
median = middle sorted value (odd n), or mean of the middle two (even n)
population_variance = sum((r - mean)^2) / n
range = max(r) - min(r)
```

All values are exact reduced rational numerator/denominator strings derived from
valuation v1's exact return ratio, not rounded decimal renderings or cash deltas.
Population variance is the explicitly defined dispersion measure. No approximate
square root, standard deviation, sample-estimator claim, annualization,
significance test or confidence interval is introduced. For zero evaluated
returns, mean/median/variance/extrema/range are null with `no_evaluated_returns`;
coverage is exactly zero. Ratios are unit returns, not percentages.

Latest job statuses are kept separately as succeeded/failed/cancelled/interrupted/
unexamined. A succeeded job may lack a valuation or have unavailable marks/flow
basis: it stays in `unavailable_succeeded_count` and the expected denominator.
Cancelled attempts preceding successful resume are retained in per-run attempt
history rather than counted as another completed job. Missing outcomes are never
assigned zero return. Per-run valuation diagnostics retain original missing/stale/
future mark reason codes and source paths.

Summary statistics are **conditional on evaluated observations**, not unbiased
estimates over all intended runs. Different missingness patterns can make
aggregate means misleading; coverage and exclusions must be examined alongside
them. Low risk rejections, high synthetic return or a high win count do not
select or promote a strategy.

## Paired scenario differences

Pair identity uses exact job key, exogenous evaluation-case hash and derived
run seed. Strategy/version/code references and full action-script hashes remain
source identities rather than pair identity. Same case counts with different job
keys/seeds are not silently paired by position or by scenario name.

For each request-ordered experiment pair (left, right), rows are sorted by pair
identity over the union of their job identities. Each row retains original run
references and statuses:

- `evaluated`: both matched runs have independently verified available returns;
  difference is **right return minus left return**.
- `unavailable`: identities match but at least one is failed/cancelled/unexamined,
  missing a valuation, or has unavailable valuation evidence.
- `unmatched`: a corresponding case/job/seed identity is absent on one side.

Paired statistics use only evaluated differences. Their expected denominator is
the union of pair identities, explicitly preserving unmatched outcomes. Incomplete
paired coverage adds `paired_subset_only`. Positive/negative differences are
observations in the stated orientation, not a winning-strategy decision.

## Integrity, determinism and resource limits

`content_fingerprint` is the canonical digest of the entire comparison excluding
only that field. It differs from the full-document digest and preserves original
source pins. No generated wall-clock timestamp or measured duration enters it.
`FrozenJSON` gives immutable bytes and detached JSON views.

`verify_performance_comparison` requires a trusted comparison fingerprint plus all
original owners/pins. It reconstructs registry/checkpoint validation, original
economic/valuation replay and the full comparison. Rehashed altered statistics,
coverage, pair rows or source references cannot pass; unknown versions are
rejected. A transport hash by itself is not economic validation.

A caller must specify an aggregate job budget covering all inputs, at most 300
jobs across 2–5 experiments. Existing scenario/record/attempt bounds and per-run
cooperative deadlines remain in force. Comparison output is capped at 4 MiB.
No unbounded workers or parallel execution are started. Complete checkpoint
history, case projections and report bytes are materialized; this is not hard
process isolation, streaming analysis, a persistence redesign or proof of
1,000/10,000-run scalability. Analysis explicitly replays completed jobs and
must not be confused with checkpoint resume or a read-only HTTP publication read.

## Reproducible controlled example

With repository development fixtures:

```python
from tests.backtest.test_simulation_comparison import registry, campaign, policy
from donghak_stock_vision.backtest.simulation_comparison import (
    compare_performance, verify_performance_comparison,
)

adapter = registry()
a = campaign(adapter, "a")  # Buy one share on the controlled 10 quote.
b = campaign(adapter, "b")  # Buy three on the same path, policy and starting book.
p = policy()
report = compare_performance((a, b), p, p.identifier, max_jobs=2)
value = report.to_dict()
assert value["paired_comparisons"][0]["statistics"]["mean"] == {
    "numerator": "1", "denominator": "225",
}
verify_performance_comparison(
    report, value["content_fingerprint"], (a, b), p, p.identifier, max_jobs=2,
)
```

The source fixtures explicitly prepare approved synthetic owners/pins, use zero
fixture costs, start with 900 and no inventory, and independently supply a 12
closing mark. Actual results are:

| Variant | Ending cash | Shares | Marked equity | Period P&L | Total return |
| --- | ---: | ---: | ---: | ---: | ---: |
| a | 890 | 1 | 902 | 2 | 1/450 |
| b | 870 | 3 | 906 | 6 | 1/150 |

The paired difference is `1/150 - 1/450 = 1/225`, with full evaluated coverage.
This reports different scripted exposure, not strategy superiority. Test helpers
calculate pins for controlled inputs; external consumers must retain approvals
independently. Production comparison creates no publication or approval records.

Next work requires independent OOS/economic validation and explicitly governed
valuation-aware publication, not automatically optimizing or promoting a variant.
Existing PIT, OOS, Corporate Actions and operational restrictions remain intact.
Synthetic returns are not verified real-world investment performance.
