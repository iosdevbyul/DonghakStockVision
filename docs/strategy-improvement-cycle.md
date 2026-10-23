# Strategy improvement cycle foundation

This is bounded synthetic research prioritization. It neither selects a production
strategy nor supplies historical PIT/OOS eligibility, operational approval, model
training custody, or evidence of real investment profitability.

## Boundary and contracts

`backtest.strategy_improvement` composes the existing parameter execution adapter,
strategy decision replay, Risk Engine, isolated Paper Broker accounts, valuation,
and compatible within-generation performance comparison. It adds no economic
calculation, execution policy, network client, persistence layer, API or CLI.

The immutable `ImprovementPlan` produces `strategy_improvement_plan_v1`. An
independently retained plan hash authorizes the complete source plan, raw synthetic
observations and partition authorization pins, explicit valuation-mark templates,
valuation policy, search axis, initial indices, objective, coverage threshold, and
budgets. The original source plan carries frozen code/artifact, typed grid, seed,
market/currency, partition metadata and per-simulation resource limits. Its original
verification still checks installed strategy code and exact authorization.

`TuningFeedback` retains the original `StrategyExecutionBatch`,
`StrategyEconomicAnalysis`, and pinned `ValuationEvidence` owners. A generation
binds these to the cycle hash, generation number, proposed indices and previous
generation hash. `ImprovementResult` retains these owners separately from the
compact `strategy_improvement_report_v1` projection. The report records actual
candidate IDs, configuration hashes, original execution/analysis/valuation hashes,
run states, exact performance metrics, execution costs, exclusions and proposals.

## Finite proposal algorithm

The reference implementation supports exactly one varying parameter axis; every
other axis is fixed. The existing typed `ParameterGrid` validates types, numeric
bounds, duplicate values, configuration bytes and Cartesian cardinality before
materialization. The full search space is at most 64 configurations.

Initial indices explicitly select one to five configurations. After each generation:

1. Verify and assess every attempted configuration.
2. Retain unavailable and failed cases in the expected-case denominator.
3. Find eligible observations across the same cycle's verified tuning history.
4. Use the greatest supported objective value as an internal research anchor,
   breaking ties by the smaller declared axis index.
5. Propose exactly one unseen index nearest to that anchor; ties use smaller index.

Distance is **declared grid index distance**, not economic or numeric distance.
Choice order is part of the immutable plan. No interpolation, mutation of strategy
code, arbitrary evaluation language, model fitting or unbounded search occurs.
The anchor is explicitly research priority, not an investment winner.

Maximums are 16 generations, 64 attempted configurations, four scenarios per
configuration, and 16 MiB retained serialized execution/analysis/checkpoint evidence
plus final report. A lower caller limit is enforced. Original per-simulation limits
remain in force. Evidence bytes are checked before retaining a generation; temporary
construction can exceed the retained-byte budget. These are cooperative bounded
work limits, not hard process time/RSS isolation.

## Namespace bridge and cohorts

Each generation derives an original `ResearchPlan` with the same frozen sources,
policies, partitions, code, strategy and seed; only its selected grid choices and
explicit cycle-generation provenance change. Original v1 candidate IDs remain
plan-specific. The cycle-wide configuration hash is the original canonical
parameter document hash; it is not misrepresented as a v1 candidate ID.

All generations use the same authorized cases, source dataset, initial accounts,
market/currency, costs, execution policy, valuation templates and comparison
policy. They cannot import another cohort's feedback. Existing comparison v1
validates compatible candidates within each generation; a one-candidate generation
honestly retains its original `fewer_than_two_executed_candidates` diagnostic.
Cross-generation prioritization uses original independently verified per-case
valuations, not a fabricated cross-generation comparison v1 document.

## Permitted feedback and objectives

Only exact `StrategyExecutionRequest`, `SyntheticObservations` and
`PartitionAuthorization` owners are allowed, with role `training` or `tuning`.
Feedback must be an exact `TuningFeedback` owner from the same cycle and lineage.
There is no generic report reader, path input, callback, OOS loader or prior-cycle
summary import. Unknown owners are rejected before accessing their contents.

Original `ResearchPlan` includes OOS/holdout **partition metadata** because v1
requires four partitions. The improvement engine never receives their observations,
valuation results, walk-forward reports, holdout feedback or operational approvals.
It cannot infer an input's undisclosed real-world custody: the externally trusted
training/tuning authorization pin remains a required independent trust boundary.
An operator must never authorize OOS material as tuning. No hash establishes PIT
safety or historical data custody by itself.

The explicit objective version `verified_tuning_return_v1` supports `mean_return`
and `median_return`. Values are exact rational summaries of original available
`performance.total_return` metrics. Coverage is available-case count divided by
all expected scenarios; missing returns are never zero. Eligibility requires an
original completed candidate, at least the strictly positive configured coverage,
and supported economic evidence. A completed simulation can legitimately have a
negative return or risk-denied orders; those are distinct from infrastructure or
configuration failure. The report retains original run failure and rejection
information through immutable evidence owners, rather than calling a loss a failure.

## Valuation and independent verification

`TuningMarks` is an explicit immutable template keyed to a scenario. It supplies
opening/closing timestamps, original `MarketQuote` owners, max age and cash-flow
scope. Its complete bytes are authorized by the independent cycle plan pin. It is
not itself an execution receipt. Once actual execution IDs exist, the bridge binds
the template to original experiment, run and execution fingerprints to construct
original `ValuationEvidence`. It never invents marks or assumes closed cash flows.
Missing templates or unsupported flow evidence preserve unavailable returns.
Original valuation checks freshness, information availability, positions, cost
basis, settlement conservation, fees/taxes and semantic execution replay.

`verify_feedback` reconstructs the expected generation plan, checks cycle/parent/
indices and template bindings, then calls original `verify_strategy_analysis`.
This independently reconstructs strategy decisions and replays broker/risk/economic
results and existing comparison evidence. Self-rehashed forged returns, altered
code/parameters, changed inputs and foreign-cycle feedback do not become trusted.
No cache or summary-only verification replaces original owners.

`verify_improvement(result, result_pin, plan, plan_pin)` independently reruns the
entire deterministic cycle and compares both the report and original feedback
owners. Cancellation control must be independently supplied again. Rehashing a
modified report or proposal history does not pass this semantic replay.

## Lifecycle and stopping

The report records `planned`, `running`, then `completed`, `stopped` or `failed`.
Reasons include finite search exhaustion, generation/attempt budgets, no eligible
tuning evidence, cancellation at a generation boundary and all configurations
failing. Invalid authorization, corrupted evidence and exhausted byte budgets fail
closed with contract errors; they do not create apparently valid performance.

`cancel_before_generation` stops only at a bounded generation boundary and retains
all prior completed feedback. There is no durable checkpoint, resume import or
process-crash recovery claim in this foundation. There is no reuse of OOS fold
terminology: generations are parameter search iterations, not walk-forward folds.

## Worked two-generation synthetic example

The executable fixture in `tests/backtest/test_strategy_improvement.py` uses initial
cash 900, no initial positions, explicit zero costs, quantity one, exit threshold
12, and the entry grid `[10, 9, 11]`. Source decision/execution prices are 10, 12,
and 11; each execution is later than its decision. Explicit endpoint marks and
closed synthetic cash-flow evidence are pinned.

| Generation | Entry | Observed original behavior | Exact return |
|---|---:|---|---:|
| 0 | 10 | Buy at 10, sell at 12; cash ends 902 | 1/450 |
| 0 | 9 | No entry; sell attempt risk-denied; cash stays 900 | 0 |
| 1 | 11 | Buy 10, sell 12, buy 11; cash 891 plus marked stock 11 | 1/450 |

Initial indices are `(0, 1)`, coverage requirement is one, objective is mean return,
maximum generations two and attempts four. The first candidate is the research
anchor; unseen index two is proposed. No configuration is repeated. The final
position in generation one remains open; its stock value comes from the explicit
closing mark, not an invented liquidation. All three configurations have coverage
one in this fixture. Missing marks/flow evidence instead stop the cycle when no
eligible feedback remains. These are synthetic mechanics, not historical returns.

```python
from donghak_stock_vision.backtest.strategy_improvement import (
    run_improvement, verify_improvement,
)

# plan and separately retained plan_pin come from an authorized local owner.
result = run_improvement(plan, plan_pin)
result_pin = result.report.to_dict()["content_fingerprint"]
verify_improvement(result, result_pin, plan, plan_pin)
```

## Remaining work

The reference path is sequential, one search axis, bounded synthetic scenarios and
in-memory owners. Independent replay is deliberately expensive; retained-byte
measurement is not peak Python/RSS measurement. This PR does not benchmark large
searches or claim 1,000/10,000-candidate scalability. It has no automatic optimizer,
production ranking, deployment or strategy activation.

A later separately reviewed workflow can freeze an explicitly chosen research
candidate for independent frozen OOS evaluation. OOS evidence must remain outside
this cycle, including stopping and proposal decisions. Genuine historical research
still needs independent training custody, PIT/calendar/corporate-action evidence,
untouched holdout governance and OOS review; current gates remain unchanged.
