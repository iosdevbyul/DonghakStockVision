# A-2: deployment readiness checklist and trust boundary

`assess_custody_deployment_readiness` reports missing deployment controls
from explicitly provided operator assertions. The default report requires
all five controls:

1. Explicitly approved HTTPS endpoint
2. Independently administered external custodian
3. Authenticated acknowledgement verifier
4. Durable remote retrieval integration test
5. Idempotent remote replay integration test

The report is intentionally **not** an acceptance or security certification.
Even when every operator assertion is true, the report always sets
`verified_independent_custody=False` and `inference_authorized=False`.
Assertions cannot substitute for independent evidence, provider key
verification, authenticated receipts or recovery tests. It performs no
network calls and stores no credentials.

This checklist helps track the remaining A-2 deployment requirements.
Actual provider selection, deployment and verification are still necessary.
