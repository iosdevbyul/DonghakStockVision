# Pinned reviewer public-key registry

The optional verification-only `load_pinned_reviewer_keys(path, trusted_sha256=...)` reads a local registry whose **exact byte hash must be pinned independently from the registry and review receipt**. The strict `trusted_reviewer_keys_v1` document contains unique key IDs, named reviewers, base64-encoded 32-byte Ed25519 public keys and explicit `active` or `revoked` status.

Revoked keys are retained but rejected by the detached signature verifier. Duplicate IDs, invalid base64, malformed payloads and unexpected file changes fail closed. Editing the registry requires a new independently verified pin. A hash alone does not authenticate the registry's origin; whoever provides the pin must be a separate trusted authority.

This read-only adapter **does not** provision a managed key service, confirm a reviewer's legal identity, support online revocation, distribute signing keys, sign any receipt, or authorize operational model registration. `registration_authorized` remains false. Key lifecycle changes must be reviewed and deployed through a separate controlled configuration process. No existing research-only HGB model is promoted.
