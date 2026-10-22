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
scenario commands. The original scheduler scanned prior results each transition;
its measured quadratic cost motivated the checkpoint optimization below. The
adapter's successful bounded tests do not establish cost-effective 1,000 or
10,000-run execution. Measure those sizes independently before changing storage
or concurrency; do not remove safety bounds to obtain a faster benchmark.

Next work can add evidence-based failure diagnostics and independently verified
valuation/projection adapters, then profile bounded chunked checkpoint designs.
That work must preserve original receipts, trusted pins, native settlement, risk
checks and independent OOS validation. No optimization, strategy approval,
Corporate Actions/PIT/OOS relaxation or operational activation follows from this
integration.


## Checkpoint performance optimization

The reference baseline is main after PR #126:
`d0a31fa20d674c8c8a58b1c8d75753f47794f2be`. The earlier table records that PR's
original one-sample traced observations. The following investigation remeasures
the same fixtures rather than using those old timings as the comparison baseline.

### Investigation and implementation

Originally each committed job built `[*records, record]`, serialized all completed
outputs through `FrozenJSON`, parsed that document, and revalidated every previous
run hash, output and transition. It also reparsed/copied history to build latest
results and reconstruct resume bookkeeping. With N equal-sized results, append
serialization/validation visited N(N+1)/2 records. Whole-checkpoint hashing at
export is a separate linear cost, not the dominant repeated operation.

The implementation keeps a private persistent chain of immutable validated record
owners. Appending validates the entire **new** record (schema, canonical bytes,
hash, identity, seed, status/failure, attempt, transition and resource limits),
then derives cumulative byte/output counts from the current validated parent.
Current-parent identity and scheduler-owner checks prevent stale or cross-owner
append reuse. Small summary seals and per-record byte digests detect stale or
corrupted internal metadata; they are not signatures or a new authorization grant.
A single head replacement commits the record and invalidates the old export cache
atomically. Interrupted precommit work preserves the original checkpoint.

Full legacy v1 JSON is rendered only when `checkpoint()` is requested. A per-head
rendering cache can serve repeated reads. Export traverses/checks the immutable
chain, verifies recorded byte accounting and checks cached rendering bytes against
the original byte pin. Public result owners are detached from internal records.
No dictionary of untrusted supplied documents is marked "already validated".

**Restore has no incremental shortcut.** Every supplied `FrozenJSON`, including a
previously supplied object, is independently checked against its trusted pin and
then fully validated record by record into a new private chain. Unknown versions,
extra cache metadata, modified identities, corrupted records, illegal transitions
and mismatched limits are rejected. A valid earlier prefix cannot be distinguished
from legitimate earlier state without the original trusted pin; that pin remains
mandatory. Hashes do not authenticate a semantic forgery if both evidence and pin
are replaced. Explicit `PaperSimulationAdapter.replay_validate` remains necessary
for semantic replay and is never implicitly skipped or replaced by a summary seal.

The trust boundary is immutable in-process owners and ordinary untrusted JSON.
Summary seals are accidental-corruption checks, not a sandbox against arbitrary
Python code that can replace private attributes, recompute seals and run arbitrary
instructions. This is the same in-process trust limitation as the cooperative
runner; no trading/risk/governance eligibility is expanded.

### Compatibility and correctness

`mass_simulation_checkpoint_v1` keeps exactly the same meaning, fields, ordering
and canonical bytes. There is no migration, new format, pickle or DB change. Run
IDs, seeds, result hashes and adapter outputs remain unchanged. Golden hashes
captured from the baseline cover success, failure, cancellation and interruption;
the Paper Broker fixture retains PR #126 checkpoint/result hashes. Full restored
validation, bounded attempt history, cancellation/failure isolation and job-boundary
resume remain authoritative. Complete jobs do not execute again during resume.

A metadata/transport pin check does not claim a profitable or independently
validated strategy. No Paper Broker, Risk Engine or accounting code is changed.
All existing risk/conservation/replay regressions remain in scope.

### Bounded before/after measurements

Untraced total times include the first full checkpoint export. Ten/100-job
cases have two fresh-process samples per implementation; the 300-job case is a
single bounded sample. Percentage decreases use the sample means, not rounded
numbers. Small-case variability is shown rather than treated as a precise speedup.

| Jobs | Before raw totals (s) | After raw totals (s) | Mean time decrease | Checkpoint bytes, unchanged |
| --- | --- | --- | ---: | ---: |
| 10 | 0.779229, 0.747374 | 0.662698, 0.630228 | 15.31% | 117,233 |
| 100 | 16.268955, 16.217186 | 6.683056, 6.518434 | 59.36% | 1,171,033 |
| 300 | 110.869562 | 20.421424 | 81.58% | 3,512,811 |

Representative untraced component timings (second sample for 10/100, sole sample
for 300), before → after, in seconds. Construction is cumulative `_document`
time including the first export, while validation is `_validate` time. Export
also includes chain-byte/seal checks not attributed to `_validate`. Residual
scheduling therefore includes record preparation/commit guards; these component
boundaries must not be interpreted as eliminating every integrity cost.

| Jobs | Paper adapter | Checkpoint construction | Record/history validation | Result projection | First export | Restore | Completed resume |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 0.614116 → 0.589979 | 0.030555 → 0.005348 | 0.086641 → 0.015237 | 0.007361 → 0.004077 | 0.000001 → 0.007563 | 0.023739 → 0.030705 | 0.010703 → 0.003673 |
| 100 | 6.079426 → 6.069736 | 2.735056 → 0.060572 | 7.220941 → 0.155781 | 0.070349 → 0.046119 | 0.000002 → 0.083365 | 0.229536 → 0.373385 | 0.089431 → 0.039444 |
| 300 | 18.782944 → 18.854174 | 25.046305 → 0.149142 | 66.260314 → 0.474610 | 0.203717 → 0.127199 | 0.000002 → 0.230470 | 0.959755 → 1.263707 | 0.284063 → 0.122492 |

External canonical checkpoint hashing is still a full linear operation: for 100
jobs it was 0.026196 → 0.035434 s and for 300 jobs 0.090819 → 0.089779 s. No hashing
speedup is claimed. Initialization was 0.048039 → 0.043170 s at 100 jobs and
0.368636 → 0.363721 s at 300; existing run-ID setup still repeatedly fingerprints
the full job specification, a remaining quadratic term that was minor in these
bounded samples. The adapter itself remains approximately 6.1 s per 100 jobs and
18.8 s per 300: it is now the dominant measured work, not an optimized trading
engine.

**Cold restore is slower**, because it still fully verifies every supplied record
and constructs independently owned immutable records/summaries/digests. At 100
jobs the representative restore rose from 0.230 s to 0.373 s; at 300 it rose from
0.960 s to 1.264 s. The new first export is also actual work rather than returning
a previously built head; total timings include that cost. These are explicit
tradeoffs, not hidden performance wins.

Separate allocation-traced samples (one per size/implementation):

| Jobs | Traced total before → after (s) | Python peak before → after (bytes) | Fresh-process RSS before → after, untraced representative (bytes) |
| --- | --- | --- | --- |
| 10 | 3.748631 → 2.984016 | 2,857,434 → 2,985,536 | 35,475,456 → 35,844,096 |
| 100 | 115.035175 → 30.825141 | 14,050,165 → 14,175,615 | 56,541,184 → 53,338,112 |
| 300 | Not traced | Not measured | 88,674,304 → 99,430,400 |

**No overall peak-memory reduction is established.** The 100-job traced peak is
essentially flat/slightly higher, and the single 300-job process RSS high-water
sample is higher after optimization. The persistent chain plus detached results,
rendered checkpoint and restored owners retain additional representations; final
full rendering/measurement also allocates large transient objects. RSS varies
with allocator/GC behavior and cannot be attributed solely to the checkpoint.
This is a time optimization, not a proven memory or streaming optimization.

Serialized result-array bytes are unchanged at 117,087 / 1,170,887 / 3,512,665 for
10/100/300 jobs. Corresponding checkpoint hashes are unchanged from baseline:
`00c3b8ce04ebf3ea2161b49fc847538e5108266015e1ade89ad0c13e367fc8ab`,
`78dddd0e0e042ed327666f3378a5152dbc4efd255d9c257e94c93b420783c6da`,
`eeaf3e4e7aad72e7debc1e3b96c0b1662c3089043c79ec459799e9ea3a7c133b`.
All samples executed exactly N scheduled adapter calls and zero adapter calls on
completed resume. One-job explicit semantic replay passed in every sample;
untraced before/after observations ranged approximately 0.059–0.074 / 0.059–0.071 s.
No full 300-job semantic replay timing is claimed.


Reproduction from a source checkout (the benchmark is opt-in, not collected by
normal pytest):

```sh
git show d0a31fa20d674c8c8a58b1c8d75753f47794f2be:src/donghak_stock_vision/backtest/mass_simulation.py > /tmp/dsv-checkpoint-before.py
PYTHONPATH=src:. .venv/bin/python tests/benchmarks/mass_simulation_checkpoint.py --runs 100 --legacy-module /tmp/dsv-checkpoint-before.py
PYTHONPATH=src:. .venv/bin/python tests/benchmarks/mass_simulation_checkpoint.py --runs 100
# Separate allocation-traced sample; do not mix with untraced time comparisons:
PYTHONPATH=src:. .venv/bin/python tests/benchmarks/mass_simulation_checkpoint.py --runs 100 --trace
```

The legacy-module switch loads a developer-supplied local source snapshot solely
in this measurement harness, never in production restore or an API request. Each
sample runs in a new process. Before/after use the same registry, order/clock/cost
fixtures, instrumented method boundaries and unchanged production limits. The
300-job fixture changes only the explicit max-run count and job tuple; its 16 MiB
output cap and per-job budgets remain unchanged. Each invocation was externally
bounded to 180 seconds; no 300-job allocation-traced run was attempted because
the baseline's quadratic traced cost would exceed the bounded reference budget.

Times use `perf_counter`. Total includes scheduler initialization, execution,
result projection and first complete checkpoint export; it does not hide the new
on-demand export cost. Method instrumentation separately times adapter execution,
checkpoint construction, validation and results; residual scheduling is reported
by the JSON driver. Hash/decode and restore/complete-resume are separate operations.
Python peak uses `tracemalloc` only in the explicitly traced cases; those times
must not be compared with untraced timings. Source fixture construction precedes
tracing. RSS is process-lifetime `getrusage(RUSAGE_SELF).ru_maxrss` from each fresh
process, including imports, fixtures, restore and one explicit replay, converted
to bytes for the host platform; it is not attributed solely to checkpointing.
One-job semantic replay is measured; full-batch semantic replay is not claimed.

### Remaining limits and practical next work

Successful 10/100/300 cases are not a proof of 1,000/10,000-run performance. The
whole result/JSON document must still be retained/rendered, restored documents
must still undergo independent full verification, and frequent full exports can
still create caller-driven quadratic work. Paper Broker execution, result
projection and experiment/run-ID setup also consume resources independently.

There is no durable mid-job crash recovery or hard OS CPU/RSS deadline sandbox.
Cooperative time/work limits and the synthetic research-only restrictions are
unchanged. Future scaling should first measure 1,000 bounded synthetic jobs,
profile adapter/initialization and full export/restore separately, and only then
consider explicitly versioned chunked persistence or isolated workers. Preserve
trusted pins and semantic replay; do not relax verification, accounting or risk
checks to produce a faster benchmark.
