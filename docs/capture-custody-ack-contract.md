# A-2: external custody acknowledgement review contract

This increment defines a **read-only verification contract** for an externally
supplied acknowledgement and the exact bytes of a previously exported local
capture receipt. It checks a separately supplied acknowledgement digest,
explicit raw ID/custodian identity, and the digest of the receipt bytes.

A caller-generated digest of the same acknowledgement is **not** an independent
pin. Matching contents cannot prove which custodian issued the acknowledgement,
whether the claimed acceptance time is authentic, whether the remote store is
independently administered, or whether records are tamper-resistant. The review
therefore always returns `custodian_authenticated=false`,
`independent_custody_verified=false`, and `inference_authorized=false`.

No external receiver, remote delivery acknowledgement persistence, scheduled
job, network call, credentials, or cryptographic verification is added here.
Phase A-2 remains incomplete until a separately administered evidence service
and durable acknowledgement / recovery workflow are integrated. Independent
signature/key verification is reserved for A-3.
