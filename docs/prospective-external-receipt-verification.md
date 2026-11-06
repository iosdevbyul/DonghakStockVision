# Offline external receipt verification foundation

## Supported guarantees

`storage.prospective_external_receipt` verifies a bounded, application-specific
Ed25519 signed assertion over canonical artifact references. It reuses FrozenJSON,
SHA pin, strict-field and UTC utilities and the existing optional cryptography
library, following the repository's pinned-key signature boundary.

**This is not RFC 3161, a TSA token verifier, certificate-chain validation or an
independent timestamp service.** ASN.1/CMS, JWT/JWS, arbitrary certificates,
Ed25519ph and other algorithms/formats are unsupported. No new dependency,
network lookup, trust discovery, submission, paid service, registry or store is
introduced. Signing/private-key handling exists only in ephemeral test fixtures;
production code only verifies public material.

The cryptographic primitive uses the existing
[cryptography Ed25519 verifier](https://cryptography.io/en/44.0.3/hazmat/primitives/asymmetric/ed25519/).
Tests exercise real signatures plus the public empty-message test vector in
[RFC 8032 section 7.1](https://www.rfc-editor.org/rfc/rfc8032.html#section-7.1).
The RFC is a primitive test source, not a timestamp-format compliance claim.

## Exact wire formats

The receipt is FrozenJSON with exactly:

- `version`: `prospective_ed25519_receipt_v1`.
- `algorithm`: `Ed25519`.
- `payload`: exactly `receipt_id`, `issuer`, `key_id`, `artifact_role`,
  `artifact_id`, `artifact_digest`, `artifact_encoding`, `issued_at`.
- `signature_base64`: canonical Base64 encoding of a 64-byte Ed25519 signature.

The signed message is the exported `DOMAIN` bytes concatenated with
`FrozenJSON.freeze(payload).payload_json.encode()` (UTF-8). This is ordinary
Ed25519 signing of a domain-separated message, not Ed25519ctx or Ed25519ph.
The version/algorithm are fixed by that domain and strict envelope validation.
No arbitrary approval or extra fields are accepted.

`artifact_role` is policy or prediction. The artifact's original canonical
FrozenJSON identifier must equal both artifact identity and digest, with encoding
`dsv_canonical_json_sha256_v1`. This binds canonical JSON, not the original file's
whitespace/raw byte sequence. Other digest encodings are rejected. Original
artifact versions are `prospective_evaluation_policy_v1` and
`forward_prediction_evidence_v1` respectively. Those version checks do not replay
the artifact's internal evidence or prove policy-to-prediction cohort membership.
The original journal/source verifiers remain authoritative outside this module.

IDs/pins are lowercase 64-character hexadecimal strings. Issuer text is limited
to 128 characters. `issued_at` is canonical UTC with `+00:00`, not a naive date.

A separately configured and pinned public trust anchor contains exactly:

- `version`: `prospective_receipt_trust_anchor_v1`.
- `issuer`, `key_id`, `public_key_base64` (canonical 32-byte raw public key).
- Canonical UTC `valid_from`, `valid_until`, with inclusive issuance bounds.
- `status`: must be active for successful verification.
- `revoked_receipt_ids`: at most 32 unique receipt IDs.

Receipt issuer/key must exactly match this explicitly supplied configuration.
The signed issuance assertion must fall inside the configured validity window
and not after `as_of`. Revoked/inactive keys and receipts listed in the pinned
revocation snapshot are rejected. No online freshness, historical revocation,
key custody or independent issuer status is inferred. Expiry is checked against
the signed issuance assertion, not a claim of current online authorization.
Trust-anchor pins must be retained separately, never read solely from receipts.

## Offline API

```python
from donghak_stock_vision.storage.prospective_external_receipt import (
    ReceiptOwner, verify_receipts, verify_report,
)

owners = (
    ReceiptOwner('policy', original_policy, retained_policy_pin,
                 expected_policy_receipt_id, policy_receipt, retained_policy_receipt_pin),
    ReceiptOwner('prediction', original_prediction, retained_prediction_pin,
                 expected_prediction_receipt_id, prediction_receipt,
                 retained_prediction_receipt_pin),
)
report = verify_receipts(owners, configured_anchor, retained_anchor_pin,
                        explicit_utc_as_of, outcome_observed_at=original_outcome_context)
verify_report(report, separately_retained_report_pin, owners,
              configured_anchor, retained_anchor_pin, explicit_utc_as_of,
              outcome_observed_at=original_outcome_context)
```

Use None for a missing receipt and its pin, or for an unavailable anchor/pin.
The API accepts no private key and opens no files or databases. The existing
`api` optional dependency supplies cryptography; when unavailable, verification
fails closed with `existing_crypto_extra_required`. No dependency is installed
implicitly. The expected receipt IDs, artifact pins and receipt pins must come
from retained original owners, not be inferred solely from a received envelope.

Exactly two distinct roles/receipt identities/artifacts are required. The bounds
are 128 KiB per canonical artifact, 8 KiB per receipt/anchor, 32 KiB per report,
one 32-byte key and at most two signature verifications. There is no unbounded
certificate parsing, batch scheduler or retry. Callers must bound raw inputs
before constructing FrozenJSON; these are cooperative, not process isolation.

## Report meaning and chronology

`prospective_external_receipt_verification_v1` exposes per-receipt structural
validity separately from signature verification. Row states include
`verified_cryptographic_structure`, `missing_trust_anchor`, `unsupported_receipt`,
`invalid_signature`, `artifact_mismatch` and `invalid`, with fixed failure reasons.
Missing receipts remain explicit. Structurally valid does not mean the artifact
binding or signature is valid. Claimed issuance is emitted as `signed_issuer_time`
only after actual signature verification; before that it is never an authenticated
time input.

When both signatures verify, their signed assertions must put policy before
prediction; a supplied outcome context must follow prediction and not exceed
`as_of`. Contradiction produces aggregate invalid. Otherwise aggregate state stays
`chronology_unresolved`: a signature binds an issuer's time assertion but does not
prove the issuer's clock, independence or actual existence time. The outcome time
is an unauthenticated context, never promoted to an authenticated timestamp.

Even valid signatures from a real external key do not establish original-source
replay, predeclaration, policy/cohort membership, independent issuer evidence or
genuine prospective eligibility. All those independent chronology/source flags,
genuine forward evidence, PIT qualification, performance approval and execution
authorization remain false. No existing readiness or evaluation contract is
unlocked. Synthetic test keys can prove verifier mechanics only.

Semantic report verification re-verifies original bytes, receipt IDs, configured
anchor and separately retained pins; a self-rehashed altered report is rejected.
If every document and supposedly trusted pin is replaced, no independent trust
can be inferred. Future work requires independently reviewed issuer/key custody,
clock assurance, original receipt/readback and revision lineage, offline trust and
revocation freshness rules, and original journal/cohort/source replay. This PR
implements none of those authorities and performs no live-market operations.
