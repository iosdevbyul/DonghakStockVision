# A-2: explicit custody lookup audit

`lookup_outbox_and_audit` queries a previously enqueued receipt through
the opt-in idempotent transport and writes an append-only local audit row.
It records the idempotency key, raw ID, unverified response classification,
first blocker, and observation time. No submission is performed.

The lookup **never** mutates the outbox state or resets retry counts. In
particular, neither a response nor the absence of a response establishes
authenticated acceptance or authenticated non-delivery. Events are local
and cannot authenticate an external custodian. Operator permission checks,
an independently managed custody provider and verifiable acknowledgements
are still necessary. A-2 remains in progress; no inference authorization.
