# Bounded multi-prediction receipt reconciliation

## Scope

`storage.prospective_receipt_cohort` composes the unchanged single-case
reconciliation for every original declared prediction, producing
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
owner. Each completed single-case custody/source reference pin must match the
first one: source change between repeated traversals fails closed. Only one copy
of the common full custody context is retained in the final report. Per-case
projections retain the single-report pin, findings, original artifact identity,
receipt diagnostics and local future state/reason.

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

For N cases there are exactly N single-case invocations, no more than 2N Ed25519
checks (64 at N=32), and N squared full-cohort case traversals **per existing replay
layer** (1,024 at N=32). Existing layers may replay each case multiple times; this
is not a claim of only 1,024 total disk reads. Missing receipt owners add at most
N selected original-prediction inspections. Semantic verification repeats the
bounded work. No retries, parallel workers or hidden trust shortcuts are added.

Output, including one common custody report and all case projections, must fit
256 KiB. Retained projections are checked incrementally and the final canonical
report is checked separately. Any existing single-case or output-limit error
fails the request; cases are never silently truncated. These are cooperative
bounds, not hard process/memory/time isolation.

## Measured bounded local probe

The final local test used 32 distinct original synthetic predictions (four source
observations each), one shared policy receipt and 32 real ephemeral Ed25519
prediction receipts. Future observations were absent, so this is not a benchmark
of 32 mature/verified future captures. Reconciliation alone, excluding fixture
setup, took 64.968176 seconds and produced 132,220 canonical bytes. All 32 pairs
had valid signatures and all 32 unavailable observations remained in the context.
The controlled one-/three-case tests also exercised verified future observations.

This one machine-specific functional measurement is not a latency guarantee or
proof of larger-scale throughput. No memory/RSS measurement is claimed. The normal
32-case test performs real source and signature validation; installed-wheel focused
checks exercise the smaller cases without repeating this costly probe.

## Unresolved trust

Every independent issuer, historical anchor validity, custody/clock authentication,
genuine forward, PIT, performance approval and execution flag stays false.
Metrics remain null. All PR #213 custody gaps remain in the common original
context. The underlying signed assertion is not RFC3161 or independent evidence
of historical key authority, clock accuracy, actual publication or creation time.

A reviewed future batch optimization could reduce repeated immutable-owner work,
but cannot bypass independent verification of new/restored documents. Independent
issuer/custody certification, real performance evaluation and trading remain
outside this PR.
