# Signed independent review verification

This module verifies **detached Ed25519 signatures** of the exact JSON review receipt bytes, prefixed by the fixed domain-separation string `DonghakStockVision:operational-model-review:v1\0`. The receipt commits to exact registration proposal bytes by SHA-256.

The signing key's public portion **must be provisioned independently** as a `TrustedReviewerKey` from a trusted, separately maintained local configuration. An attacker-controlled envelope contains only a key ID and signature, never a public key. Unknown, revoked or duplicated key IDs fail closed. Reviewer identity must match the independently provisioned key, and the reviewer cannot be the proposer. Modified receipt bytes fail signature verification.

The verifier does not create, rotate or distribute keys and never receives private signing keys. Establish external identity proofing, signer enrollment, revocation and secure signing procedures before production usage. The trust tuple is an *injected verification-only* interface, not yet a managed key registry or operational authorization service.

**Even a valid signature does not register a model.** `registration_authorized=false` remains fixed because no independently verified operational checkpoint, immutable experiment/qualification/provenance or operational registry exists. Existing HGB checkpoints stay research-only and unregistered. No fitting, inference, signal publication or trading is enabled. Tests use ephemeral generated keys only.
