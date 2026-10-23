# Bounded multi-parameter strategy improvement

This is an end-to-end synthetic research workflow, not strategy promotion,
historical investment performance, forward trading or operational approval.

## Architecture and compatibility

`backtest.multi_parameter_improvement` composes the existing immutable typed
`ResearchPlan`, canonical candidate generation, `ImprovementPlan` v1 execution,
reference strategy, original Risk Engine and Paper Broker, simulation adapter,
portfolio valuation and independent semantic replay. No simplified simulator,
economic formula, public endpoint, CLI, checkpoint format or broker implementation
is added. Existing single-axis plans and reports remain unchanged.

`MultiParameterPlan` has the version `multi_parameter_improvement_plan_v1`.
It freezes the full original source-plan pin, independently pinned raw source and
training/tuning authorization, original code/artifact, seed, native market/currency,
execution/risk/valuation policies, explicit pinned mark templates, objective,
initial coordinates and resource limits. Its externally trusted canonical hash is
required at execution and verification.

`multi_parameter_improvement_report_v1` records every finite configuration as
invalid, attempted or skipped; invalid/skipped reasons; generation assessments;
original economic references; exact available observations; coverage/exclusions;
proposal anchors; full generation lineage; cancellation and budget decisions.
`MultiParameterResult.evidence` separately retains original v1 report owners,
execution/checkpoint/valuation evidence and structured failed-attempt reasons.

## Parameter representation and bounds

The installed synthetic price-band strategy has canonical axis order
`entry`, `exit`, `quantity`. Exactly two or three axes must vary; an unchanged axis
has one value. Each axis uses the original `ParameterGrid` validation and preserves
its declared value order. A coordinate is the tuple of declared value indices.

The following bounds are checked before complete Cartesian candidate construction:

- Three named dimensions, with two or three varying axes.
- At most eight values per axis and 64 raw combinations.
- A caller-selected maximum valid-candidate count, at most 64.
- Validity counts use the original strategy parameter validator on entry/exit pairs
  and quantities independently, without materializing the complete candidate set.
- Original research-plan configuration-byte, type, range and duplicate checks.

Original validation requires positive entry/exit, `exit > entry`, and integer
quantity 1 through 100. Invalid configurations remain explicitly identified but
are never simulated or proposed as new neighbors. Invalid initial configurations
are recorded as invalid requests rather than silently corrected.

The complete configuration identity is the canonical original parameter document
hash. Existing v1 candidate IDs remain bound to their particular original plan;
they are not falsely claimed invariant across derived namespaces. Duplicate
coordinates/configurations cannot be evaluated in later generations.

## Neighborhood policy

`complete_case_best_l1_grid_neighborhood_v1` is deterministic:

1. Execute the explicitly supplied initial coordinates, in supplied order.
2. Obtain original independently verified native-currency economic observations.
3. Consider only completed, fully evaluated cases from the same authorized cohort.
4. Use the greatest exact objective value as a research-priority anchor; ties use
   lexicographically smaller coordinates.
5. Sort unseen valid points by Manhattan distance between **declared indices**,
   then by lexicographic coordinate order.
6. Propose up to the configured limit, one to five, and repeat within all budgets.

Distance is not numeric price distance. Invalid holes can make the nearest valid
neighbor more than one index step away. All eligible history uses the same frozen
raw source, case membership, initial accounts, policies, costs, marks and seed.
Priority is internal research scheduling, never an investment recommendation,
production winner or authorization to inspect final holdouts.

## Actual execution and namespaces

Each coordinate is mapped to a singleton original research grid, with explicit
cycle/generation/parent/coordinate provenance. The original v1 improvement engine
executes one generation of that candidate. Actual decisions call the installed
reference strategy with the selected entry, exit and quantity; original Risk
Engine checks and Paper Broker reservations, partial fills, cancellations, cash,
shares and execution accounting remain authoritative.

Original execution/economic analysis reconstructs decisions and independently
replays broker/risk/valuation evidence before feedback is used. Costs, cash flows,
marks and economic outcomes are never calculated by this search module.
The bridge retains original plan-specific candidate, specialized strategy,
execution, checkpoint, economic and valuation identities alongside the complete
configuration hash and outer cycle hash.

The original economic-analysis v1 report remains honest about its single-candidate
comparison being unavailable. Separate singleton generated registries cannot be
misrepresented as compatible comparison-v1 input. Cross-candidate research
priority here uses fully verified valuations for the **identical authorized raw
cases**. No comparison-v1 contract is changed or fabricated.

## Complete membership and missing feedback

Mean or median native-currency return comes from the original v1 assessment.
Exact fractions are retained; unavailable returns are never zero. Partial coverage
still produces descriptive observations, but **cannot** drive proposals, even if
the descriptive minimum coverage is below one. Every expected case must have an
available original return, with original completed execution and exact policy and
case bindings. No matching by ticker/name alone or externally supplied summary
JSON is accepted.

A completed loss, denied simulated order, unavailable valuation, failed execution
and invalid configuration remain distinct. Original owners retain detailed risk,
fill, cost, cancellation and run-failure evidence. An execution/evidence contract
failure becomes a failed, ineligible attempt with its exact reason; no return is
invented. Independent replay must reproduce such failures too.

## Resources, cancellation and output budgets

Maximums are 16 generations, 64 evaluated attempts, 256 reserved scenario runs and
five proposals per generation. Original scheduler limits still constrain each
candidate. Every generation checks attempt count, total scenario-run reservations
and remaining retained-byte capacity **before starting any candidate**.

Retained capacity conservatively reserves the full configured per-candidate byte
allowance for all valid candidates in that generation. It can stop even when the
actual results might have fit. The per-candidate v1 output check includes its
serialized execution/analysis reports, checkpoints and v1 report; retained capacity
is at most 16 MiB. A separate final check bounds outer report plus retained evidence
against the configured total output size, also at most 16 MiB.

`simulation_runs_reserved` counts bounded scenario slots for attempted candidates;
it is not a claim about the number of internal replay invocations. Validation
reexecutes original work. Serialized-byte counts do not include every in-memory
adapter object, Python overhead or temporary construction. These are cooperative
work/output limits, not hard RSS isolation or OS-enforced deadlines.

Cancellation occurs at an explicit generation boundary and remains `stopped` with
its cancellation reason even after invalid/failed attempts or search exhaustion.
All prior generation owners remain unchanged. Attempt/run/retained-budget stops
have distinct reasons; skipped configurations retain the stop reason. Final-output
failure raises a contract error rather than publishing an over-budget report.
There is no transactional persistence, durable checkpoint/resume or process-crash
recovery claim.

## Independent verification and isolation

`verify_multi_parameter` requires independent plan/report pins and reconstructs the
entire finite search, candidate identities, invalid outcomes, original decisions,
commands, economics, eligibility, neighbors, lineage and stop decisions. It compares
both the immutable report and original owners, then independently calls original
v1 semantic verification on retained successful owners. Rehashed forged returns,
modified generation history, foreign-cycle owners and altered evidence fail closed.
There is no trusted cache or summary-only shortcut.

Exact training/tuning owner and role allowlists run before nested-owner validation.
OOS/holdout results, walk-forward reports and operational approvals have no input
path. Required original research plans retain OOS/holdout partition **metadata**,
not their observations or results. An independent source custodian still must not
mislabel OOS data as tuning; a hash is not evidence of genuine historical PIT
safety or undisclosed data custody.

## Worked two-generation experiment

`tests/backtest/test_multi_parameter_improvement.py::test_two_generations_actual_economics_and_replay`
executes this bounded example. Initial cash is 900, no shares; explicit zero fixture
costs; prices 10, 12, 11; capacity one per fill; residual orders cancelled; closing
mark 11 and explicit closed synthetic cash-flow evidence.

Search axes are entry `[10, 11]`, exit `[11, 12]`, quantity `[1, 2]`. Eight raw
configurations include two invalid entry=exit=11 combinations and six valid points.

| Generation | Coordinates | Parameters | Original outcome | Exact return |
|---|---|---|---|---|
| 0 | (0,1,0) | entry 10, exit 12, quantity 1 | Buy 10, sell 12; cash 902 | 1/450 |
| 0 | (0,1,1) | entry 10, exit 12, quantity 2 | Partial buy one; sell two denied; cash 890 plus stock marked 11 | 1/900 |
| 1 | (0,0,0) | entry 10, exit 11, quantity 1 | Buy 10, sell 12; later sell without shares denied; cash 902 | 1/450 |

The first point anchors the next proposal; (0,0,0) wins the distance/coordinate tie.
No parameter change is assumed to improve the result. Remaining points are marked
skipped at the two-generation budget. Tests also execute KR/KRW and US/USD cases,
partial-coverage exclusion, failed-attempt cancellation, and independent replay.

```python
from donghak_stock_vision.backtest.multi_parameter_improvement import (
    run_multi_parameter, verify_multi_parameter,
)

# plan is an authorized immutable local owner; plan_pin is separately trusted.
result = run_multi_parameter(plan, plan_pin)
report_pin = result.report.to_dict()["content_fingerprint"]
verify_multi_parameter(result, report_pin, plan, plan_pin)
```

## Future requirements

This is small, sequential, synthetic search with repeated independent verification.
It does not demonstrate large-scale optimization throughput or historical
profitability. Larger experiments need measured replay/retention costs and explicit
persistence/recovery design. Genuine historical evaluation still requires independent
PIT, corporate-action, session/calendar, strategy-freeze, training custody and OOS
review, plus untouched final-holdout governance. All existing restrictions remain.
