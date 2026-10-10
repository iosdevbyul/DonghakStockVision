# Signed qualification registry issuer

`verify_qualification_issuer(registry_bytes, envelope_bytes, trusted_issuers)` verifies detached Ed25519 signatures over the **exact registry JSON bytes**, prefixed by the domain separator `DonghakStockVision:qualification-registry:v1\0`. The signature envelope includes an independently resolvable issuer key ID and SHA-256 digest, but **never** contains a public key. The caller supplies active/revoked issuer keys through a separately governed trust boundary.

The verifier rejects unknown/revoked/duplicate issuer key IDs, modified JSON bytes, digest mismatches, malformed registry documents and invalid signatures. A valid signature can establish authenticity relative to the independently supplied key; it does not establish issuer enrollment, deployment approval, or a model's scientific validity.

`registration_authorized` **always remains false**. The verifier is not connected to a model runner or publisher. Production still needs independent issuer identity governance, deployment authorization, qualification provenance, and revocation controls. No existing research-only HGB model is promoted or executed.
