# Mass Simulation Orchestrator foundation

This is offline research orchestration infrastructure, not strategy optimization,
a trading service or an investment recommendation. A successful run means that
an adapter returned a structurally valid result within the observed budgets.
It says nothing about profitability, qualification, PIT approval or permission to
trade. No CLI, HTTP endpoint, database, worker process or provider is added.

## Boundaries and existing contracts

The additive modules stay in the existing Simulation domain:

- `backtest/simulation_spec.py`: immutable experiment/job specification and
  mandatory limits. It reuses Trading Core `Market`/`Currency`, existing
  `FrozenJSON`, canonical serialization/digest and artifact reference validation.
- `backtest/mass_simulation.py`: the `SimulationRunner` protocol, cooperative
  `RunBudget`, cancellation token, structured failures and sequential
  `BatchScheduler`. Checkpoints and versioned result records reuse `FrozenJSON`.
- `tests/backtest/test_mass_simulation.py`: a tiny explicitly synthetic adapter
  with no execution, inference, market data or economic accounting.

Dependency direction is Simulation → existing Core/Market value utilities.
Neither module imports FastAPI, SQLite, HTTP clients, data providers, models,
Paper Broker implementations or application/CLI code. Existing architecture
checks and debt inventory remain unchanged.

The existing `models.experiment` contract selects/trains research candidates and
uses AnalysisStore; it is not a generic simulation scheduler. `BacktestRunner`
already owns frozen historical input validation and accounting. Paper Broker
already owns risk checks, cash/share reservations, fills and simulated command
replay. This scheduler duplicates none of those economic or training models and
does not modify their existing interfaces.

**Actual BacktestRunner/Paper Broker execution adapters are not implemented in
this PR.** The port is the connection point, not proof that either integration
exists. Future adapters must validate and pin their actual inputs using those
components' existing validators, retain research restrictions, use explicit
simulation clocks/prices/policies and return bounded result summaries or evidence
references. They must preserve risk denial diagnostics and never accept an
orchestration success as operational approval.

## Immutable inputs and identity

`ExperimentSpec` requires:

| Field | Meaning |
| --- | --- |
| `experiment_id` | Caller-assigned logical experiment name, bounded to 128 characters |
| `strategy`, `dataset`, `code`, `runner` | Existing `artifact_id`, `version`, `content_hash` reference objects |
| `parameters` | Canonical immutable common parameters |
| `execution_policy` | Explicit immutable adapter policy document |
| `seed` | Nonnegative integer seed; no global random state |
| `market`, `currency` | Existing KR/KRW or US/USD pair; mixed/FX books require later adapters |
| `data_origin` | `synthetic` → `synthetic_test_only`, or `real` → `research_only` |
| `limits` | Mandatory resource configuration, no permissive defaults |
| `jobs` | Nonempty tuple of unique bounded keys and immutable job parameters, in execution order |

References identify bytes but are not attestations of authenticity, existence,
input availability or data-use approval. The scheduler does not open datasets or
validate strategy/model contents. Input validation belongs to each adapter; a
missing or incompatible input must fail explicitly before simulation work.

The experiment fingerprint hashes its complete versioned canonical specification
(`mass_simulation_experiment_v1`). Each run ID hashes that fingerprint and the
job definition. Distinct experiment IDs or changed immutable configuration have
different namespaces; jobs cannot overwrite one another. This in-memory layer
creates no filesystem output paths. Future persistence must use these namespaces
and reject conflicting writes.

The job seed is the first 64 digest bits of the canonical experiment seed and
job key. An adapter receives that seed explicitly; it must not use global RNG,
wall-clock dates or external mutable inputs. Identical immutable inputs and a
deterministic adapter produce identical successful output bytes, record hashes
and checkpoint hashes. Timeouts and caller cancellation are external conditions:
results affected by those conditions are not promised to match an uninterrupted
run. Runtime durations and current timestamps are deliberately absent from
canonical evidence. Run order is the supplied job tuple, never completion order.

## Lifecycle, failures and results

Jobs with no record are queued. Execution is synchronous and not checkpointed as
a durable running state. Each committed attempt is a `mass_simulation_run_v1`
record with experiment fingerprint, run ID, job key, attempt number, derived seed,
status, consumed logical steps, output or failure, and its canonical result hash.

| Status | Behavior |
| --- | --- |
| `succeeded` | Bounded canonical object returned; never rerun on resume |
| `failed` | Explicit diagnostic or unexpected exception; isolated from later jobs, never automatically retried |
| `cancelled` | Caller stop observed; stop batch, retain attempt, eligible for bounded explicit resume |
| `interrupted` | `KeyboardInterrupt` observed; stop batch, retain attempt, eligible for bounded explicit resume |

`results` returns the latest attempt per examined job in specification order.
`checkpoint().to_dict()["records"]` preserves all committed attempts in transition
order. A clean resume keeps successful/failed records exactly, retries the last
cancelled/interrupted job, then continues untouched jobs. It never reruns a
committed succeeded/failed job. Resume is an explicit caller action; there is no
automatic retry loop.

`SimulationFault` carries a `FailureCode` plus a bounded stable detail:

| Code | Interpretation |
| --- | --- |
| `invalid_configuration` | Adapter configuration or invalid/nonmonotonic injected clock |
| `missing_input`, `incompatible_input` | Adapter input evidence absent/incompatible; runner reference mismatch is rejected before batch execution |
| `execution_exception` | Unexpected adapter exception; only exception class name is recorded, not messages/tracebacks/secrets |
| `risk_denied` | Adapter explicitly reports a risk-blocked simulation; distinct from infrastructure failure |
| `timeout`, `budget_exhausted` | Observed cooperative deadline or retained-output/logical-work/attempt budget exhausted |
| `cancelled`, `interrupted` | Explicit stop/interrupt, not a strategy loss |
| `checkpoint_mismatch`, `checkpoint_corruption` | Missing trusted pin, incompatible version/configuration, malformed or altered checkpoint |
| `result_integrity_mismatch` | Invalid, noncanonical or oversized adapter result |

Malformed specification constructors raise the existing structural `ContractError`
with stable validation reasons before a scheduler can run. Checkpoint restoration
raises a structured fault rather than creating a misleading per-job result.
Ordinary negative returns, losses or rejected simulated actions may be factual
fields in a successful adapter result. They are not automatically infrastructure
failures. An adapter decides whether a risk denial prevents the whole run or is
an observed rejected action; no ranking, optimization or parameter revision is
performed here.

## Enforced budgets and limitations

All `SimulationLimits` fields are mandatory:

- `max_runs`: positive, at most 10,000; the job tuple must fit this count.
- `max_attempts_per_run`: positive, at most 10, including the original attempt.
  If the last permitted attempt is cancelled/interrupted, it is committed as
  `failed/budget_exhausted` with `attempt_limit_after_cancelled` or
  `attempt_limit_after_interrupted`. Previous attempts remain intact. This avoids
  infinite cancellation history; it does not run another simulation.
- `concurrency`: only **1** is supported. Other values fail closed, with no worker
  pool or implicit fallback. Use one owning caller per scheduler; it is not a
  concurrent scheduler service. Reentrant execution is rejected.
- `max_steps_per_run`: positive logical units, at most one billion. Adapters must
  call `consume(n)` at meaningful work boundaries; exact limit is allowed, the
  next unit is denied without increasing the counter.
- `max_run_seconds`: positive, at most one day. An injected monotonic clock is
  checked before/after each adapter call and at cooperative checks. Exact
  deadline is expired. Timeout output is discarded.
- `max_result_bytes`, `max_total_result_bytes`: positive canonical UTF-8 output
  byte caps, aggregate at most 16 MiB. No truncated/rounded results are accepted;
  the first over-budget result is rejected while prior results remain intact.
- `max_spec_bytes`: positive, at most 4 MiB for the complete canonical spec.
  References are separately limited to 4 KiB each and parameters to 16 KiB each.

Checkpoint size additionally has a fixed 4 KiB metadata allowance per configured
job/attempt, plus the total result cap. This is a retention bound, not a process
memory measurement. Configurations near the foundation caps can retain hundreds
of MiB of metadata. Do not select these maxima without independent measurement.

**This is not an OS sandbox.** An uncooperative adapter cannot be preempted and
may allocate memory before returning or block indefinitely. There is no hard CPU,
RSS, process-count or I/O enforcement. Deadlines detect excess only at cooperative
boundaries. No claim of hard wall-clock timeout or bounded adapter peak memory is
made. A future isolated execution adapter must implement actual process resource
controls before accepting untrusted strategy code. No unbounded processes or
threads are started by this foundation.

## Checkpoint and resume

`mass_simulation_checkpoint_v1` contains the exact experiment fingerprint and
all committed versioned attempt records. New state is built and validated before
one immutable in-memory head replacement. Failure during construction/validation
leaves the previous head intact. Readers may retain prior `FrozenJSON` owners.

Resume requires both the original specification and an **independently trusted
checkpoint hash**. It validates the canonical bytes/pin, exact version/config,
record schemas/digests, job/run/seed identities, transition order, duplicate
terminal runs, attempt numbers, status/failure consistency and all retention
limits. Unknown versions, changed configs and missing/untrusted pins are rejected.
There is no unsafe pickle, migration or database access.

```python
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler

# scheduler/spec/adapter are the caller's already validated objects.
checkpoint = scheduler.checkpoint()
trusted_pin = checkpoint.identifier  # Retain separately through a trusted channel.
restored_bytes = FrozenJSON(checkpoint.payload_json)
resumed = BatchScheduler(
    spec, checkpoint=restored_bytes, expected_checkpoint_hash=trusted_pin
)
results = resumed.run(adapter)  # Supply a fresh, noncancelled token to continue.
```

Hashes provide integrity against a trusted pin, not signatures or authenticity.
A party able to replace both the document and trusted pin can fabricate a
structurally plausible output; semantic result verification requires the actual
adapter/evidence validators or independent replay. The scheduler does not replay
completed simulations when loading a checkpoint.

There is **no durable process-crash recovery**. Filesystem atomic write/fsync,
checkpoint custody and hash pin storage are outside this PR. An exception during
a checkpoint transition, process death, `SystemExit` or interrupt outside the
adapter boundary may lose an uncommitted attempt. Reexecuting it requires a pure,
independent deterministic adapter; exactly-once external side effects are not
supported. A failed adapter must not contaminate subsequent jobs through shared
mutable account state. Use fresh validated broker/runner state per future job.

## Reproducible 10-run and 100-run examples

These source-checkout developer examples reuse the **test-only** minimal fixture,
not a packaged production simulation command. They need no datasets, credentials,
network or broker and do not claim Paper Broker/backtest integration.

```sh
PYTHONPATH=src:. .venv/bin/python - <<'PY'
from tests.backtest.test_mass_simulation import experiment, SyntheticRunner
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler

for count in (10, 100):
    spec = experiment(count)  # All explicit limits are visible in the fixture.
    scheduler = BatchScheduler(spec)
    results = scheduler.run(SyntheticRunner())
    print(count, len(results), spec.fingerprint, scheduler.checkpoint().identifier)
    print(len(scheduler.checkpoint().payload_json.encode()), results[0].to_dict()["output"])
PY
```

Observed with this exact fixture:

| Jobs | Succeeded | Canonical checkpoint bytes | Experiment fingerprint | Checkpoint hash |
| --- | --- | --- | --- | --- |
| 10 | 10 | 5,002 | `129e7d2888ee163d2f86a6909ab41d50509a83e7db475945d83d9f61556b726d` | `dddcbd0dfb9ad19f152ff81e050931736f536b25d2128c6a631950945f8c13f9` |
| 100 | 100 | 48,776 | `ae3405b21f551b26183a293a96c4af6e0a1753cccdff04157accc1fbeed5805d` | `b78fe23356691ab0c0a796ae1ebbbfeead248495e65f0987bf11279b2c2a586a` |

The first output in both examples is
`{"draw":144,"index":0,"rejected_actions":1,"strategy_loss":"-1.25","synthetic":true}`.
The loss/rejection fields are deliberately synthetic constants, not actual P&L
calculations or risk evaluations. Repeated tests compare the complete records
and checkpoint bytes, not just these summary values.

Focused offline validation:

```sh
.venv/bin/pytest -q tests/backtest/test_mass_simulation.py tests/trading tests/architecture
```

The offline test harness forbids TCP connections. Tests also guard against
activation of the existing Paper Broker/risk service, retain original checkpoint
objects, and test atomic head replacement failure. All market/evidence/model files
remain outside execution; protected artifact hashes are compared separately.

## Toward 1,000 and 10,000 jobs

No 10,000-job CI workload is added. The 10/100 cases validate mechanics, not
production throughput. The current checkpoint implementation copies/serializes
and validates retained records on every transition; cumulative checkpoint work
is quadratic in batch length and memory includes old/new immutable documents.
This PR does not claim streaming, constant-memory replay or throughput gains.

Before scaling, measure 1,000 synthetic jobs locally under explicit budgets,
profile serialization/retention and adapter costs, then design independently
verified chunked durable storage and resource-isolated execution. Preserve the
versioned input/result identities and trusted-pin boundary. Only then evaluate
10,000 runs or bounded parallel execution with deterministic ordering. Do not
silently change economics, risk, data selection or fill assumptions to increase
throughput.

Future adapter tests must cover fresh Paper Broker books per job, explicit clock
and market frames, reservations/fill evidence, risk denials and accounting hash
consistency. Future strategy selection needs predeclared comparisons, independent
OOS validation, leakage/PIT checks and separate governance review. Running many
synthetic experiments grants none of those approvals. Existing Corporate Actions,
Runner Tail, Run Input Freeze, strict PIT and operational restrictions remain.
