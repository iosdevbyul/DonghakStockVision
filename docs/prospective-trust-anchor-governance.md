# Offline external trust-anchor governance review

## Scope and original contract

`storage.prospective_anchor_governance` reviews one original configured public
anchor and bounded optional retained snapshot/document references. The output is
`prospective_trust_anchor_governance_v1`. It is independent of cohort receipt
reconciliation and introduces no registry, authority, persistent store, signature
format or authentication adapter. Existing receipt verification semantics are
unchanged.

The anchor owner uses exactly the existing
`prospective_receipt_trust_anchor_v1` fields: version, issuer, key_id,
public_key_base64, valid_from, valid_until, status and revoked_receipt_ids.
Canonical public-key encoding/length checks reuse the receipt verifier's helper;
strict fields, SHA pins, UTC and canonical JSON use existing utilities.

This format implicitly supports raw Ed25519 public keys (32 bytes), not arbitrary
certificates or configurable algorithms. Extra algorithm/approval fields are
rejected. Encoding validity does not prove key ownership, cryptographic usability,
issuer legitimacy or a valid signature. The displayed public-key digest is only
a mechanical fingerprint: the existing opaque key_id is not reinterpreted as a
public-key digest.

## API and bounded reference carriers

```python
from donghak_stock_vision.storage.prospective_anchor_governance import (
    AnchorSnapshot, GovernanceReference, review_anchor, verify_governance,
)

report = review_anchor(
    original_anchor, retained_anchor_pin, expected_issuer, expected_key_id,
    explicit_utc_as_of,
    history=retained_anchor_snapshots,
    references=original_document_references,
)
verify_governance(
    report, separately_retained_report_pin, original_anchor, retained_anchor_pin,
    expected_issuer, expected_key_id, explicit_utc_as_of,
    history=retained_anchor_snapshots,
    references=original_document_references,
)
```

`AnchorSnapshot(owner, pin)` carries an original local anchor document and its
separately retained pin. It does **not** prove historical capture time.
`GovernanceReference(category, issuer, key_id, document, pin)` carries original
FrozenJSON document bytes and a claimed association to this issuer and a known
current/snapshot key. Categories are issuer_identity, key_custody, clock_assurance,
key_rotation, retirement and historical_revocation. Unknown categories or wrong
issuer/key associations are rejected.

The supplied document's pin is checked, but **no external document format has an
independent authenticity verifier here**. Documents are not interpreted as
attestations. A signed-looking or self-attested JSON is reported as
`unsupported_evidence`, with `independent_document_authentication_not_supported`.
The category/issuer/key association is an input reference association, not proof
of the document's contents or issuer. Supplied-reference counts distinguish
absence from supplied-but-unverified material; neither establishes authentication.

No document is fetched automatically and no new signature format is accepted.
These immutable in-memory carriers are not a second evidence registry. Only
original canonical JSON documents are accepted; PDF, X.509, OCSP, CRL or timestamp
formats would require separately reviewed adapters, not arbitrary wrapper flags.

## Six different findings

The report separates:

1. Structural validity of the exact anchor wire format.
2. Consistency of issuer/key references and configured public bytes per key ID.
3. Local snapshot/document availability, including declared revocation IDs.
4. Independent issuer identity and key-custody authentication: always false.
5. Historical signing-authority/revocation authentication: always false.
6. Independent clock assurance: always false.

`state` remains unresolved even if every local configuration check succeeds.
The original custody/issuer/rotation/retirement/clock gaps are never removed by a
public key, local file/hash/time, Git metadata or trusted/independent/clock_verified
claim. Genuine forward evidence, PIT, prospective performance approval and
execution authorization stay false; performance metrics remain null.

## Validity, lifecycle and revocation rules

Declared validity instants must be canonical UTC; start <= end. Inclusive boundary
comparisons classify the requested review time as not_yet_valid,
within_declared_window or expired. The governance report recognizes active,
revoked and inactive declarations. Other statuses, including undocumented
retirement labels, are rejected. A revoked/inactive anchor can be structurally
reviewed but is not locally active; this does not relax the receipt verifier,
which still requires an active configured key.

`locally_active_at_review_time` means only active configuration within its declared
window. The existing receipt verifier instead checks the signed issuance assertion
against its configured window. This review is not an authorization gate and does
not redefine historical receipt verification after a window expires.

Same issuer/key ID with different configured public bytes is rejected as an
ambiguous identity. Wrong issuer, duplicate snapshot pins, malformed/duplicate
revoked receipt IDs and reversed intervals are rejected. A distinct key ID may
appear in retained snapshots, but it is only a local rotation hint; no rotation
lineage, issuer control or retirement time is authenticated.

Snapshots have no independently verified observation order. Changed status or
revoked-receipt lists therefore add unresolved reasons rather than fabricated
monotonic lifecycle rules. An older-looking revoked snapshot plus an active one
cannot prove reinstatement or determine which declaration is authentic. Revoked
IDs are sorted for presentation, not timestamped. Missing historical material is
explicitly reported; supplied snapshots do not establish completeness, freshness,
issuance-time trust or historical revocation authority.

## Integrity, determinism and limits

Anchor pin and expected issuer/key identity must match exactly. Every original
snapshot/reference pin is independently supplied and checked. Report verification
reconstructs from all original owners and compares against a separately retained
report pin; rehashed altered findings do not become trusted. Replacing every
original and every supposedly trusted pin cannot establish independent trust.
No mutable verification cache is introduced.

At most eight historical snapshots and eight document references are allowed,
with 8 KiB per owner, 128 KiB total supplied owner bytes, and 32 KiB output.
Duplicate snapshot/document references are rejected rather than double counted.
Ordering is canonical by pin/category, independent of supplied tuple order.
FrozenJSON provides detached deterministic serialization and duplicate-key
rejection. Callers must bound raw documents before constructing owners; these
are cooperative limits, not hard process isolation.

The module performs no file/database writes **or reads**, no network/provider,
broker, TSA or paid-service calls and no private-key operations. It processes
only supplied immutable public configuration and reference owners.

## Requirements beyond this boundary

A future supported adapter would need independently retained original issuer and
key-custody evidence, explicit trusted roots and their authentic provenance,
verifiable rotation/retirement assertions and historical authority intervals,
authenticated revocation snapshots with capture/freshness rules, and clock
assurance whose issuer/scope can actually prove the claimed time. Such an adapter
must bind exact original content, define revocation/expiry semantics and preserve
original evidence replay and research controls. This PR implements no unsupported
attestation and certifies no genuine prospective market performance.
