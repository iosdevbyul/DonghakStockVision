# Fail-closed operational execution gate

`inspect_execution_gate` composes the existing registration review, qualification
registry inspection, signed issuer verification, and scoped deployment approval
inspection into a single read-only result.

`evidence_consistent` reports whether these prerequisite diagnostics passed.
It is **not** permission to execute or publish stock predictions. Both
`registration_authorized` and `inference_authorized` are always false,
including when all four evidence inputs are structurally successful.

A production permission boundary still requires independently authenticated
operational qualification, authorized key governance and up-to-date revocation,
an audited permission service and approved model checkpoints. This change does
not connect a model loader, execute a PIT inference, write a registry, publish
stock signals, or promote any research-only HGB artifact.

Do not treat advisory evidence as an execution capability. Revoked or missing
evidence fails closed, and the absence of a permission service is always reported.
