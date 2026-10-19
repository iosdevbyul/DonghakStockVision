# Shared historical evidence v1

This optimizes the storage and loading of **analysis inputs**, without changing
inference, policies, fills, accounting or qualifications. It is not a new database.
The table lives in a frozen JSON document, belongs to one run, and has no network
or model loader. Legacy trade receipts and reports remain fully readable.

## Investigation and baseline

FrozenJSON holds canonical detached JSON; its identifier is SHA-256 of that JSON.
FrozenAnalysis originally holds a complete bundle per decision. Linear bundles
repeat model, dataset, inference snapshot and training snapshot (often the same
snapshot twice). HGB repeats two model descriptors, experiment records, quality
and the inference snapshot both directly and in public evidence. Signals, feature
windows, cutoffs, scores and observation receipts remain decision-specific.

AnalysisStore already stores immutable source artifacts by digest. ResearchRunStore
routes model/dataset/snapshot reads to the source and generated signals to isolated
output. No source schema or store behavior needs changing. Position History
replays trade receipts through FrozenAnalysis and the existing engine/execution
validators; Performance verifies history, ledger frames and marks. The report
projects these facts and hashes, rather than replacing their validation.

The initial genuine synthetic linear fixture had 5 decisions, 1 model and 1 source
snapshot. Its legacy frozen request was 4,291,364 bytes and detailed report 39,062
bytes. Fixture preparation plus complete execution/report under tracemalloc took
124.20 seconds and peaked at 140,725,981 traced Python bytes. This includes imports
and fixture training and is **not comparable** to the isolated loading measurements
below. The same request in shared form measured 804,239 bytes with 14 artifacts.
Full run, ledger, history, Performance and report equality were checked.

## Explicit transport versions

- Frozen replay request `version: 1`: unchanged legacy representation.
- Frozen replay request `version: 2`: `analyses` contains typed root references and
  an additional `evidence_table` using `shared_historical_evidence_v1`.
- Historical input `historical_input_v1` / version 1: unchanged legacy representation.
- Historical input `historical_input_v2` / version 2: same metadata, typed analysis
  references and the same table contract.

`freeze_request(..., shared=True)` is the default writer; `shared=False` explicitly
emits v1. `run_backtest_with_report` reads both versions. `pack(legacy)` and
`expand(shared)` provide a validated migration/export path; existing files are
never rewritten. Expanded shared input equals the original canonical v1 document.
A bare version 2 without its table is rejected, rather than interpreted as v1.

The low-level historical builder retains its legacy default and exposes
`shared_evidence=True` for incremental capture. The high-level
`run_historical_backtest` selects sharing by default and offers
`shared_evidence=False` for explicit legacy comparison/export. HGB and linear
providers are unchanged. Both use their original input validation.

## Table and identity rules

```json
{
  "version": "shared_historical_evidence_v1",
  "run_id": "original RunManifest identifier",
  "artifacts": [{
    "artifact_id": "SHA-256 of canonical fully reconstructed payload",
    "kind": "an explicitly supported evidence kind",
    "source_id": "original source identity",
    "payload": {"snapshot": {"$evidence": {"artifact_id": "...", "kind": "snapshot"}}},
    "analysis_ids": ["sorted original signal identities that reference this artifact"]
  }]
}
```

Each root record has `analysis_id` (original signal ID), `artifact_id` (original
complete bundle digest), and `kind: analysis`. Content IDs are computed before
reference substitution. A bundle digest is not relabeled as its signal ID.

Supported components are analysis, linear model, dataset, snapshot, quality,
signal, HGB public envelope/provenance/models, each original HGB model descriptor,
experiment, lock, qualification and plan. Only declared schema paths may contain
references. Dataset samples stay inside the shared dataset; there is no per-cell
object store or arbitrary recursive object loading.

`source_id` equals the canonical content ID except for exported HGB locks and
qualifications, whose original IDs exclude the embedded `lock_id` / `report_id`.
Both the full artifact digest and original source digest are recomputed and
checked. Original source IDs inside payloads remain unchanged, including model,
dataset, training snapshot, both HGB fingerprints/checkpoint SHAs, calendar,
feature/label/runtime contracts and qualification/experiment links. Unknown HGB
model creation time and attribution remain null/unavailable; no linear dataset
or model is invented.

Writers sort artifacts by ID and consumers by original analysis ID. Repeated
references to identical content reuse one entry. A different canonical body gets
a different ID. Readers reject duplicate definitions (including identical duplicate
definitions), conflicting payloads, missing references, wrong kinds, checksum or
source-ID mismatches, cycles, undeclared consumers and unreachable artifacts.
Repeated root references are preserved; the existing Runner still rejects duplicate
analysis IDs in a decision run rather than silently dropping them.

A table is bound to the recomputed RunManifest ID. Both the table reader and direct
FrozenAnalysis construction check this binding. Reusing a model in a different
run requires a newly validated table for that run, with the same immutable source
ID when content is genuinely identical. Hash equality is not authentication or
proof of historical availability; all existing release, cutoff, provenance and
PIT restrictions still apply.

## Reconstruction, caching and compatibility

The reader resolves and verifies the full graph before decision validation. It
then calls the original FrozenAnalysis validator for **every** analysis. Public
views return detached copies. The immutable graph retains shared children rather
than expanded canonical bundle strings per date. Original bundle identifiers
remain their verified legacy content digests.

A verified graph and its validated analyses are cached only on the owning immutable
FrozenJSON document. New or modified JSON creates a fresh validation boundary;
there is no process-wide mutable store, unbounded global cache, or skip-validation
flag. FrozenAnalysis also caches its scalar analysis ID, so Runner does not parse
all full bundles merely to find a decision's ID at every event.

Run/decision/order/fill/checkpoint/history/Performance/report identities remain
identical for equivalent frozen inputs. Legacy historical receipts explicitly
export the original evidence representation. The old original canonical input hash
is streamed one analysis at a time, with the existing signal-audit normalization
retained for HistoricalBacktestInput.identifier. This avoids reconstructing the
entire legacy analysis array merely to compute its hash.

The **transport document identifier changes** because its canonical structure and
version change. It must not be confused with the preserved logical/source hashes.
HistoricalBacktestResult adds `historical_input_transport_hash` for shared input;
`historical_input_hash` keeps its original normalized legacy meaning. The detailed
report remains `historical_trading_report_v1`, with unchanged facts and hash.

## Bounded measurements

Run the offline synthetic benchmark with:

```sh
PYTHONPATH=src python -m tests.backtest.shared_evidence_benchmark
```

It uses actual public linear and trusted HGB inference over temporary deterministic
fixtures. There is no protected DB access or full-market capture. Each family has
1, 10, 50 and 100 **distinct** signal dates, and every record is reconstructed and
validated with FrozenAnalysis. Matching legacy bundle hashes are asserted at each
scale. HGB has one pair reference containing two original model descriptors and
separate inference window identities; training remains a separate source reference.

The benchmark times serialization, canonical deserialization and full **analysis
validation**, separately. It is not a 100-date trading/performance backtest. Complete
trading equivalence is tested separately with genuine scored linear/HGB fixtures
and the historical-service path. No timing or reduction-ratio threshold is used
as a unit-test oracle.

Memory is measured in a separate tracemalloc run after fixture setup, including
fresh document loading and validation of all analyses. It measures Python
allocations, not native estimator/RSS memory. Profiling is capped at 50 decisions
to bound instrumentation overhead; 100-date memory is explicitly unavailable.
Wall-clock timings are single measurements on the shared development host and
are observations, not latency guarantees. See the accompanying benchmark results.

## Remaining scope and evidence limits

Legacy-compatible **output trade receipts still embed expanded evidence**, and
explicit `expand()` intentionally materializes a legacy document. This change does
not claim bounded memory for a complete long trading journal or optimize the
receipt archive. A future explicitly versioned receipt format can address that
separately while preserving validators. Source capture and validation still cost
work per changed decision; very small runs can have table overhead and need not
improve every timing or memory metric.

No model code, strategy, prices, fees/taxes/slippage, reservation revision protection,
source DB/checkpoint/approval, Security Metadata, Operations or UI is changed.
Synthetic fixture qualifications are test contracts, never real approval evidence.
Corporate Actions, Runner Tail, Run Input Freeze, strict PIT and operational gates
remain unchanged. No real-market replay, live data collection or external market
API request is performed. CI monitoring is deliberately omitted for this PR at
the user's request; local offline validation and builds remain required.

Next recommended PR: `feat/shared-trading-receipts`, a separately versioned
single-ticker receipt/archive reference format with exact legacy reconstruction.
Do not begin concurrent orders or portfolio expansion as part of this work.

For opt-in compact **run outputs**, see [trading receipt archives](shared-trading-receipts.md).
This does not change the shared v2 input or default legacy result contracts.
