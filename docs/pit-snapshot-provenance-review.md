# PIT snapshot provenance review (non-authorizing)

This offline, read-only diagnostic binds a `pit_inference_input_v1` candidate
to a separately delivered `pit_source_evidence_v1` statement using the snapshot
identifier, input window hash, ticker, last trading date, feature version,
source cutoff, and **exact unchanged quality flags**. The evidence SHA-256 is
passed independently from the evidence document.

It reports separate blockers for unavailable revision history, unverified
corporate actions, unverified calendar, unresolved snapshot quality flags,
content mismatches, or incorrect evidence fingerprint.

**A matching SHA-256 fingerprint does not authenticate the evidence issuer.**
No source revision-history authority, signed evidence provenance, append-only
history, or current revocation validation exists here. The review therefore
always reports `provenance_authenticated=false` and
`inference_authorized=false`, even if all declared fields match. It does
not clear any Phase 1 quality flags, infer model scores, load models, or alter
storage. Existing HGB research-only restrictions remain unchanged.

Next: establish a genuinely trusted immutable source evidence ledger and
independent issuer signing/verification before considering any resolution of
PIT quality blockers or allowing inference.
