# Compact immutable trading receipt archives

## Baseline and integration design

Base main: `46c1ea52106f7554d8e6133ab123e69cd66e80fe`, merged PR #112.
Before implementation, the existing synthetic three-fill and twenty-fill artifacts
were inspected without changing them. Their canonical sizes are 12,525,245 and
84,618,984 bytes. In the twenty-fill case, 41 identical historical-input descriptions
occupy 55,975,537 inclusive bytes. Twenty analysis bundles occupy 23,106,310 bytes;
20 acceptances 29,371,905; Position History 52,764,833; checkpoints/account snapshots
4,297,081; admission/policy evidence 157,720; projections 467,960. These are nested,
**inclusive and non-additive** measurements; details are in the baseline JSON.
The unique per-event decisions, timestamps, reservation transitions, fills, releases
and ledger event order must survive. Model/snapshot portions are shareable; different
ledger revisions are not interchangeable.

The existing shared analysis DAG validates original model/source identities but
only reduces frozen input. Execution acceptance embeds the entire historical input;
Position History embeds acceptance, analysis and decision/execution packets. Reports
and final checkpoints repeat account data. The economic authorities remain the
existing Runner, Execution, Ledger, Position History, Performance and projections.

Add an opt-in transport rather than change any existing result contract. A typed,
run-bound content-addressed graph retains exact canonical source hashes. References
use separate JSON paths and null placeholders; source JSON containing reference-like
keys is never interpreted as transport. Container arrays retain their original order.
Canonical expansion must reproduce the entire original JSON before existing replay
validators approve it. A distinct transport hash never replaces an original hash.

Encoding starts from an existing result, so that expanded result already occupies
memory. Graph verification and streaming legacy export should avoid holding a second
expanded graph. Full validated expansion still materializes the legacy result and
runs the original engine; it is not a bounded-memory engine or a speed guarantee.
Explicit limits bound transport bytes, expanded bytes, artifact/reference counts,
structural depth and total digest work. No new database or publication endpoint.

## Interfaces and trust levels

```python
from donghak_stock_vision.backtest.receipt_archive import ReceiptArchive, encode_result

archive = encode_result(existing_result)   # no inference, strategy or accounting
archive.verify_transport()                 # graph integrity only
transport_hash = archive.transport_hash    # different from legacy document hash
restored = archive.expand_validated()      # original complete replay + exact equality
assert restored == existing_result
```

Supported roots are the unchanged five-ticker result v1 and HistoricalBacktestResult
v1. For an existing `ReportedBacktest`, `receipt_validation.reported_result(frozen,
reported)` creates the explicit `reported_backtest_archive_source_v1` envelope;
legacy single-ticker frozen requests v1 and shared v2 both remain readable. Nothing
silently rewrites an old result, receipt, position proof, performance or report.
The historical service's existing post-capture execution body is extracted into
`run_frozen_historical` for database-free replay; its calculations are unchanged.

`shared_trading_receipts_v1` has run_id, legacy_hash, a typed root reference,
metadata, three root component views and sorted artifact definitions. Every reference
binds run_id, kind, exact original source_hash and a domain/run-bound artifact_id.
The address is SHA-256 of canonical `{version, run_id, kind, source_hash}`. Artifact
payloads contain original JSON with referenced child containers replaced by null;
separate sorted path/reference records specify exact reconstruction. Original nulls
and reference-looking user keys are unambiguous. Canonical ordering applies to the
archive definitions, never source event/ledger arrays.

The source hash is the original entire container's canonical digest. Original model,
order, fill, event, source and ledger IDs remain inside the reconstructed bytes;
self-identifying contracts retain their original semantic IDs. Identical complete
receipts share one definition. Different ledger revisions retain distinct complete
hashes even when immutable children are shared. Explicit artifact kinds distinguish
results, runs, events, ledger events, acceptances, orders, executions, fills,
releases, checkpoints, position proofs, trade packets, analyses and projections.
Other JSON containers use object/array kinds. No second economic ledger exists.

`iter_legacy_json()` verifies the immutable graph and streams canonical legacy JSON
without keeping a second expanded graph. `component("performance")`, `component("run")`
and `component("detailed_report")` retrieve transport-verified original projections.
These operations do **not** approve economic validity or publication. A future API
can use those views for summary, per-ticker results and timeline, but should require
`expand_validated()` and its original evidence validation before treating an archive
as an executed valid research result. No HTTP endpoint or publication approval is
added here.

## Integrity and explicit bounds

Verification rejects duplicate definitions (including identical duplicates), unused
artifacts, missing references, mismatched kinds/run namespaces/digests, modified
payloads, wrong null placeholders, overlapping/invalid paths, wrong declared sizes,
noncanonical definitions, views or metadata, and cycles. Lengths are derived from
compact payload plus child lengths before expansion; bounded digest work then
verifies each source hash. Original array ordering and every timestamp are retained.
Full validated expansion delegates to existing replay and compares the entire result,
including decisions, admissions/rejections, reservations, receipts, checkpoints,
Position History, P&L, valuations, timeline, rendered report and their hashes.
A freshly re-signed invalid transport therefore does not bypass economic validation.

`ArchiveLimits` defaults: 64 MiB transport, 256 MiB expanded canonical source,
25,000 artifacts, 250,000 references, depth 64, 5,000,000 physical payload nodes,
and 2 GiB total canonical digest work. Callers can provide explicit positive limits.
Limits bound byte/graph work, not the Python heap of a materialized result or engine.
The `ReceiptArchive` frozen owner caches only its verified compact graph; detached
views cannot mutate it. New or modified documents validate independently. There is
no process-global receipt cache or persisted approval.

Full encoding still starts from an already expanded FrozenJSON result and parses
it. Full validated expansion materializes the original result and performs original
replay. Neither path is a streaming trading engine or a bounded-memory replay claim.
Cold graph verification and encoding add hashing/reference work. Measurements must
separate JSON transport size, projections, encoding/decoding, reference verification,
legacy text expansion, original replay, and instrumented Python allocation scope.

Single-use storage-only nodes are inlined after dependency construction; only
actual repetition justifies their reference overhead. Individual trading/event,
checkpoint and projection identities stay explicit. This deterministic pass changes
only transport layout, never original container hashes or array order. The initial
prototype slightly inflated genuine zero-order examples; this was caught by size
assertions and fixed in the writer without relaxing them. Original JSON/transport
hashes are intentionally distinct even when the expanded source is identical.
The original single-ticker report uses `schema_version`, while the five-ticker
report uses `version`; both are recognized without altering either source schema.
The source digest-work budget applies per encoding/graph-verification operation.
Encoding counts duplicate source bodies too: proving equality still requires their
original content hash. It does not silently treat repeated input as zero work.
The writer parses the original result once and reuses the root's already computed
source hash; it does not parse the entire expanded result again merely to name it.
The baseline JSON also separates actual event headers, decision records and execution
receipts from nested static analysis components. Those unique observations remain
individually auditable; their measured subcategories are still not additive totals.
This is opt-in storage: very small or already minimal inputs can still have graph
metadata overhead. No universal compression ratio or historical profitability is
claimed. No public route, new training/strategy/fees/taxes, operational qualification,
Security Metadata, protected database, or iOS/Web changes are introduced.

Final bounded measurements and phase definitions:
[measurement report](shared-trading-receipts-benchmarks.md),
[raw metrics](shared-trading-receipts-benchmarks.json).
