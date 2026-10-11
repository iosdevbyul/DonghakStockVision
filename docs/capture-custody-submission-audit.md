# A-2: explicit remote custody submission audit

`submit_outbox_and_audit` sends an already queued exported receipt
through a caller-supplied `IdempotentCustodyTransport` and writes
an append-only local audit entry with the stable idempotency key,
outcome classification, blocker and timestamp.

The operation is **explicit** and separate from the existing outbox
processor. It rejects unknown queue entries and `manual_review`
entries; an operator must use the established audited replay procedure
for those. It never changes outbox state, consumes retry allowance,
authenticates an external acknowledgement or grants inference permission.

A transport exception or other blocked submission is still recorded
as an observation. The audit database is local: its append-only
triggers are not independently administered remote evidence.
A-2 is not complete until a separately controlled custodian and
verified acknowledgement/recovery integration are deployed.
