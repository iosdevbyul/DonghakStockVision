# Bounded multi-prediction receipt reconciliation

## Scope

`storage.prospective_receipt_cohort` composes single-case receipt verification
against freshly replayed invocation-local original evidence, producing
`prospective_receipt_cohort_reconciliation_v1`. It accepts 1–32 cases and never
uses a supplied summary as evidence. No original journal, signature verifier,
consumer, result or custody contract is changed. There is no new store, registry,
network service, trading integration or performance evaluator.

The full original EvaluationSources must contain exactly the policy's complete
predeclared membership. Extra/missing source cases, duplicate predictions, invalid
original evidence, incompatible scopes and unsupported shared outcomes remain
whole-request failures under existing contracts. All original-source errors
propagate; no broad exception handler changes them into missing-receipt counts.

## Shared policy and missing prediction receipts

Supply one original policy ReceiptOwner, reused unchanged for every pair, and a
dictionary of prediction ID -> ReceiptOwner for the available prediction receipt
references. Each pair still passes through the original two-role verifier. The
shared policy receipt is not fabricated or duplicated into different identities.
It is verified repeatedly, not implicitly trusted by a cache.

A separately retained expected-ID configuration is required:

```json
{
  "version": "prospective_receipt_cohort_expectations_v1",
  "prediction_receipt_ids": {
    "<original prediction ID>": "<retained expected receipt ID>"
  }
}
```

This strict FrozenJSON configuration must cover the complete declared cohort and
have a separately retained canonical pin. It is an input reference list, **not**
a receipt registry, independent custody proof or approval. Expected IDs must come
from the operator's retained original/request references. The adapter cannot
invent an unknown expected ID on behalf of an issuer. If it is unknown, obtain
that reference before requesting this integration; no placeholder evidence is
created.

Omitted dictionary keys are missing receipts, not omitted predictions. For such
cases the original artifact is recovered through existing journal replay and
paired with the supplied expected ID and receipt/pin set to None. No signed body,
issuer assertion, timestamp or receipt file is manufactured. Explicit owners
with both receipt and pin None are likewise missing. A partial reference such
as a pin without body is supplied but invalid under the original verifier.

All expected prediction receipt IDs are globally unique and distinct from the
single shared policy receipt ID. That shared policy identity is counted once in
this uniqueness domain; original pair-level duplicate validation remains intact.
Extra receipt dictionary keys, wrong roles, conflicting expected IDs, substituted
artifact owners and wrong original policy owners fail the complete request.
Malformed/signature-invalid receipts for otherwise correct original owners are
accounted per case by the existing verifier. Oversized input is an envelope failure.

## Read-only API

```python
from donghak_stock_vision.storage.prospective_receipt_cohort import (
    reconcile_cohort, verify_cohort,
)

report = reconcile_cohort(
    original_sources, shared_policy_receipt_owner, prediction_receipt_owners,
    expected_receipt_ids, retained_expectations_pin,
    configured_public_anchor, retained_anchor_pin, explicit_utc_as_of,
)
verify_cohort(
    report, retained_report_pin, retained_original_source_reference_pin,
    original_sources, shared_policy_receipt_owner, prediction_receipt_owners,
    expected_receipt_ids, retained_expectations_pin,
    configured_public_anchor, retained_anchor_pin, explicit_utc_as_of,
)
```

Original artifact, receipt, policy, source and anchor pins retain their existing
meanings. Verification reconstructs every original case and signature, checks the
separately retained source-reference pin and compares the complete canonical
report. Self-rehashed altered counts, reordered cases, changed references or
forged flags fail semantic verification. Canonical prediction-ID ordering makes
caller dictionary and source-tuple ordering irrelevant.

The receipt-owner map is detached on entry; no mutable verification cache is
created. Every pair receives the same configured public anchor and shared policy
owner. Full authoritative custody replay runs at entry and again immediately
before successful return. Between those boundaries, the function owns a detached
FrozenJSON custody report and a tuple of original prediction artifacts recovered
through existing `inspect_prediction` replay. No public parameter accepts a
verified context, trusted status flag or previously computed summary.

The existing public single-case API still performs its original full replay.
Its unchanged report assembly was extracted into a private helper so the cohort
can reuse that code without duplicating signature, artifact, chronology or report
semantics. The helper is not an authentication authority: public semantic verifiers
always reconstruct original owners. Only invocation-local replay-derived values
reach it through the public cohort path.

The final authoritative custody report must exactly equal the initial one, or
`cohort_source_changed_during_replay` rejects the result. This checks persistent
changes during receipt verification and serialization, including later cases.
Original integrity failures still propagate without broad exception suppression.
This is a consistency boundary, not an atomic filesystem snapshot or continuous
tamper monitor: a change fully restored between reads is not necessarily detected,
and evidence can change after a function returns. No context survives into another
invocation; every verification repeats both boundaries. Verified source hashes,
journal heads and future provenance remain authoritative at each boundary.

Only one common custody report is retained in output. Per-case projections retain
exact single-report pins, findings, original artifact identities, receipt diagnostics
and local future state/reason. Those semantic fields are unchanged.

## Accounting and diagnostics

The denominator is always total declared predictions, including every missing,
pending, unavailable or invalid case. Counters distinguish:

- Supplied receipt references (body or pin present) versus completely missing ones.
- Verified prediction signatures versus pairs with both policy/prediction
  signatures verified against exact original artifacts.
- Failed verification attempts and invalid reconciliations. A supplied reference
  that cannot verify (including missing anchor) is an unsuccessful attempt;
  missing trust/unsupported receipts remain unresolved rather than automatically
  invalid. An invalid shared policy can fail every pair.
- Predictions not cryptographically verified: declared minus verified pairs.
- Original local future observations pending, unavailable or invalid.

Some counters deliberately overlap: valid signatures can coexist with invalid
chronology diagnostics. The exact per-case reason is retained. Counters are not
a disjoint classification of prediction performance.

`cryptographic_coverage_diagnostic` uses integer numerator/denominator and an exact
all-declared-signatures boolean. It is **not prospective evaluation coverage**,
accuracy, investment return or approval. Even 32/32 verified pairs with absent
future observations are unauthenticated for genuine prospective purposes.

The unchanged consumer requires one exact common horizon/gate. Therefore pending
and mature observations cannot coexist at one `as_of`; this module does not
fabricate a mixed-horizon cohort. Tests cover verified/unavailable together and
all-pending separately. Invalid future evidence remains independently accounted
without invalidating an otherwise correct prediction signature.

## Bounds and computational cost

Original case/request bounds remain 32 cases, 256 KiB per request and 2 MiB
aggregate original requests. Expectations and each receipt/anchor are limited to
8 KiB; each canonical artifact is limited to 128 KiB. Counts plus per-document
limits also bound total supplied receipt material. Unknown expectation versions
and fields, malformed pins and oversize inputs fail closed.

For N cases there are N logical single-case assembly invocations, no more than
2N Ed25519 checks (64 at N=32), two complete custody replays and N explicit original
prediction extractions. Full-cohort case traversals are now **2N per existing
replay layer** (64 at N=32), rather than N squared. Existing layers can replay a
case multiple times; this is not a claim of only 64 total disk reads. In the
measured no-future fixture, original source replay calls fell from 3N²+N to 7N.
Semantic verification repeats the bounded work. No retries, parallel workers,
cryptographic-check reductions or persistent trust shortcuts are introduced.

The one-case path deliberately adds a second source boundary and can be slower.
Canonical assembly of full single-case fingerprints still serializes the common
custody context repeatedly; overall CPU serialization is not claimed to be linear.
The objective is removing redundant source I/O and replay, not a persistence redesign.

Output, including one common custody report and all case projections, must fit
256 KiB. Retained projections are checked incrementally and the final canonical
report is checked separately. Any existing single-case or output-limit error
fails the request; cases are never silently truncated. These are cooperative
bounds, not hard process/memory/time isolation.

## v1 compatibility and exact work diagnostic

Public `reconcile_cohort` and `verify_cohort` signatures, version, field set,
counts, ordering, signature checks, source pins and size limits are unchanged.
Only `work_bounds.maximum_full_cohort_case_traversals_per_replay_layer` changes
from N² to 2N. `single_case_invocations` counts logical pair assemblies, now through
the private helper rather than N full public single-case source replays.

This explicit engineering diagnostic change alters the document hash (except
where N² equals 2N). It does not change evidence identity or performance claims.
The verifier also accepts the exact original v1 representation with the legacy
N² bound, after full current original-owner reconstruction. Only that closed-form
field may differ: arbitrary bounds, flags, reordered cases, references or counts
are rejected. It does not assert that the old physical algorithm was re-executed.
The independent result/reference/report pins retain their meanings. Tests and
before/after runs compare all canonical content after adjusting this one diagnostic;
all single-case report pins remain identical. Existing retained v1 reports still
verify without running the quadratic algorithm.

## Measured before/after evidence

Baseline code was loaded from commit `338d6e928f0d3e10d839b6e391cef43d863a06fa`
(cohort and original single-case implementations). Each comparison used the same
in-memory owners, ephemeral real signatures, policy, expected IDs, anchor and
as_of for both algorithms. Fixtures had four original synthetic source observations
per prediction and no future observations. Setup, semantic verification and key
creation were excluded from reconciliation timings. Source/custody/crypto counters
wrapped real functions; no successful verification was mocked.

Environment: Python 3.12.14, macOS 26.7.1 x86_64, cryptography 50.0.2 in the local
benchmark interpreter. Results are one sequential before/after sample, with normal
filesystem caching and possible concurrent local tooling. They are not confidence
intervals, guarantees, cold-cache claims or predictions of larger-scale throughput.

| Cases | Before seconds | After seconds | Speedup | Source replay calls before/after | Full custody calls before/after | Ed25519 checks both | Bytes before/after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.101332 | 0.169256 | 0.60x | 4 / 7 | 1 / 2 | 2 | 7,022 / 7,022 |
| 3 | 0.744175 | 0.529583 | 1.41x | 30 / 21 | 3 / 2 | 6 | 15,098 / 15,098 |
| 8 | 4.850998 | 1.382295 | 3.51x | 200 / 56 | 8 / 2 | 16 | 35,290 / 35,290 |
| 32 | 69.058728 | 5.451403 | 12.67x | 3,104 / 224 | 32 / 2 | 64 | 132,220 / 132,218 |

Raw component timings for the 32-case comparison (nested cumulative work, **not
additive**) were: custody audit 68.290018 -> 4.587846 seconds; original-source
replay 52.837008 -> 4.106771 seconds; explicit selected artifact extraction
0.642305 -> 0.730428 seconds; Ed25519 0.008935 -> 0.008920 seconds.

A separate 8-case cProfile probe established the original bottleneck before the
implementation: 4.925264 total seconds, 4.713403 cumulative custody seconds,
200 source replay calls, and 16 crypto checks taking 0.001937 seconds. Global
canonical serialization calls fell from 49,408 (0.984101 cumulative seconds) to
13,936 (0.351900 cumulative seconds) in the after profile. These include nested
source/report serialization and must not be added to custody/source totals.
After-profile logical pair assembly took 0.020520 cumulative seconds; cohort
orchestration exclusive time was 0.000482 seconds, excluding called serialization
and validation. Fresh 8-case semantic reconstruction took 1.383504 seconds;
standalone canonical freezing of its fixed report averaged 0.000802 seconds over
100 repetitions. These measurements distinguish source, crypto, construction and
serialization rather than attributing all elapsed time to crypto or final JSON.

All four before/after documents matched canonically except the declared work
bound, and all actual legacy documents passed the optimized verifier. Smaller
mature-future cohorts and missing/invalid evidence remain covered by regression
tests. No 32-case mature-future latency, memory/RSS or trading-engine speedup is
claimed. The earlier #216 32-case functional probe was 64.968176 seconds and
132,220 bytes under its own local conditions; the paired table above is the
controlled comparison for this optimization.

## Unresolved trust

Every independent issuer, historical anchor validity, custody/clock authentication,
genuine forward, PIT, performance approval and execution flag stays false.
Metrics remain null. All PR #213 custody gaps remain in the common original
context. The underlying signed assertion is not RFC3161 or independent evidence
of historical key authority, clock accuracy, actual publication or creation time.

Further optimization of serialization or immutable-owner work would require
separate review and cannot bypass verification of new/restored documents. Independent
issuer/custody certification, real performance evaluation and trading remain
outside this PR.
