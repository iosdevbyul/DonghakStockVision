# Strategy parameter execution adapter

This research-only adapter executes an allowlisted, parameter-sensitive synthetic
reference strategy. It is not an optimizer, a model, an investment recommendation,
a historical PIT approval, a forward-paper service or a live trading interface.
No public CLI/API, broker authorization, valuation/comparison v1 contract,
checkpoint, approval gate or CI policy changes.

## Responsibilities and dependency direction

- `strategy.paper_reference`: exact parameter validation, pure price-band decision,
  immutable built-in artifact and installed source-code fingerprint.
- `backtest.strategy_observations`: bounded raw synthetic observations and independent
  partition authorization. No provider, filesystem dataset loader or holdout reader.
- `backtest.strategy_execution`: authorized decision generation, narrow immutable
  decision-to-scenario construction, existing Mass Simulation scheduling and
  original Paper Simulation execution/replay.
- `backtest.strategy_execution_analysis`: existing valuation/comparison integration
  after independent strategy replay, and explicit legacy evaluation limitations.

These follow existing Research/Simulation/Core/Risk boundaries without exceptions.
They depend on no application, HTTP, database or external provider layer. The only
new filesystem read is the allowlisted installed `strategy/paper_reference.py`
package resource used to hash its actual code bytes. No caller-controlled path,
plugin import, dynamic `eval`, executable expression or supplied code is accepted.

Existing `strategy.engine` uses verified model-analysis, decision-policy and
historical/PIT evidence; it is not a raw-price strategy with the independently
available inputs needed here. Rather than fabricate that evidence, this adapter
introduces the explicitly synthetic reference below. Existing signals, models and
strategy decisions remain unchanged.

## Reference implementation and immutable binding

`synthetic_price_band_strategy_v1` accepts exactly:

| Parameter | Contract |
|---|---|
| `entry` | Positive exact decimal string |
| `exit` | Positive exact decimal string strictly greater than `entry` |
| `quantity` | Strict integer 1–100; booleans rejected |

The current available raw quote is the only strategy input. At decision time:

- Price <= entry: BUY the declared quantity.
- Price >= exit: SELL the declared quantity.
- Otherwise: WAIT. Missing quote: WAIT with `missing_quote`.

There is no implicit position sizing, liquidation, profit target, stop loss,
portfolio optimization or random decision. A SELL is a proposed action and can be
risk-denied for insufficient shares. A stale quote does not bypass risk checks.
Strategy decisions and risk approval are separate records.

The existing `ResearchPlan`, typed `ParameterGrid` and `StrategyCandidate` remain
unchanged. The plan's strategy reference must equal the built-in artifact reference;
its code reference must equal SHA-256 of the installed strategy module bytes. The
artifact includes that code reference, parameter schema, rule definitions and
synthetic restriction. Changing the actual source invalidates an old plan/code
pin. Arbitrary implementation fingerprints cannot select executable code.

Each `frozen_strategy_instance_v1` explicitly embeds the complete candidate document
(plan hash, exact parameter values, candidate ID), built-in artifact, code reference,
original observation hash and authorization hash. This specialized strategy's
content-addressed reference is the original `ExperimentSpec.strategy` value.

`ExperimentSpec.parameters` explicitly describes the common **runner** binding:
`{"strategy_parameter_scope":"frozen_strategy_instance_v1"}`. Actual strategy
parameters are not replaced, omitted or represented as common parameters: they
are visible in the immutable specialized strategy artifact and every candidate
report. This distinction lets existing comparison v1 compare distinct frozen
strategies under genuinely identical runner configuration, source market paths,
starting accounts, policies, seeds and evaluation case mixes. It does not relax
comparison compatibility checks or compare different scenario mixes.

## Original sources and strict partition access

`SyntheticObservations` contains 1–4 uniquely named, sorted `ObservationCase`
owners. Each case has one native instrument, original initial account, original
`FixturePolicy`, and 1–4 `MarketStep` values. A step contains:

- An explicit decision `PaperFrame`.
- A strictly later execution `PaperFrame`.
- Independently fixed capacity (0–100 shares), liquidity evidence ID and cancellation
  instruction. Candidate parameters cannot change these controls.

Frames have unique IDs; steps are strictly ordered. Quotes, accounts, sessions,
risk valuations and availability times use existing immutable domain types.
The source accepts no feature expression, future-derived score or executable field.
A proposed future feature requires a separate independently verified contract;
there is no fallback that invents its availability.

`PartitionAuthorization` is an independently retained synthetic source-custody
attestation binding exact dataset content hash, cohort hash, UTC half-open window,
role and authorization identity. `StrategyExecutionRequest` requires independently
trusted plan, observation and authorization hashes. The plan partition's provenance
reference must pin the actual authorization body, not merely a similar label.

Before decision generation, independently reconstructed owners must establish:

1. Exact original source and authorization pins.
2. Authorized training or tuning role. OOS/final holdout is rejected even with
   internally consistent supplied hashes.
3. Exact partition dataset, cohort, window and provenance bindings.
4. All source timestamps inside the authorized half-open window.
5. Every frame's information timestamps <= its explicit clock; existing types
   require timezone-aware observation and availability times in the proper order.
6. Native market/currency, fixture risk policies and execution controls matching
   plan references.

Missing timestamps, future availability, dataset substitution, unrelated custody,
foreign native currencies and out-of-window observations fail closed. Generation
reads no training/tuning data from arbitrary paths. Final-holdout contents are
never automatically opened, summarized or evaluated. The plan may retain holdout
**metadata references** as required by v1, which is not data access.

Future execution observations are separately pinned synthetic execution inputs;
`decide` receives only the current quote and candidate parameters. Altering a
later execution price does not change the strategy's current action. It does
change source/candidate/report identities. This is not independent historical PIT
custody, calendar approval, label/purge evidence or genuine OOS qualification.

## Decision-to-Paper Broker execution

`prepare_strategy_candidate(request, candidate)` is the explicit single-candidate
boundary: it reauthorizes original sources and returns immutable scenarios plus
decision provenance. The batch path applies the same compiler after request-wide
authorization. It accepts only exact members of the original immutable plan.

For each candidate/case, the adapter:

1. Reads only the authorized current frame and invokes the allowlisted decision.
2. Records decision ID, candidate, parameter hash, case/step, cutoff, quote with
   observed/available timestamps, observation hash, rule and full proposed intent.
3. Creates market-order intent and idempotency key from canonical decision identity.
4. Advances to the explicitly supplied later execution frame.
5. Attempts at most the fixture's capacity, capped by requested quantity, only if
   the original broker still has an open order. Cancels remaining quantity only
   when independently fixed fixture policy requests cancellation.
6. Freezes this sequence as an original `PaperScenario`.

A fresh preview instance of the **existing** `DeterministicPaperBroker` is used
only to determine whether risk rejection or terminal state makes a fill/cancel
command inapplicable. It supplies no forged approvals or receipts to the final
script: every `Submission.risk` remains null. All risk, reservations, settlement,
fees, taxes, slippage and invariants use original broker implementations.

A new shared immutable scenario registry contains the derived scripts. Each
candidate gets its own `ExperimentSpec` and existing `BatchScheduler`; every job
creates a fresh Paper Broker through `PaperSimulationAdapter`. Account IDs may be
identical across cases without sharing mutable state. Per-case job keys, stable
candidate order, run IDs, seeds and original result hashes are retained. The seed
is recorded; the reference has no stochastic operation.

The original source dataset and generated scenario dataset have **distinct**
identities. The execution report binds both via the candidate instance, source
pins and experiment/checkpoint references. It never claims their JSON or hashes
are interchangeable.

A rejected order has no fill command. Missing liquidity can leave a reserved
pending order or trigger fixture cancellation. Partial fill/cancellation and
insufficient-share behavior stay authoritative in the broker. Risk approval does
not imply execution eligibility: a closed execution session produces an original
pending receipt and no fill, even if the earlier order passed risk. Nothing
fabricates a final liquidation.

## Results, bounds and failure handling

`strategy_parameter_execution_v1` is an immutable canonical projection retaining
original source/authorization/code references, candidate instances, decision
sequences, experiments, run states and checkpoint/result fingerprints. Typed
`StrategyExecutionBatch.inputs` retain the original `PerformanceInput` owners for
independent downstream validation; original receipts remain in checkpoints.

Execution supports at most 5 candidates x 4 cases x 4 steps, sequentially, and must
fit the plan's run count. It requires an explicitly single-attempt policy. Each
case must provision up to five original commands per step, within the original
64-command limit. Existing result/spec/scenario-registry limits also apply. The
execution projection is capped at 4 MiB, and projection plus checkpoints must fit
`max_total_result_bytes`. Source observations are capped at 512 KiB.

These are cooperative logical bounds, not hard process isolation. The bounded
compiler/preview runs before scheduler deadlines; scheduler budgets apply to the
final executions. Compilation plus final execution plus independent replay adds
work; no throughput, large-scale memory or speed claim is made.

Unsupported parameter combinations become candidate-level
`invalid_configuration` failures without touching another candidate account.
Invalid common source/custody fails the whole request before execution. Scheduler
budget/execution failures remain incomplete with original per-run failure records.
Risk-denied orders and negative economic outcomes do not become infrastructure
failures. No automatic retry or parameter revision occurs.

## Existing economics and evaluation integration

`analyze_strategy_execution` first independently verifies strategy execution. It
then consumes explicit `PinnedValuation` owners, each containing original
`ValuationEvidence` and an independent pin. No terminal mark, FX rate, external
cash-flow assertion, cost basis or execution cost is synthesized. Marks must remain
within the authorized partition and match the pinned valuation policy.

Existing `value_paper_simulation` computes original valuation results, including
its own Paper Broker replay. Existing `compare_performance` computes descriptive
and paired statistics after all original validation. No second return, cost or
P&L formula is implemented. Missing valuation remains unavailable in the existing
comparison denominator; an unavailable return never becomes zero.

`strategy_parameter_analysis_v1` retains full original valuation/comparison
projections, their source pins, complete candidate outcome counts and exclusions.
Invalid configurations remain in the outer candidate coverage; a comparison of
only executed candidates is explicitly labelled `executed_candidate_subset_only`.
Fewer than two executable variants yields no comparison. No ranking or promotion.

The older `strategy_evaluation` v1 completed-assessment path requires raw-plan
dataset and direct parameter equality. It cannot honestly consume derived scenario
namespaces or frozen strategy specialization unchanged. The explicit
`legacy_evaluation_assessments` bridge therefore reports unavailable
`evaluation_not_supported` (or an invalid-configuration failure), **not false
completed economics**. The dedicated analysis above is implemented; automatic v1
namespace translation is not. A future translation needs an explicit versioned
contract and independent original-source verification.

## Independent semantic verification

`verify_strategy_execution(batch, trusted_report_hash, request)` checks the pin,
then reconstructs authorization, candidates, strategy decisions, intents, scripts,
experiments, scheduler checkpoints and outputs from original immutable observations.
It compares original owners and invokes existing independent
`PaperSimulationAdapter.replay_validate` for successful results. No cache or matching
output hash substitutes for this semantic reconstruction.

`verify_strategy_analysis` also reconstructs valuations/comparisons from original
pinned owners. Rehashed changed decisions, orders, parameter/artifact references,
receipts, outcomes, marks or economic summaries cannot pass merely by supplying
a new digest. Trusted pins must be held independently; extracting a digest from
an untrusted incoming report is not independent trust.

## Worked bounded synthetic example

The offline fixture in `tests/backtest/test_strategy_execution.py` starts with
native cash 900 and no shares. It fixes three decision/execution price pairs:
10/10, 12/12 and 11/11. Execution follows decision by one second. Capacity is one
share; fees/taxes/slippage are explicitly zero in this example.

| Configuration | Actual decisions | Original simulated outcome |
|---|---|---|
| entry 10, exit 12, quantity 1 | BUY, SELL, WAIT | Two fills; closing cash/equity 902 |
| entry 9, exit 12, quantity 1 | WAIT, SELL, WAIT | SELL risk-denied, no fills; closing cash/equity 900 |

With separately pinned opening/closing marks and explicit closed-book scope,
original valuation returns are exactly `1/450` and `0/1`. The original paired
right-minus-left comparison is `-1/450`. This demonstrates parameter consumption,
provenance and exact accounting, **not strategy improvement**. KRW and USD fixtures
remain independent. Nonzero cost fixtures reconcile fees, taxes and slippage
through original broker/valuation code without charging embedded slippage twice.

Run the controlled workflow with:

```sh
.venv/bin/pytest tests/backtest/test_strategy_execution.py -q
```

All IDs and pins in this fixture describe synthetic research only. No model or
real market data is loaded.

## Remaining work

Production historical strategy evaluation requires independent dataset and training
custody, properly available features, exchange calendars, corporate actions,
lookback/purge/embargo evidence and strategy freezing. A separate frozen OOS cycle
must reuse existing walk-forward verification, preserving untouched final holdouts
and prohibiting feedback into the same tuning cycle. Limit-order strategies,
realistic liquidity, advanced policies and scalable persistence are not implemented
here. Automatic optimization, winner selection, trading activation and live broker
connectivity remain prohibited.
