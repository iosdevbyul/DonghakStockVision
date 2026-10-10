# A-2: explicit local custody outbox

`enqueue_capture_receipt` persists a queue item only for an existing
capture receipt. It is idempotent and does not reset previous attempts.

`process_outbox` is operator-invoked, bounded, and persists each attempt
result. Transport exceptions and invalid transport return types are retried
up to the configured maximum. Invalid acknowledgement contracts, digest
conflicts, missing exports, and superficially matching but **unauthenticated**
acknowledgements are quarantined for manual review. There is intentionally
no `accepted` state. Restarting a process reuses the persisted count.

This is a local delivery-attempt workflow, **not** independent custody
acceptance. Remote transports and independent pins must be supplied by
operators; a caller-generated pin does not authenticate a custodian. SQLite
updates and remote submission cannot be atomic: a crash between submission
and recording may cause duplicate delivery on retry. A production custodian
must support idempotent raw IDs / receipt digests and independent status
queries before operator recovery can safely assert acceptance. No automatic
scheduler, backoff clock, credential, remote storage, authority promotion or
model inference permission is provided. A-2 remains in progress.
