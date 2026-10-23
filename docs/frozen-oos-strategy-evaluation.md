# Frozen OOS strategy evaluation foundation

This is a deterministic **synthetic research** workflow for one independently
frozen reference candidate per declared OOS fold. It connects the parameter-driven
compiler/Paper Broker integration to existing walk-forward planning contracts.
It does not optimize, rank, select, promote or activate strategies. Historical OOS
eligibility, strict PIT approval and operational eligibility remain false.

## Modules and original contracts

- `frozen_oos_contracts`: freeze, source-custody and fold/lineage verification.
- `frozen_oos_execution`: OOS-only execution, original valuation and semantic replay.
- `frozen_oos_walk_forward`: compatible fold summaries with a separate versioned
  report and independently verified original owners.

All belong to the existing Simulation boundary. They reuse `ResearchPlan`,
`StrategyCandidate`, `SyntheticObservations`, `TimeWindow`, `WalkForwardPlan`,
existing canonical digests, the original strategy compiler, `PaperScenario`,
`PaperSimulationAdapter`, `BatchScheduler` and original portfolio valuation.
Existing Trading Core, Risk Engine, Paper Broker, checkpoints, historical Ledger,
public APIs/CLIs, approval gates and CI policy remain unchanged. No network,
provider, database, credential or executable-plugin access is introduced.

## Trust model: synthetic evidence, not historical authentication

`frozen_strategy_oos_spec_v1` binds the complete original research plan and selected
candidate, exact parameters, built-in strategy artifact/installed source hash,
dataset/partition identities, ordered training/tuning lineage references,
execution/risk/valuation policies, seed/resource limits, walk-forward plan hash,
methodology and explicit freeze declaration time.

A freeze declaration and its own computed hash are insufficient. The caller must
supply a separately retained `SyntheticFreezeCustody` owner and independently
trusted custody pin. Its body binds the exact declaration and OOS authorization,
a separate recording time, tuning closure, and explicit absence of same-cycle OOS
feedback. An independently pinned `FrozenOOSAuthorization` commits the raw source,
cohort, fold/cycle, window, exact case membership, seal/release times and absence of
prior research access before release.

These pins are an **out-of-band trust precondition**, not values to extract from an
untrusted incoming document. The deterministic fixtures retain their controlled
synthetic source/authorization/declaration/custody pins separately. Verification
rejects missing/incorrect pins and self-rehashed declarations that do not match the
original independent custody owner.

This implementation does not authenticate a custodian's real identity, query an
audit service, prove a physical file was never opened, or cryptographically verify
historical recording time. A caller who replaces every trusted root can declare a
new synthetic experiment; that is not proof of historical custody. Independent
storage/signatures, access auditing and real training provenance are future work.
No approval record is created from these synthetic declarations.

## Authorization and leakage boundaries

The original `StrategyExecutionRequest` and `PartitionAuthorization` training/tuning
path remains unchanged and rejects OOS/holdout. The new `FrozenOOSRequest` is a
separate OOS-only entry; no authorization subclass or relabelled training request
is used to execute OOS.

It requires original typed owners for:

1. Freeze specification and independent custody/pins.
2. Raw OOS observations and independent OOS authorization/pins.
3. Both original training and tuning `StrategyExecutionRequest` source owners.
4. Existing `WalkForwardPlan`, its independent pin and an exact dataset-group
   manifest committed by its dataset reference.

The lineage owners retain their original source authorization. They establish
synthetic observation availability/custody, not a model-fitting process or an
explanation of why a researcher chose a parameter. No model is trained here.
The selected candidate must be an exact member of the original immutable plan.

The OOS dataset must be distinct from training, tuning and final-holdout datasets.
Its raw-source reference, cohort, authorization body and window must agree with the
original research plan and exact fold entry in the frozen dataset manifest. The
manifest covers all declared folds in order; it contains metadata references,
not final-holdout observations. Duplicate/omitted cases, wrong case hashes,
duplicate source content and wrong fold/cycle membership are rejected.

All actual information timestamps must fall in the authorized UTC half-open
`[start, end)` OOS window and declared synthetic market session. Every decision
and execution frame requires an available raw quote and matching open-session
identity. Original domain validation checks observation/availability order and
rejects future information relative to each frame clock. Training/tuning lineage
also requires compatible explicit session evidence. Calendar/timezone validation
reuses the original synthetic session-calendar parser.

The reference accepts only current raw prices. Methodology explicitly declares
zero feature lookback and zero label horizon; other features/horizons are rejected
rather than assigned fabricated availability. There is no expression or feature
loader. Calendar fixtures do not establish actual holiday/session authenticity.

## Freeze, purge and embargo semantics

Every fold requires an explicit training and tuning window before testing.
Optional-tuning plans are currently unsupported and fail closed. Existing
`walk_forward_folds` constructs holdout, rolling and expanding UTC elapsed-time
plans; existing `WalkForwardPlan` enforces nonoverlap, ordering and declared gaps.
This adapter additionally binds those exact windows to original lineage sources.

`OOSMethodology` requires explicit purge and embargo values matching the plan.
The last available lineage observation plus purge + embargo must precede the next
partition. Timing requirements are:

```text
last lineage information <= tuning_closed_at <= frozen_at <= custody.recorded_at
custody.recorded_at + embargo < OOS window start
sealed_at <= frozen_at < released_at <= OOS window start
```

The boundary is strict where shown; source/window ends are exclusive. A recorded
freeze after OOS release/feedback or before tuning closure is rejected even when
its supplied documents are internally rehashed. Nonempty feedback references,
a feedback timestamp, or prior pre-release research access invalidate custody.
Each fold requires its own freeze, custody and cycle. Distinct separately frozen
candidates are allowed; one fold's custody cannot authorize another fold.

Raw observations in a later fold's declared training window are not automatically
strategy retuning from an earlier OOS result. There is no retuning algorithm here;
parameter choice is explicit, separately frozen and must have no same-cycle OOS
result feedback. The code cannot police research performed outside these pinned
interfaces. Replaying an authorized OOS result for integrity is permitted; it is
not a new tuning authorization or permission to consume a final holdout.

## Execution and immutable results

`evaluate_frozen_oos(request, marks=(), cancelled=False)` authorizes sources first,
then passes the **exact frozen candidate** to the same internal decision-to-scenario
compiler used in PR #133. It does not rewrite a plan into a training namespace.
The original broker preview determines applicable commands; the original Paper
Simulation Adapter performs final isolated execution. Risk checks, reservations,
fees/taxes/slippage, fill ordering, idempotency and conservation stay authoritative
in the original implementations. No second broker/economic ledger is introduced.

An explicit `frozen_oos_strategy_instance_v1` binds candidate, code, spec, custody
and source authorization. Original experiment/checkpoint/result identities remain
available through the retained `PerformanceInput` owner. The raw OOS dataset and
derived PaperScenario registry have distinct identities; both are traced through
the freeze and experiment rather than claimed to have the same hash.

`frozen_strategy_oos_result_v1` records case decisions/quotes/times, run and execution
hashes, original risk-denial/fill counts and costs, failures, valuations and native
returns. `executed_cases` counts scheduler-recorded execution attempts; completion
is reported separately. A risk-denied action can belong to a completed cash-only
simulation with a verified zero return. An infrastructure failure is not a loss.

The explicit `cancelled=True` control supports deterministic cancellation **before
execution**: no scenario compilation, account, fills or checkpoint are invented.
All expected cases remain cancelled/unavailable. It must be supplied independently
to replay as well; verification does not trust a cancellation flag in a rehashed
report. Mid-run asynchronous interruption is not implemented by this adapter.
Scheduler budget failures remain original structured failed-run records.

The existing bounds apply: at most four cases/four steps per fold, sequential
single-attempt execution, original command/spec/result limits, maximum 4 MiB result
projection and the plan's total output budget including the original checkpoint.
Walk-forward summaries allow at most 20 declared folds/80 supplied cases. These are
cooperative logical bounds; compilation/preview precedes scheduler deadlines and
is not hard process isolation. No large-scale throughput claim is made.

## Valuation and descriptive statistics

The valuation policy body is fixed by the freeze and original plan reference.
Provided `PinnedValuation` evidence uses the existing independent evidence-pin
contract. The frozen methodology permits only original first-decision opening
quotes and terminal-execution closing quotes at those exact clocks. Provided marks
must equal those original source observations; a new price or reselected terminal
time cannot become valid by rehashing its evidence. Missing marks may remain empty,
which preserves original unavailable valuation behavior.

Original `value_paper_simulation` validates/replays the original execution and
computes economics. No cash-change-as-profit formula, invented FX, initial cost
basis, corporate action or second slippage charge is added. External cash-flow
scope must be explicitly supplied by the frozen existing policy; no inference or
fallback approval is introduced. Missing marks or unknown return evidence stays
unavailable, never zero. Descriptive statistics reuse existing comparison utilities
on the exact rational returns produced by original valuation.

Comparison v1 requires multiple compatible variants; this workflow evaluates one
frozen variant per fold. Walk-forward v1 is comparison-oriented and uses a different
source namespace. Neither schema is silently reinterpreted. The separate
`frozen_strategy_walk_forward_v1` adapter reuses planning/session validation,
original economic evidence and exact statistics, and retains explicit v1
compatibility limitations.

Fold order is the declared plan order. Missing folds are skipped with explicit
reasons and remain in expected-case coverage. Aggregation requires sufficient
complete folds plus compatible frozen code/parameters, native currency, policies,
methodology, cohort, seed and starting account economics. Distinct frozen variants
retain their own fold results but are not silently pooled. No Sharpe, annualized
return, drawdown, significance, composite score, ranking or promotion is calculated.

## Independent replay and integrity

`verify_frozen_oos` reconstructs authorization, freeze/custody/lineage, decisions,
intents, scenarios, scheduler results, valuations and statistics from original
owners. It compares the complete retained owner and independently invokes original
Paper Broker semantic replay. A matching hash alone never establishes execution.
`verify_frozen_walk_forward` reconstructs all supplied fold evidence and the full
summary. Rehashed changed decisions/fills/returns, substituted valuations or
incorrect complete-coverage claims are rejected.

## Worked synthetic example

The offline fixture in `tests/backtest/test_frozen_oos.py` uses explicit KOSPI or US
session calendars and separately pinned training, tuning and OOS observation roots.
The reference candidate is entry 10, exit 12, quantity 1. Native initial cash is
900 with no shares. OOS decision/execution pairs 10/10, 12/12 and 11/11 generate
BUY, SELL, WAIT with original broker fills. Explicit zero costs produce closing
equity 902 and original valuation return exactly `1/450`.

Two compatible chronological rolling or expanding folds each return `1/450`;
pooled evaluated count is 2 and mean remains `1/450`. Missing the second fold leaves
expected cases 2 and blocks the minimum-complete-fold aggregate. An independently
frozen entry-9 second fold has a verified flat return after a denied sale, but its
different frozen parameters block pooling. Entry 10/exit 20 can leave a real open
simulated position; a missing terminal mark then leaves return unavailable.

```sh
.venv/bin/pytest tests/backtest/test_frozen_oos.py -q
```

These examples demonstrate parameter freezing, source boundaries and synthetic
accounting, not historical profitability or generalization. No production IDs or
approved market data are implied.

## Future requirements

Genuine historical evaluation needs independently authenticated training/freeze
custody, trustworthy access logs, original feature/label availability, official
exchange calendars, approved corporate-action/PIT datasets and independent OOS
review. Final-holdout access and any future strategy optimization require separate
explicit governance. This foundation creates no such approval and performs no
financial provider calls, broker requests, model training or live activation.
