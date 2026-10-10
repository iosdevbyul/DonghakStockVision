# Signed, scoped operational deployment approval contract

`inspect_deployment_approval` independently verifies a detached Ed25519 approval over the exact receipt bytes using the domain separator `DonghakStockVision:deployment-approval:v1\0`. The receipt binds exact proposal and qualification registry byte hashes, model ID/version, checkpoint SHA, deployment environment, PIT research-only purpose, distinct approver, explicit decision and an expiry.

Trusted approver public keys are provided from a separate controlled authority, never from the receipt. Unknown, revoked or duplicate key IDs, invalid signatures, modified bytes, proposal/registry mismatch, self-approval, expired approval and environment mismatches are rejected.

**This is not a deployment capability or authorization.** Neither a valid signature nor a structurally consistent model/qualification registry grants operational status. Both `registration_authorized` and `inference_authorized` are always false. The model runner and publication paths remain untouched. A separate audited permission service that considers all independently authenticated evidence, issuer governance, revocation policy, and deployment risk approval is required before any execution. Existing HGB checkpoints remain research-only.
