# A-2: stable submission identity for custody replay

`prepare_custody_submission_identity` derives a deterministic SHA-256
idempotency key from the raw ID and the digest of an existing export. It
refuses missing, modified, or otherwise unverifiable local receipts.

Callers can supply the resulting key to a future real custody transport
with a server-enforced idempotency contract. The current transport protocol
does **not** yet transmit the key. Local identity stability alone cannot
prevent duplicate remote delivery following a crash. A separately operated
custodian must enforce deduplication and offer independently verifiable
status/acknowledgements before A-2 can be marked complete.

No external service, authentication, signature, PIT authority, trading
permission, or inference authorization is introduced here.
