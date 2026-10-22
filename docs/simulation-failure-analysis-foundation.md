# Simulation failure analysis foundation

`backtest/simulation_diagnostics.py` provides read-only, deterministic diagnostics
and factual experiment comparisons over existing synthetic Paper Broker runs.
It does not optimize parameters, select stocks, promote strategies, publish an
HTTP service, or authorize trading. Historical research/publication contracts,
checkpoint v1, broker accounting and risk policies are unchanged.

## Boundaries and trusted evidence

The Simulation module composes `BatchScheduler`, `PaperSimulationAdapter`,
`FrozenJSON`, existing canonical SHA-256 utilities, `RiskCode` and the Paper
Broker's exact Decimal context. It does not depend on API, CLI, storage adapters,
providers or broker infrastructure. Existing historical performance projections
require their own ledger/valuation evidence; they are not applied to paper cash
deltas. This module projects observations, not another performance calculator or
economic ledger.

`analyze_simulations(experiment, checkpoint, trusted_checkpoint_hash, adapter,
max_jobs=...)` requires an independently retained checkpoint pin and the original
immutable experiment and scenario registry:

1. Bind the adapter, dataset and execution policy to the experiment.
2. Restore through the existing full checkpoint v1 validator. It verifies the
   pin, version, experiment, seeds, job identities, lifecycle, ordering, result
   hashes and resource limits. Analysis does not resume the scheduler.
3. Validate each successful paper result through `verify_paper_result` and
   **explicitly replay** it through `PaperSimulationAdapter.replay_validate`.
   This recomputes a fresh isolated broker book, risk evaluations and receipts
   and checks complete semantic equality, including strategy/scenario/dataset
   bindings, conservation and execution provenance.
4. Project verified facts with the source checkpoint pin, original run/result
   hashes and field references such as `records[0].output.execution.orders[0]`.

A changed result with newly computed transport hashes still fails semantic
replay. Missing, corrupt, unknown-version or mismatched evidence produces an
exception and **no trusted diagnostic report**. Integrity failure codes already
recorded by the scheduler can be reported as recorded failures; an actually
invalid input is not converted into a successful diagnostic archive.

Failed/cancelled/interrupted attempts have no successful paper output. Their
structured causes are **pinned recorded observations**, not independently replayed
causal explanations. A scheduler exception's class is not evidence of an economic
cause. Unknown details remain `unknown`. Constructor-invalid experiments cannot
produce a report; existing validation errors are returned to the caller.

Analysis deliberately incurs bounded offline replay. It is not an HTTP read
projection or a cached verification shortcut. The caller must supply a positive
`max_jobs` no larger than 300, covering the complete experiment. Existing per-job
cooperative time/step limits remain enforced, and the diagnostic JSON is capped
at 16 MiB. This is not hard process isolation, streaming analysis or a claim of
10,000-run scalability. A full checkpoint and its outputs are materialized;
no durable diagnostic registry or new database is introduced.

## Versioned report contract

`VerifiedDiagnosticReport` is an immutable factory-owned Python value containing
canonical `FrozenJSON`. `to_dict()` returns a detached JSON view; `fingerprint`
is SHA-256 over the complete canonical report **excluding** `content_fingerprint`.
No wall-clock rendering time or nondeterministic timing metric enters the report.
Source hashes keep their existing meaning. The report's full JSON document digest
is distinct from its content fingerprint.

Only factory-owned verified reports may enter `compare_diagnostics`; arbitrary
JSON summaries or self-hashed user claims are rejected. This ownership boundary
is a Python API contract, not protection against arbitrary code execution in the
same process. Serialized reports cannot be re-imported as trusted owners; analyze
the original pinned checkpoint again. There is no pickle deserialization.

| Field | Meaning |
| --- | --- |
| `version` | `simulation_diagnostic_report_v1` |
| `experiment_id`, `experiment_fingerprint`, `strategy`, `code` | Original immutable experiment/version references |
| `scope` | Dataset reference, market, currency, common parameters, execution-policy hash and reduced relative scenario mix |
| `source_checkpoint` | Original checkpoint version and trusted full-document pin |
| `verification`, `evidence_availability` | Full validation, explicit replay count, recorded non-success jobs, unexamined jobs and missing independent valuation |
| `job_counts` | Latest per-job total/observed/completed/failed/cancelled/interrupted/unexamined |
| `attempt_counts` | Every recorded attempt, including cancellation before a successful resume |
| `runs` | Latest job/run/attempt/status/result hashes, paper/execution fingerprints and scenario reference |
| `categories`, `observations` | Counts, affected run IDs, source field references and original codes |
| `risk_rejection_counts` | Unique existing code per receipt carrying a risk evaluation; different fields with the same code count once per receipt |
| `warning_counts` | Market/risk-valuation rejection warnings and non-cancellation execution reasons, deduplicated per receipt/code |
| `execution_counts` | Orders/fill events, risk-denied orders, orders with any fill, cancellations, partial/unfilled orders and risk-evaluated/rejected receipts |
| `rates` | Exact numerator/denominator pairs; zero denominator gives null plus `zero_denominator` |
| `execution_costs` | Exact sums of existing fee/tax/slippage components in one currency, completed-job coverage only |
| `performance` | Null P&L and return with `independent_valuation_evidence_unavailable` |
| `limitations`, `content_fingerprint` | Explicit evidence restrictions and canonical content identity |

Completion/failure denominators are all specified jobs, including unexamined
jobs. Risk rejection frequency counts risk-bearing receipts (submission and fill
reassessments), not just orders. Fill rate counts orders with **any** fill divided
by recorded orders, not quantity-weighted completion. Cancellation rate is
cancelled orders divided by recorded orders. A partially filled then cancelled
order is simultaneously partial, cancelled and not fully filled; categories are
not mutually exclusive. Risk-rejected orders are not classified as accepted but
unfilled. Rates do not round or convert through floats.

Counts/costs from successful latest runs are aggregated once, not once per
historical attempt. Failure observations include all attempts. An unsuccessful
job's discarded broker state is unavailable, so its partial costs are not
invented. Slippage is already embedded in execution prices and is not added
again as a separate P&L deduction. An order cancellation is an observed action,
not evidence of a strategy's motive. Successful job completion is an
infrastructure outcome, not an investment gain.

## Diagnostic taxonomy

| Category | Evidence |
| --- | --- |
| `experiment_configuration_failure`, `missing_input` | Existing scheduler failure codes |
| `invalid_scenario_input`, `invalid_trading_instruction` | Existing exact `scenario_binding_invalid` / `invalid_paper_instruction` adapter details |
| `simulation_adapter_failure` | Recorded execution exception; no guessed root cause |
| `orchestration_timeout`, `execution_budget_exhausted` | Existing cooperative deadline/budget failure |
| `orchestration_cancellation`, `orchestration_interruption` | Recorded job stop/interruption, distinct from an order cancellation |
| `result_integrity_failure` | Recorded checkpoint/result integrity failure; invalid analysis input itself is rejected |
| `risk_denial` | Existing structured risk codes, or recorded adapter risk denial |
| `insufficient_buying_power_or_quantity` | Existing cash or position rejection code |
| `market_observation_issue` | Existing missing/invalid/stale price or session/closed-market reasons |
| `information_availability_issue` | Existing future-evidence code; does not assume whether price, account or valuation was future-dated |
| `risk_valuation_evidence_issue` | Existing missing/stale/invalid risk valuation codes, distinct from terminal performance valuation |
| `execution_constraint_violation` | Explicit existing execution reasons, e.g. nonmarketable limit or accepted-price cap |
| `order_cancellation`, `partial_fill`, `unfilled_order` | Verified final order status, accepted timestamp and requested/filled quantities |
| `execution_cost_observation` | Existing execution-cost components |
| `missing_performance_evidence` | Existing unavailable terminal valuation |
| `unknown` | Unsupported code/detail; no speculative classification |

These are factual diagnostic labels. Market warnings are not official data-quality
approvals. An unsafe order rejected by the Risk Engine is not a software bug.
No loss is inferred from cash movement, and an investment loss is not classified
as an infrastructure error.

## Compatible comparisons

`compare_diagnostics((report_a, report_b))` accepts 2–5 distinct verified owners
and returns canonical `simulation_diagnostic_comparison_v1` JSON in requested
order, with report references and its own content fingerprint. It compares exact
completion/failure, risk-rejection, fill/cancellation ratios, execution components,
evidence availability and limitations. It rejects scope mismatches instead of
pooling incomparable observations.

Market/currency, complete dataset registry, common parameters (including supplied
period assumptions), execution policy and relative scenario mix must match.
One versus two copies of the same scenario are compatible; costs remain raw batch
sums and are explicitly **not normalized** by batch size. Different datasets,
period parameters, fee/slippage policies, KR versus US or scenario mixes are
rejected. Strategy and code references may differ and are retained per item.
No missing date is inferred from a scenario name. There are no rankings,
investment recommendations, strategy promotions or claims that low rejection
frequency means a better strategy.

## Reproducible synthetic example

From the repository checkout with development dependencies installed:

```python
from tests.backtest.test_paper_simulation import scenario, experiment
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter
from donghak_stock_vision.backtest.simulation_diagnostics import (
    analyze_simulations, compare_diagnostics,
)

adapter = PaperSimulationAdapter((scenario(),))
reports = []
for count in (1, 2):
    spec = experiment(adapter, count)
    scheduler = BatchScheduler(spec)
    scheduler.run(adapter)
    checkpoint = scheduler.checkpoint()
    # In an actual workflow retain this pin independently at checkpoint approval.
    pin = checkpoint.identifier
    reports.append(analyze_simulations(spec, checkpoint, pin, adapter, max_jobs=2))
comparison = compare_diagnostics(tuple(reports)).to_dict()
assert [r["job_counts"]["total"] for r in comparison["items"]] == [1, 2]
```

The one-job KR fixture actually produces two orders, two fill events, one
partially filled/cancelled buy and one fully filled sale. Observed diagnostic
fields are:

```json
{
  "execution_counts": {
    "orders": 2,
    "fills": 2,
    "orders_with_fills": 2,
    "cancelled_orders": 1,
    "partially_filled_orders": 1,
    "unfilled_orders": 1,
    "risk_denied_orders": 0,
    "risk_evaluated_receipts": 4,
    "risk_rejected_receipts": 0
  },
  "execution_costs": {
    "currency": "KRW",
    "fees": "0.05",
    "taxes": "0.04",
    "slippage": "0.4",
    "slippage_already_in_fill_prices": true,
    "coverage": "completed_jobs_only"
  },
  "performance": {
    "available": false,
    "pnl": null,
    "return": null,
    "reason": "independent_valuation_evidence_unavailable"
  }
}
```

This example uses test-only synthetic inputs, not historical market data or
real-time forward paper trading. The same fixtures cover native USD accounts.
A future Strategy Optimization Engine can consume these immutable diagnostic
references to propose independently evaluated experiments. It must not equate
improved synthetic fill/rejection/cost metrics with real profitability, bypass
risk checks, or automatically approve a candidate. Independent valuation,
OOS/PIT/Corporate Actions evidence and existing governance gates remain required.
