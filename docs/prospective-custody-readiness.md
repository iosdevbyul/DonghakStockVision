# Prospective custody and chronology readiness

## Read-only boundary

`storage.prospective_custody_readiness` reuses the original `EvaluationSources`
from the merged preparation consumer. It calls original-owner preparation and
binding replay, then emits `prospective_custody_readiness_v1`. It adds no evidence
registry, database, timestamp provider, public route, approval or custody service.

```python
from donghak_stock_vision.storage.prospective_custody_readiness import (
    audit_custody, verify_custody,
)

report = audit_custody(original_evaluation_sources, explicit_utc_as_of)
# Retain report.identifier and references_pin independently of mutable documents.
verify_custody(report, retained_report_pin, retained_references_pin,
               original_evaluation_sources, explicit_utc_as_of)
```

Original source pins and the policy/cohort/horizon owners remain authoritative.
No caller-supplied approval flag, Git author date, local path, self-computed hash
or receipt summary is an authentication input. Unsupported owner types are rejected
with `unsupported_evidence_owner_type`. Only the existing offline synthetic
preparation path is supported; genuine prospective mode remains blocked by its
original consumer restriction.

## Three different findings

1. **Local replay:** the original journal chain, frozen prediction identity,
   original request/source pins, cohort, horizon and future capture can be replayed.
   This is a content/chronology consistency check, not independent custody.
2. **Authenticated chronology:** independently verifiable evidence must bind exact
   original policy and prediction bytes to an externally established time before
   future outcomes. No such verifier or anchor is part of the current input path.
3. **Genuine prospective qualification:** requires additional independent source,
   timing, eligibility and governance evidence. Passing local replay is insufficient.

Every report remains `state=unresolved`, with independent chronology/custody,
genuine forward evidence, PIT qualification, performance approval and execution
authorization false. Even all-locally-replayable cases retain all custody gaps.
No accuracy, price return, baseline or performance metric is emitted.

## Stable reasons and coverage

Each case always includes:

- `missing_independent_policy_predeclaration_timestamp`
- `missing_independent_prediction_creation_time_evidence`
- `unauthenticated_local_timestamps`
- `missing_independently_retained_custody_references`
- `future_observation_provenance_not_independently_authenticated`

These reasons describe **missing supported, authenticated evidence in the supplied
original-owner workflow**. They do not assert that an external document cannot
exist elsewhere. A retained local source pin is not an external custody receipt.
This module intentionally accepts no arbitrary anchor that could suppress a gap.

`missing_or_unverifiable_future_observation_provenance` additionally marks pending,
missing, unavailable or invalid future evidence. `inconsistent_chronology` marks
specific original resolver clock/window violations, including a capture before
the frozen gate or ingestion after its deadline. Such a violation can mean a
valid retained observation is outside this prediction's allowed window; it does
not necessarily mean corruption. The exact original binding reason and state
are preserved for diagnosis.

Counts retain all supplied original predictions, locally verified future pairs,
pending/unavailable/invalid future cases and zero independently authenticated
cases. Reason counts are per-case, not independent evidence counts. Cases are
ordered by original prediction ID. They include original journal/request/policy
pins, local recording instant, target date, gate/deadline and verified future
capture/ingestion instants when available. Supplied future-request references are
identified separately and are not implicitly verified before the evaluation gate.

## Integrity, chronology and limits

The existing consumer enforces full declared membership, original policy/split
and horizon pins, the audit's 32-case limit, bounded requests and protected
partition checks. This report is bounded to 256 KiB. Original source replay is
sequential; there is no cache authenticating newly supplied documents. A repeated
binding must agree with the consumer result's state and verified outcome IDs/pins.

`verify_custody` checks a separately retained report pin and original preparation
reference pin, then reconstructs the entire report from original owners. A
rehashed forged timestamp, approval flag or source reference cannot replace that
reconstruction. Unknown report versions and oversized documents fail before
replay. Replacing all documents and all purported trusted pins does not establish
independent trust; retain original pins outside mutable evidence packages.

Malformed original policy, inconsistent policy-declaration chronology, invalid
journal/source identities and unsupported owner versions fail closed under the
existing validators. They do not produce invented recording instants. An invalid
future capture attached to a valid original prediction can be reported with its
original failure reason. Shared outcomes and unsupported cohort types retain
existing consumer/result restrictions; no contracts are weakened here.

A full audit necessarily reads original local files. Its guarantee is **no source
writes and no network/provider/broker I/O**, not zero file reads. Invalid owner
and oversized/unknown-report rejection can occur with no source I/O. Pending
resolution does not load future capture files. Cooperative byte/count limits are
not hard process isolation or durable crash recovery.

## Evidence required beyond this boundary

A future separately reviewed integration would need original external evidence
that can be independently verified offline, including:

- Exact policy/prediction content binding, explicit issuer/custodian identity and
  authenticated timestamp with independently retained trust roots.
- An assertion whose actual scope proves existence before the relevant outcome,
  not merely later retrieval, upload, ingestion or Git commit-author time.
- Original receipts, authenticated revision/correction lineage where applicable,
  issuer verification/revocation rules and independently retained readback.
- Outcome/source identity and availability evidence matched to the original
  frozen instrument, target, gate and deadline, plus unchanged PIT/OOS restrictions.

No external receipt is fabricated and no service is selected or contacted here.
Local replay, the readiness report and its content hash cannot certify genuine
prospective performance. Any future adapter must be separately specified and
must verify what the actual issuer and receipt scope can establish.
