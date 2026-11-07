# Original evidence and external receipt reconciliation

## Scope and cardinality

`storage.prospective_receipt_reconciliation` composes the existing consumer,
custody audit, original prediction replay and Ed25519 verifier without changing
any v1 contract. It produces `prospective_receipt_custody_reconciliation_v1`.
There is no new journal, registry, database, network client, timestamp authority,
public API route or trading operation.

Exactly **one explicitly selected prediction ID** and exactly two receipt owners
(one original evaluation policy and that prediction) are supported. The complete
original declared cohort, up to the existing 32-case audit bound, is still
replayed and retained as custody context. Other predictions' receipts are not
examined or implicitly authenticated. A tuple containing more than two receipt
owners, multiple selected IDs or missing/duplicate selection is rejected.
The unchanged two-role receipt verifier rejects duplicate receipt identities.

## Original identity and cryptographic matching

1. Verify the original full EvaluationSources through `audit_custody`: original
   policy pin, complete membership, cohort, partition, frozen horizon, original
   journal and source evidence remain authoritative.
2. Recover the selected `forward_prediction_evidence_v1` through
   `inspect_prediction`, not from a supplied summary or status flag.
3. Require byte-for-byte canonical FrozenJSON equality between the receipt owners'
   policy/prediction artifacts and the independently replayed originals. Pins must
   also match those original canonical artifacts. A correctly signed substitute
   cannot replace the original even if its own digest is internally consistent.
4. Call the unchanged `verify_receipts` for actual signature, exact signed artifact
   binding, configured issuer/key, pinned revocation snapshot and validity-window
   checks. No signature or source replay logic is reimplemented here.

The journal entry pin includes the journal envelope. It is **not** the digest of
the nested prediction evidence. Both are preserved, and the receipt must bind the
nested original prediction artifact's canonical digest and actual prediction ID.
A receipt owner with the right ID but different policy, parameters, horizon or
source content fails the original-owner equality check.

## Read-only Python API

```python
from donghak_stock_vision.storage.prospective_receipt_reconciliation import (
    reconcile_receipts, verify_reconciliation,
)

report = reconcile_receipts(
    original_evaluation_sources, selected_prediction_id,
    (original_policy_receipt_owner, original_prediction_receipt_owner),
    configured_public_anchor, retained_anchor_pin, explicit_utc_as_of,
)
verify_reconciliation(
    report, independently_retained_report_pin, retained_original_references_pin,
    original_evaluation_sources, selected_prediction_id,
    (original_policy_receipt_owner, original_prediction_receipt_owner),
    configured_public_anchor, retained_anchor_pin, explicit_utc_as_of,
)
```

All original policy/source/receipt pins and expected receipt IDs must be retained
separately from mutable documents. The original reference pin is the consumer's
original-source reference pin exposed by the custody report. It is not a receipt
pin or external custody proof. A missing receipt uses None in the existing
ReceiptOwner receipt/pin fields; missing trust uses None for anchor and anchor pin.

Verification reconstructs the entire report from original owners and checks the
retained report/reference pins. Rehashing forged findings, changed cohort counts,
source references or purported authentication cannot replace actual replay.
Replacing every supposedly trusted pin is not independent trust.

## Four distinct findings

- `original_prediction_policy_replay_completed` means the original prediction and
  policy/cohort context replay succeeded. `original_source_replay_completed` also
  requires a locally verified selected future observation. Pending, unavailable
  and invalid future cases remain visible in the complete custody report.
- `supplied_artifact_owners_match_originals` describes canonical owner equality.
  `selected_receipt_signatures_verified` and
  `selected_artifact_references_match_originals` require actual cryptographic
  verification against the explicitly configured public key.
- Issuer independence, clock/custody authentication and historical trust-anchor
  validity remain false. Current pinned key configuration and its claimed
  validity interval do not prove historical authorization or revocation freshness.
- Genuine forward evidence, PIT qualification, prospective performance approval
  and execution authorization remain false. Performance metrics are null.

The report always states that cohort-wide receipt verification was not performed,
with counts for the selected case and other cases whose receipts were not
examined. The denominator remains the complete declared original cohort. Even
when the cohort contains one case, this interface makes no batch-authentication
or genuine prospective claim.

## Chronology diagnostics and unresolved trust

Outcome timing is obtained only from the selected locally verified original
future capture. The caller cannot substitute an outcome instant in this API.
The existing verifier compares signed policy/prediction assertions and this
unauthenticated local capture context. After both signatures pass, reconciliation
also checks declared policy time <= signed policy time < original prediction
recording time <= signed prediction time, and signed prediction time before the
verified local future capture when present. All instants use existing UTC checks.

Contradictions produce invalid with stable reasons, without asserting which
clock is authentic. Signature validity remains a separate finding. Noncontradictory
results stay unresolved: the signed statement is not proof of issuer independence,
clock accuracy, actual artifact creation time or source publication availability.
Missing/unsupported receipts and missing anchors stay unresolved with original
verifier reasons. Invalid signatures, bindings and revoked-key failures are invalid.

The existing external verifier is an application-specific Ed25519 signed-assertion
format, not RFC3161, certificate-chain validation or a TSA. No local timestamp,
Git metadata or self-generated hash is promoted into independent predeclaration.
The original custody gaps remain unchanged in the embedded custody report.

## Bounds and safe limitations

Original source bounds and 32-case cohort limit remain enforced. Receipt inputs
retain 128 KiB artifact and 8 KiB receipt/anchor limits, fixed key/signature lengths
and two signatures. Reconciliation output is limited to 256 KiB. These are
cooperative limits, not process isolation. Unsupported batch requests fail before
source replay; a full valid reconciliation requires original local file reads,
but writes no sources and makes no network/provider/TSA/broker calls.

An invalid original prediction or incompatible shared-outcome cohort remains a
whole-request failure under the original consumer/result contracts; no recording
time or per-pair outcome identity is fabricated to bypass them. A future batch
adapter and independently authenticated issuer/clock/custody trust review require
separate specifications. This module supplies neither that authority nor empirical
performance evaluation or live-market activation.
