# Strategy experiment foundation

This layer plans bounded, reproducible research candidates. It does not load a
strategy, train a model, tune parameters automatically, rank candidates, select a
winner, or authorize execution. The evaluation port currently has a deterministic
**test-only** fixture implementation. No production strategy execution adapter is
provided.

## Boundaries and reuse

`backtest.strategy_experiment` owns the research plan, parameter grids, partition
metadata and candidate identity. `backtest.strategy_evaluation` owns the narrow
`CandidateEvaluator` protocol, pinned assessment owners and summaries. Both belong
to the existing Simulation module. They depend on existing Trading Core native
market/currency identities, `SimulationLimits`, `TimeWindow`, `FrozenJSON`,
canonical serialization and validation utilities. They have no API, CLI, network,
database or concrete broker dependency.

Existing model-training `models.experiment.Candidate` describes a fixed set of
linear/HGB training configurations; it is not a generic strategy configuration.
Its training, final-test locks and historical eligibility remain unchanged. The
new half-open four-partition metadata contract does not reinterpret legacy
inclusive-date research splits.

For available economics, the layer reuses `PerformanceInput`,
`verify_performance_comparison`, original checkpoint pins, original valuation
owners and their independently pinned evidence. Existing comparison verification
performs valuation and Paper Broker semantic replay. No economic formula is
implemented here. Trading Core, Risk Engine, Paper Broker, historical Ledger,
valuation v1, comparison v1, diagnostic v1, walk-forward v1 and checkpoints retain
their original meanings.

## Candidate and plan contracts

A `ResearchPlan.document` is `strategy_research_plan_v1`. It includes:

- Experiment ID; immutable strategy and code artifact references.
- Execution, risk, valuation and original experiment provenance references.
- Market and native currency: KR/KRW or US/USD.
- Four ordered partition descriptors, including dataset version/hash, cohort,
  UTC half-open window and provenance artifact reference.
- Typed parameter schema and finite choices, seed and existing simulation limits.
- Candidate count and UTF-8 configuration byte budgets.

Artifact references use the existing `artifact_id`, `version`, `content_hash`
contract. A mutable branch name without the required exact content digest is
insufficient. Execution policy references pin the existing complete execution
policy document; valuation policy references pin the existing comparison policy.
Risk references pin the canonical ordered tuple of actual fixture risk policies,
serialized by the existing `wire` function. Every scenario in an assessment's
registry must match that risk digest.

A `strategy_candidate_v1` document contains the exact plan hash, normalized
parameter object and candidate ID. The ID is the existing canonical digest of
`{"plan": plan_hash, "parameters": parameters}`. The referenced plan supplies
all remaining immutable context; candidates do not repeat the full plan or data.
Candidate construction and assessment reject inconsistent identities. Changing
any plan input changes candidate identity, including seeds and provenance.

Plans and candidate documents are immutable in-memory values. This foundation
exposes canonical JSON projections; it does not introduce persistence, a generic
JSON-to-code loader or an executable strategy language. Callers must retain the
original typed owners for later verification.

## Finite grids and budgets

Parameter names are unique identifiers in ascending name order. Each axis has an
explicit ordered tuple of choices:

| Kind | Values | Bounds |
|---|---|---|
| integer | Strict integers; booleans/floats rejected | Required inclusive decimal-string minimum/maximum |
| decimal | Exact finite decimal strings, canonicalized | Required inclusive bounds; existing 24-digit/12-exponent limits |
| boolean | Strict booleans | No numeric bounds |
| enum | Nonempty strings of at most 128 characters | No numeric bounds |

There are at most 16 axes, 64 choices per axis and 64 candidates. The caller must
supply an explicit maximum candidate count, configuration byte limit (at most
64 KiB) and `SimulationLimits`. The product cardinality and largest encoded
configuration are checked **before** calling the Cartesian product iterator.
Decimal-equivalent duplicate choices, such as `"0.10"` and `"0.1"`, are rejected.
Floats, duplicate axes, noncanonical axis ordering and empty choices are rejected.
Enumeration uses declared axis-choice order, with the last axis varying fastest.
There is no randomized search or external optimization service.

Plan metadata is limited to the smaller of 1 MiB and `max_spec_bytes`. Assessment
metadata is limited to 64 KiB. Summaries are limited to 8 MiB. Each summary's
aggregate source job count is limited to the smaller of 300 and `max_runs`, before
economic replay. Existing per-comparison bounds still apply. These bounds are
cooperative logical limits, not hard process memory/time isolation. Full source
replay can be expensive; this implementation does not introduce a trust cache or
claim incremental/bounded-memory economic replay.

## Partition and holdout isolation

The plan requires exactly four chronologically disjoint descriptors, in order:

1. Training/development.
2. Tuning/validation.
3. Frozen OOS evaluation.
4. Untouched final holdout.

Windows use existing UTC-aware `[start, end)` semantics. Adjacent boundaries are
allowed; overlapping windows and repeated cohort identities are rejected. Missing
provenance or dataset/version/hash fields fail closed. One frozen dataset snapshot
may have multiple distinct partition windows; this is metadata, not permission to
read the full snapshot.

Candidate enumeration reads metadata only. `accessible_partition()` permits only
training and tuning. OOS/holdout access requests are rejected. The assessment
interface is **tuning-only**: it accepts no OOS/walk-forward report owner, feedback
or prior winner. All supplied comparison owners must use the declared tuning
dataset, cohort, market and currency. Every timestamp in each original synthetic
scenario registry and valuation evidence must lie within the tuning window;
otherwise temporal membership is unavailable and the assessment is rejected
before economic replay. Whole registries containing other-period observations
are rejected rather than filtered to conceal them.

The independently pinned plan authorizes **declared split metadata**. An artifact
reference alone does not prove historical training custody, label purge, PIT
quality, exchange-session eligibility or the truth of an operator's declaration.
This layer does not acquire dataset contents or inspect final holdouts. It cannot
stop an external researcher from looking at data outside this interface. Such
research cannot be represented as independently validated OOS evidence by this
contract. Existing walk-forward verification remains the authoritative future OOS
path and must run as a separate frozen cycle, never feedback to this tuning cycle.
No corporate-action, historical PIT/OOS or operational approval is created.

## Evaluation and verification

A caller implements `CandidateEvaluator.assess(candidate)` and retains the
original `CandidateAssessment` plus an independently trusted assessment hash.
The included `OfflineFixtureAdapter` exists **only in tests**. It returns prepared
synthetic evidence, not an execution of the proposed parameter configuration.

Assessments have three states:

- `completed`: requires original `ComparisonEvidence`, no failure reason.
- `failed`: pinned infrastructure/configuration/budget failure claim; no economics.
- `unavailable`: pinned missing-input/unsupported-evaluation/provenance claim;
  no economics.

Missing assessments remain unavailable with `assessment_missing`. Unsupported
failure codes and a strategy loss masquerading as an infrastructure error are
rejected. Failed assessment claims are provenance-pinned declarations; they are
not independent replay of infrastructure failures. Original comparison run rows
preserve their own failed/cancelled/interrupted/unexamined outcomes.

`summarize_experiment(plan, trusted_plan_hash, assessments, assessment_pins)`:

1. Revalidates the plan and candidate enumeration.
2. Checks independent plan/assessment pins, membership, uniqueness and provenance.
3. Binds the selected original experiment's exact strategy and parameter object
   to its candidate, and checks code, seed, limits, dataset and policy identities.
4. Checks every comparison owner's tuning membership and compatible native market.
5. Requires matching exact evaluation membership across candidate sources, then
   calls existing comparison verification, including original economic replay.
6. Copies verified descriptive statistics and run-level coverage without a new
   return, cost, P&L or scoring formula.

A completed source with zero evaluated returns becomes `unavailable`, while its
original outcomes and exclusions remain visible. Partial economic coverage stays
in the source's expected-case denominator. Missing returns are never zero. A
negative verified return remains a completed economic observation, not a failed
simulation. Different candidate parameter configurations use independently
verified comparison sources; comparison v1's compatibility rules are not relaxed
to combine arbitrary parameter mixes.

The summary `strategy_research_summary_v1` retains candidates in enumeration
order, counts evaluated/failed/unavailable candidates, cohort/policy references,
source comparison/experiment/checkpoint references, exact original return
statistics and immutable fingerprints. No cross-candidate average, ranking,
composite score, selection or promotion is produced. Walk-forward reference is
null with `oos_feedback_forbidden_during_tuning`, not a claim of OOS evaluation.

`verify_experiment_summary` reconstructs the whole projection using original
owners and existing replay. Rehashing changed summary statistics or an economic
comparison does not make them valid. Pins must come from independent trusted
custody; copying a digest out of an untrusted document is not verification.

## Worked synthetic example

The deterministic fixture in `tests/backtest/test_strategy_experiment.py` builds
an approved-by-caller synthetic dataset and a grid:

```python
ParameterGrid("size", "integer", (1, 2), "1", "64")
```

This yields two candidate IDs bound to the same plan, with `{"size": 1}` then
`{"size": 2}`. For a tuning window `[2026-01-05T00:00:00Z,
2026-01-06T00:00:00Z)`, the fixture's original one-share script starts with native
cash 900, buys at 10 and marks at 12. Its already verified return is exactly
`1/450`; both prepared parameter-labelled sources retain that same script result.
**The size parameter does not drive this script.** Equal values demonstrate
projection/replay integrity, not parameter sensitivity or strategy effectiveness.

Supplying only the first source produces candidate count 2, evaluated count 1,
unavailable count 1. The second candidate has `assessment_missing` and null
metrics. Supplying independent prepared sources for both yields evaluated count 2.
The negative-mark fixture uses 9 and preserves two completed loss observations,
not infrastructure failures. KR and US versions keep native KRW/USD separate.

No synthetic report IDs are operational IDs. No result demonstrates genuine
historical market profitability, out-of-sample generalization or execution realism.

## Future work

A real strategy adapter must independently prove parameter consumption, immutable
code/artifact identity, risk/execution policy use and per-partition source custody.
It must reuse the existing bounded simulation infrastructure and preserve original
fills/valuations. A separate frozen OOS stage must reuse walk-forward source owners
and trusted pins, with independently evidenced strategy freeze, purge/embargo,
calendar/session and label timing. Untouched final-holdout access needs a separate
explicit review and must not feed the same candidate cycle. Optimization, ranking,
automatic selection/promotion and live trading remain outside this foundation.
