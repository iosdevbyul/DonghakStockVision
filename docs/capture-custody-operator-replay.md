# A-2: operator-controlled custody replay

Use `requeue_after_operator_review` only after an independently authorized
operator reviews a `manual_review` custody outbox item and establishes whether
the external destination already received its immutable receipt. Provide a
nonempty operator identifier and a recorded reason. The operation writes an
append-only **local** audit row and atomically resets the outbox item to
`pending` for explicit reprocessing. It refuses items in any other state.

Requeueing resets the per-cycle attempt counter, but the prior counter and
blocker remain in the audit trail. Operators must not replay indefinitely:
a deployed system should enforce external authorization, replay quotas and
an idempotency contract using the raw ID / digest with its independent
custodian. This code does not authenticate the claimed operator, query remote
status, automatically retry, prove durable remote acceptance, or authorize
inference. The local audit and outbox can be controlled by the same actor.

Phase A-2 remains in progress until independently controlled custody,
verifiable acknowledgement and the operational recovery guarantees are
actually integrated. Signature and key authentication belong to A-3.
