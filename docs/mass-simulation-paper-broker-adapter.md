# Mass Simulation Paper Broker adapter

This adapter connects `BatchScheduler` to the existing
`DeterministicPaperBroker`. It executes bounded, explicitly synthetic KR/US
scenarios in fresh virtual accounts. It is not strategy optimization, historical
market-data acquisition, real-time forward paper trading or operational trading
activation. Successful execution and synthetic cash changes are not evidence of
investment profitability.

## Responsibilities and dependency direction

- `backtest/paper_scenario.py`: immutable, versioned scenario composition from
  existing Trading Core, risk policy and Paper Broker input owners.
- `backtest/paper_simulation.py`: `PaperSimulationAdapter`, implementing the
  existing `SimulationRunner` protocol through composition, plus explicit result
  integrity/replay verification.
- Existing `trading/paper.py`: the sole owner of reservation, acceptance, fill,
  idempotency, cancellation, exact accounting and conservation rules.
- Existing `trading/risk.py`: the sole pre-trade/fill risk evaluator. No policy
  threshold, pricing, fee, tax or risk formula is implemented by this adapter.
- Existing `backtest/mass_simulation.py`: run IDs, bounded scheduling, failures,
  cancellation and trusted checkpoint resume.

The Simulation domain may compose the existing Risk-domain Paper Broker. That
broker is an in-memory domain adapter, not concrete network/database
infrastructure. No dependency on FastAPI, SQLite, providers, broker SDKs, CLI or
model loading is introduced. Architecture debt exceptions are unchanged.
Existing public CLI/API, historical backtests, schemas and CI remain unchanged.

## Explicit synthetic scenario format

`PaperScenario` is an immutable **Python contract**, not an executable config
file, a permissive JSON decoder or a pickle format. Its canonical JSON view is
used for hashes; callers supply already constructed domain values.

| Field | Existing owner/meaning |
| --- | --- |
| `scenario_id` | Unique bounded logical identifier |
| `market`, `currency` | Trading Core KR/KRW or US/USD |
| `initial_account` | `AccountSnapshot`; native cash and initial positions, no initial reservations |
| `policy` | Explicit `FixturePolicy`, including mandatory `RiskPolicy`, precision, fees, taxes and slippage |
| `initial_frame` | `PaperFrame` with explicit simulation time, quotes, supplied risk valuations, session evidence and trading flag |
| `commands` | Ordered tuple of existing `PaperCommand` values, with typed payloads below |

Accepted command forms:

```python
PaperCommand("advance", frame.frame_id, frame)
PaperCommand("submit", key, Submission(order_intent, key, None))
PaperCommand("fill", event.event_id, event)
PaperCommand("cancel", key, Cancellation(order_id, key, explicit_time))
```

`Submission.risk` **must be None in scenario inputs**. The adapter obtains the
actual preflight proof from the job-local broker. Scenarios cannot bring a grant
that bypasses evaluation. Market and limit orders reuse existing `OrderIntent`.
Fill quantities/events are explicit instructions, not inferred liquidity or
probabilistic forecasts. Commands are processed in supplied order; the adapter
does not sort away invalid event order. Existing broker clock/identity guards
reject incompatible instructions.

Each scenario has 1–64 instructions, a policy command limit no larger than 64,
up to 16 instruments/positions/quotes, up to four exchange-session observations
per frame and at most one native risk valuation per frame. Canonical scenario
bytes are capped at 256 KiB. The registry permits 1–100 unique scenarios, capped
at 4 MiB of canonical source evidence. Existing constructors revalidate nested
immutable inputs and initial account invariants.

There is one native-currency book per scenario. KR/US scenarios may coexist in
one registry, but each experiment remains a native KR/KRW or US/USD experiment.
Cross-currency settlement and FX frames are rejected; USD cannot fund a KRW
order. This restriction does not alter the underlying Risk Engine's FX contracts.

## Registry and experiment binding

`PaperSimulationAdapter((scenario_a, scenario_b))` owns an immutable registry,
sorted by scenario ID. It exposes:

- `dataset_reference`: the existing artifact reference shape, with the hash of
  the complete canonical versioned scenario registry.
- `reference`: a versioned adapter reference bound to that dataset identity.
- `execution_policy`: a deterministic per-scenario policy hash manifest.
- `job(key, scenario_id)`: an existing `SimulationJob` whose parameters contain
  only `scenario_id` and `scenario_hash`. Large account/market objects are not
  copied into every job.

The experiment must pin all three values and declare `data_origin="synthetic"`.
A job must belong to the experiment, use its exact derived seed, match its
market/currency, and match both the registered original scenario hash and current
source bytes. Unknown scenarios, altered hashes/policies/datasets, extra path
parameters and real-data labels fail closed. Even a newly rehashed job cannot
replace scenario evidence behind the original dataset pin.

Strategy and code references retain the existing experiment reference contracts;
those identifiers do not independently attest the actual source-code revision or
approve a strategy. No file paths are accepted or opened by the adapter.

## Execution sequence and isolation

For every job:

1. Consume a setup work unit and validate immutable experiment/scenario binding.
2. Construct a **new** `DeterministicPaperBroker` using the explicit initial owners.
3. Consume one logical work unit per instruction; check the cancellation/deadline
   before and after broker operations.
4. Advance frames, derive preflight decisions, submit orders, execute explicit
   fills and cancel remainders through existing public broker methods.
5. Read the final validated broker checkpoint, account, orders and fills. Project
   canonical receipts/cost summaries, identities and unavailable valuation fields.
6. Return a versioned, fingerprinted `FrozenJSON` result. Existing scheduler
   validation then commits its run record/checkpoint.

Every submission is checked both by public preflight and by the broker's
mandatory internal acceptance evaluation; fills retain internal risk checks.
Risk approval is separate from execution eligibility: closed markets, stale or
missing later quotes and nonmarketable limits can leave accepted orders pending.
Reservations remain visible in ending cash/positions; no terminal fill or
liquidation is invented.

Proof caches, broker instances, receipt lists and account transitions are local
to one invocation. Reusing an immutable initial account value or identical
account/order IDs across jobs does not share a mutable book. Exact repeated
submissions reuse the **original local proof** so the broker can validate its
complete idempotent command even after intervening fills. Conflicting keys or
order IDs are rejected by existing broker rules. Repeated fills/cancellations
are retained only once in evidence/counts; unique broker fills remain authoritative.

The adapter uses no random sampling. It validates and records the orchestrator's
seed with `seed_usage="recorded_no_stochastic_execution"`. For a fixed experiment,
calling jobs in reverse order produces identical per-job results. Reordering the
experiment's job tuple changes the existing experiment fingerprint/run IDs;
the separately exposed `execution_fingerprint` still matches the same scenario's
execution evidence. The orchestrator identity contract is not rewritten.

## Result schema and verification

`paper_simulation_result_v1` includes:

| Group | Fields |
| --- | --- |
| Restrictions | `simulated`, `data_origin`, `usage_restriction` |
| Identity | experiment ID/fingerprint, run ID, job key, seed and seed usage |
| Provenance | strategy/code/dataset/runner references, execution policy hash |
| Execution | scenario reference, market/currency/instruments, original and final account/cash, ending positions |
| Audit evidence | final order states, unique fills, canonical broker receipts with risk reasons/source IDs/timestamps |
| Counts | orders, fills, rejected receipt events, risk-denied orders, cancellations, pending orders, requested and committed instruction counts |
| Costs | sums of existing fill fees/taxes and existing receipt slippage, in native Money values |
| Broker proof | existing checkpoint version, final state ID, exact committed-command hash |
| Outcome | `completed`, null infrastructure error classification; rejections remain factual outcomes |
| Valuation | unavailable/null portfolio value and realized/unrealized P&L, stable reason |
| Integrity | execution evidence fingerprint and complete result fingerprint |

Counts describe different things: a risk-denied order is not an infrastructure
exception; rejected receipt events can include later execution rejection; repeat
instructions do not inflate accepted orders or fills. Fees/taxes are summed from
validated fill receipts, not recalculated. Slippage is already incorporated into
fill prices and must not be subtracted a second time.

`verify_paper_result(result, expected_fingerprint)` checks version, restrictions,
canonical hashes and an **independently trusted** result pin. Hashes are integrity
checks, not signatures or proof of economic correctness if both content and pin
are replaced. `adapter.replay_validate(spec, job, result, budget)` explicitly
reexecutes that scenario in a fresh broker and compares complete canonical
results. It invokes the existing broker risk/conservation validators; it is never
called implicitly during scheduler resume or result reads.

The result fingerprint hashes every top-level field except `result_fingerprint`;
the execution fingerprint hashes only `execution`. `FrozenJSON.identifier` hashes
the whole serialized object including those fingerprints, while the scheduler's
`result_hash` binds its outer attempt/status record. These are distinct identities
and are not claimed to be interchangeable document hashes.

The broker proof is a reference to a reproducible checkpoint, not a newly
invented checkpoint codec or a promise that arbitrary JSON can restore a running
broker. Original scenario owners, exact policy and adapter/code references must
remain available for explicit replay.

A final supplied **risk** equity mark does not automatically establish terminal
portfolio valuation, acquisition basis or verified performance. This adapter
therefore returns `paper_terminal_valuation_not_projected`, with null total value
and P&L. It does not label a cash difference as portfolio profit. No new economic
ledger, performance formula or current price request is introduced.

## Failures, cancellation and checkpoints

Missing scenario evidence produces `missing_input`. Binding or invalid broker
instructions produce `incompatible_input`. Unexpected exceptions retain existing
scheduler infrastructure classification. Risk rejections are preserved in the
successful scenario's broker evidence, with no automatic strategy revision.

The adapter never enlarges the scheduler's limits. Setup + N commands consumes
N+1 logical units. Oversized returned evidence fails with
`budget_exhausted/paper_result_bytes`. Time limits remain cooperative: this
in-process adapter cannot be forcibly preempted or claim an OS memory sandbox.
Bounded scenario instructions also retain existing broker command limits.

Checkpoints commit at **job boundaries**. Completed succeeded/failed jobs are
not invoked when the original scheduler checkpoint is restored with its trusted
pin. A cancellation after partial in-job execution discards that disposable
book; the uncommitted job restarts from its original inputs on explicit resume.
Earlier committed results are preserved byte-for-byte. Thus completed trades are
not silently replayed, but work in an uncommitted interrupted attempt may repeat
in a fresh isolated book. There is no mid-job durable broker recovery, filesystem
persistence redesign or external exactly-once side-effect guarantee.

## Reproducible examples and observations

From the source checkout, these **test-only** fixtures construct actual domain
owners with synthetic policy, clocks and prices and invoke the production adapter:

```sh
PYTHONPATH=src:. .venv/bin/python - <<'PY'
from tests.backtest.test_paper_simulation import scenario, experiment
from tests.trading.test_risk import KR, US
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter

adapter = PaperSimulationAdapter((scenario(KR), scenario(US)))
for source in adapter.scenarios:
    spec = experiment(adapter, 10, source.scenario_id)
    scheduler = BatchScheduler(spec)
    records = scheduler.run(adapter)
    first = records[0].to_dict()["output"]
    print(source.currency.value, first["execution"]["counts"], first["result_fingerprint"])
    checkpoint = scheduler.checkpoint()
    restored = BatchScheduler(
        spec, checkpoint=checkpoint, expected_checkpoint_hash=checkpoint.identifier
    )
    assert restored.run(adapter) == records  # No completed job executes again.
PY
```

The roundtrip fixture submits a five-share BUY, fills two, cancels three, then
SELLs/fills two. Each job has two orders, two fills, one cancellation and zero
pending orders at completion. It starts with cash 900 and ten shares, ends with
cash 899.51 and ten shares, and reports fees 0.05, synthetic taxes 0.04 and
slippage 0.4. Those are fixture assumptions in either native currency, not real
market costs or measured portfolio returns.

Single local observations on Python 3.12.14 (not a throughput guarantee):

| Measurement | 10 jobs | 100 jobs |
| --- | ---: | ---: |
| Orders / fills | 20 / 20 | 200 / 200 |
| Total scheduler construction + execution, with memory tracing | 3.799 s | 118.596 s |
| Python allocation peak | 2,812,462 bytes | 13,726,241 bytes |
| Sum of adapter output bytes | 113,053 | 1,130,543 |
| Canonical outer result-array bytes | 117,087 | 1,170,887 |
| Canonical checkpoint bytes | 117,233 | 1,171,033 |
| Cumulative checkpoint construction, traced | 0.257 s | 24.137 s |
| Cumulative checkpoint validation, traced | 0.675 s | 64.880 s |
| Extra explicit checkpoint reencoding, traced | 0.028 s | 0.262 s |
| Extra explicit checkpoint JSON decoding/canonicalization, traced | 0.027 s | 0.300 s |
| Restore + completed resume, untraced | 0.033 s | 0.382 s |
| Adapter calls on completed resume | 0 | 0 |
| One job explicit semantic replay, untraced | 0.061 s | 0.067 s |

The driver used `time.perf_counter()` and `tracemalloc`. Immutable fixture/registry
construction occurred before tracing. Peak includes scheduler execution, retained
results and extra explicit serialization, but excludes source fixture allocation,
native allocations not tracked by Python and total process RSS. Time tracing has
material overhead; the traced times must not be presented as uninstrumented
engine throughput. Resume and one-job semantic replay occurred after tracing
stopped. Full 100-job semantic replay was not measured.

A temporary measurement subclass timed existing `_document` and `_validate`
methods without changing their output. Cumulative checkpoint timing is already
included in total execution, and the extra reencode/decode measurements are
separate diagnostic operations. The 100-job checkpoint construction/validation
sum was about **89 seconds of 118.6 traced seconds**. This is a measured scaling
limitation of retained-history checkpoints; this PR does not redesign persistence
or claim readiness for 10,000 runs.

The deterministic fixture identities observed in that driver:

| Jobs | Experiment fingerprint | Checkpoint hash |
| --- | --- | --- |
| 10 | `8b9a61ed6e0385c96753224e24193b8f3cabc2add247510b27981e4bba7ed638` | `00c3b8ce04ebf3ea2161b49fc847538e5108266015e1ade89ad0c13e367fc8ab` |
| 100 | `c5f6d91cd42f7919f4da1c1774c25482c8c309e9dc561b617d721026c5af51b3` | `78dddd0e0e042ed327666f3378a5152dbc4efd255d9c257e94c93b420783c6da` |

Those identities use `PaperSimulationAdapter((scenario(),))` and
`experiment(adapter, count)` from the test fixture, not the mixed-registry example
above. Changing the registry legitimately changes experiment IDs/hashes.
Reproduce the basic duration/peak/size observation as follows (no host-specific
timing value is asserted by a test):

```python
import time
import tracemalloc
from tests.backtest.test_paper_simulation import scenario, experiment
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter

for count in (10, 100):
    adapter = PaperSimulationAdapter((scenario(),))
    spec = experiment(adapter, count)
    tracemalloc.start()
    start = time.perf_counter()
    scheduler = BatchScheduler(spec)
    records = scheduler.run(adapter)
    elapsed = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    print(count, elapsed, peak, len(scheduler.checkpoint().payload_json.encode()))
```

This shorter example measures peak through execution only, rather than including
the measurement driver's extra serialization steps. Its peak may therefore differ.

Focused validation:

```sh
.venv/bin/pytest -q tests/backtest/test_paper_simulation.py \
  tests/backtest/test_mass_simulation.py tests/trading tests/architecture
```

The offline test harness rejects TCP connections. Fixtures use no real datasets,
providers, credentials, model training or broker connections. Tests cover stable
10/100 batches, native KR/US isolation, exact costs, risk-denied actions,
nonmarketable/closed/stale/missing execution evidence, duplicate protection,
partial cancellation, failure isolation, trusted hashes, explicit semantic
replay, cancellation/resume and original conservation checks.

## Limits and next work

The full offline suite and Python 3.13 macOS X64 CI remain unchanged. Local
measurements on Python 3.12 are not a throughput claim for CI or another host.
No 10,000-run CI workload is added.

Retaining full small broker receipts makes result size proportional to jobs and
scenario commands. Existing scheduler checkpoint copying/canonical validation
scans prior results each transition, so cumulative checkpoint work is quadratic.
The adapter's successful 100-run test does not establish cost-effective 1,000 or
10,000-run execution. Measure those sizes independently before changing storage
or concurrency; do not remove safety bounds to obtain a faster benchmark.

Next work can add evidence-based failure diagnostics and independently verified
valuation/projection adapters, then profile bounded chunked checkpoint designs.
That work must preserve original receipts, trusted pins, native settlement, risk
checks and independent OOS validation. No optimization, strategy approval,
Corporate Actions/PIT/OOS relaxation or operational activation follows from this
integration.
